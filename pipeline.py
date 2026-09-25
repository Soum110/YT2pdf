"""
pipeline.py — Full URL-to-PDF orchestration pipeline.

Handles:
  1. Fetching video metadata with yt-dlp
  2. Downloading video to a temp file
  3. Running the two-pass CV + AI extractor
  4. Building the PDF
  5. Updating status.json throughout
"""

# Ensure THIS project's directory is first on sys.path so our modules
# (slide_extractor, pdf_builder) are found before any installed packages
# that might have conflicting module names (e.g. yt-dlp extractor plugins).
import sys, os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("pipeline")


# ─────────────────────────────────────────────
# Status helpers
# ─────────────────────────────────────────────

def _write_status(job_dir: Path, stage: str, progress: int, message: str,
                  slide_count: int = 0, error: str = ""):
    status = {
        "stage": stage,
        "progress": progress,
        "message": message,
        "slide_count": slide_count,
        "error": error,
        "updated_at": time.time(),
    }
    (job_dir / "status.json").write_text(json.dumps(status))


# ─────────────────────────────────────────────
# yt-dlp helpers
# ─────────────────────────────────────────────

class _YtdlpLogger:
    """
    Routes all yt-dlp output through Python's logging system.
    This completely avoids writing to stdout/stderr, which causes
    [Errno 32] Broken pipe when running inside background threads.
    Also stores recent error messages so we can surface exact error reasons.
    """
    def __init__(self):
        self.errors = []
        self.warnings = []

    def debug(self, msg):
        if str(msg).startswith("[debug]"):
            log.debug("yt-dlp: %s", msg)
        else:
            log.info("yt-dlp: %s", msg)

    def info(self, msg):
        log.info("yt-dlp: %s", msg)

    def warning(self, msg):
        self.warnings.append(str(msg))
        log.warning("yt-dlp: %s", msg)

    def error(self, msg):
        self.errors.append(str(msg))
        log.error("yt-dlp: %s", msg)


def _normalize_netscape_cookies(text: str) -> str:
    """
    Cleans up cookie strings pasted into web UI / environment variables.
    Handles:
      - Enclosing quotes
      - Escaped newlines (\\n) and tabs (\\t)
      - Space-delimited rows converted to true tab-delimited Netscape format
      - Base64-encoded cookie text
    """
    text = text.strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1]
    text = text.replace("\\n", "\n").replace("\\t", "\t")

    import base64
    if not text.startswith("#") and len(text) > 50:
        try:
            decoded = base64.b64decode(text).decode("utf-8", errors="ignore")
            if "# Netscape" in decoded or ".youtube.com" in decoded:
                text = decoded
        except Exception:
            pass

    lines = ["# Netscape HTTP Cookie File"]
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "Netscape" not in line:
                lines.append(line)
            continue
        if "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 6:
                lines.append(line)
                continue
        # Split by consecutive whitespace into up to 7 parts
        parts = line.split(None, 6)
        if len(parts) == 7:
            lines.append("\t".join(parts))
        elif len(parts) == 6:
            lines.append("\t".join(parts) + "\t")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def _extract_video_id(url: str) -> Optional[str]:
    """Extract 11-char YouTube video ID from various URL formats."""
    import re
    m = re.search(r"(?:v=|\/embed\/|\/shorts\/|\/v\/|youtu\.be\/|\/watch\?.*v=)([0-9A-Za-z_-]{11})", url)
    return m.group(1) if m else None


def _get_po_token(video_id: Optional[str] = None) -> Optional[str]:
    """
    Fetch or generate a genuine YouTube Proof-of-Origin (PO) Token.
    Tries:
      1. Local bgutil-pot HTTP server on 127.0.0.1:4416 (if /ping responds)
      2. Direct bgutil-pot CLI binary invocation
    """
    # 1. Try local bgutil HTTP server if running
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:4416/ping", timeout=0.3) as r:
            if r.status == 200:
                payload = {}
                if video_id:
                    payload["contentBinding"] = video_id
                req = urllib.request.Request(
                    "http://127.0.0.1:4416/get_pot",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        tok = data.get("poToken") or data.get("po_token")
                        if tok:
                            log.info("Obtained PO token from local bgutil-pot server for video %s", video_id)
                            return tok
    except Exception:
        pass

    # 2. Try CLI binary directly
    bgutil_bin = shutil.which("bgutil-pot") or "/usr/local/bin/bgutil-pot"
    if os.path.exists(bgutil_bin) or shutil.which("bgutil-pot"):
        flag_candidates = [["--content-binding", video_id], ["-c", video_id]] if video_id else [[]]
        for flags in flag_candidates:
            try:
                cmd = [bgutil_bin] + flags
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if res.returncode == 0 and res.stdout.strip():
                    for line in reversed(res.stdout.strip().splitlines()):
                        line = line.strip()
                        if line.startswith("{") and line.endswith("}"):
                            data = json.loads(line)
                            tok = data.get("poToken") or data.get("po_token")
                            if tok:
                                log.info("Generated PO token via bgutil-pot CLI for video %s", video_id)
                                return tok
            except Exception as e:
                log.debug("CLI bgutil-pot attempt with %s failed: %s", flags, e)

    return None


def _fetch_oembed(video_id: str) -> Optional[dict]:
    """Instant oEmbed lookup via YouTube's unauthenticated public API (50ms)."""
    if not video_id:
        return None
    try:
        url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "title": data.get("title", "YouTube Lecture"),
                    "uploader": data.get("author_name", ""),
                    "thumbnail": data.get("thumbnail_url", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"),
                    "duration": 0
                }
    except Exception as e:
        log.debug("oEmbed fetch failed for video %s: %s", video_id, e)
    return None


def _build_ydl_opts_base(extra: dict = None, video_id: Optional[str] = None) -> list[dict]:
    """
    Returns a list of yt-dlp option dicts to try in order.
    Uses a custom logger (never writes to stdout/stderr) to prevent
    Broken pipe errors in background threads.
    """
    base = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "socket_timeout": 8,
        "retries": 1,
        "fragment_retries": 1,
        "extractor_retries": 1,
        "source_address": "0.0.0.0",
    }

    # Support YouTube cookies to bypass cloud data-center anti-bot blocks
    # 1. Top priority: exact cookies.txt in repository root
    repo_cookie = Path(__file__).parent / "cookies.txt"
    cookie_file = os.environ.get("YOUTUBE_COOKIE_FILE", "").strip()
    cookie_content = os.environ.get("YOUTUBE_COOKIES", "").strip()
    has_valid_cookies = False

    if repo_cookie.exists() and repo_cookie.stat().st_size > 100:
        base["cookiefile"] = str(repo_cookie)
        has_valid_cookies = True
        log.info("Using repository cookies.txt: %s (%d bytes)", repo_cookie, repo_cookie.stat().st_size)
    elif os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 100:
        base["cookiefile"] = "cookies.txt"
        has_valid_cookies = True
        log.info("Using local cookies.txt")
    elif cookie_file and os.path.exists(cookie_file):
        base["cookiefile"] = cookie_file
        has_valid_cookies = True
    elif cookie_content:
        clean_content = _normalize_netscape_cookies(cookie_content)
        cpath = Path(tempfile.gettempdir()) / "yt_cookies.txt"
        cpath.write_text(clean_content, encoding="utf-8")
        base["cookiefile"] = str(cpath)
        has_valid_cookies = True

    if extra:
        base.update(extra)

    variants = []

    # 1. Top priority: Authenticated cookies variants (if valid cookies exist)
    if has_valid_cookies and "cookiefile" in base:
        v_c_web = dict(base)
        v_c_web["extractor_args"] = {"youtube": {"player_client": ["web"]}}
        v_c_web["logger"] = _YtdlpLogger()
        variants.append(v_c_web)

        v_c_vr = dict(base)
        v_c_vr["extractor_args"] = {"youtube": {"player_client": ["android_vr"]}}
        v_c_vr["logger"] = _YtdlpLogger()
        variants.append(v_c_vr)

        v_c_mweb = dict(base)
        v_c_mweb["extractor_args"] = {"youtube": {"player_client": ["mweb"]}}
        v_c_mweb["logger"] = _YtdlpLogger()
        variants.append(v_c_mweb)

    base_no_cookies = dict(base)
    base_no_cookies.pop("cookiefile", None)

    # 2. Web client with BotGuard PO Token if available
    po_token = _get_po_token(video_id)
    if po_token:
        v_po = dict(base_no_cookies)
        v_po["extractor_args"] = {
            "youtube": {
                "player_client": ["web", "default"],
                "po_token": [f"web.gvs+{po_token}", f"web.player+{po_token}"],
            }
        }
        v_po["logger"] = _YtdlpLogger()
        variants.append(v_po)

    # 3. android_vr client without cookies
    v_vr = dict(base_no_cookies)
    v_vr["extractor_args"] = {"youtube": {"player_client": ["android_vr"]}}
    v_vr["logger"] = _YtdlpLogger()
    variants.append(v_vr)

    # 4. Android client without cookies
    va = dict(base_no_cookies)
    va["extractor_args"] = {"youtube": {"player_client": ["android"]}}
    va["logger"] = _YtdlpLogger()
    variants.append(va)

    # 5. Fallback without cookies (mweb / default)
    vm = dict(base_no_cookies)
    vm["extractor_args"] = {"youtube": {"player_client": ["mweb"]}}
    vm["logger"] = _YtdlpLogger()
    variants.append(vm)

    vd = dict(base_no_cookies)
    vd["logger"] = _YtdlpLogger()
    variants.append(vd)

    return variants


def _get_video_info(url: str) -> dict:
    """Fetch video title and duration with instant oEmbed lookup and fail-fast yt-dlp timeout."""
    import yt_dlp
    import concurrent.futures

    video_id = _extract_video_id(url)
    oembed_info = _fetch_oembed(video_id) if video_id else None

    all_errors = []
    # Try up to 2 best variants for info (capped to prevent stalls)
    variants = _build_ydl_opts_base({"skip_download": True}, video_id=video_id)
    for opts in variants[:2]:
        client = opts.get("extractor_args", {}).get("youtube", {}).get("player_client", ["default"])[0]
        logger = opts.get("logger")
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            def _extract():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=False)

            fut = ex.submit(_extract)
            info = fut.result(timeout=5)

            return {
                "title": info.get("title") or (oembed_info.get("title") if oembed_info else "Untitled Video"),
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader") or (oembed_info.get("uploader") if oembed_info else ""),
                "thumbnail": info.get("thumbnail") or (oembed_info.get("thumbnail") if oembed_info else ""),
            }
        except Exception as e:
            err_msg = str(e).strip()
            if not err_msg and logger and logger.errors:
                err_msg = logger.errors[-1].strip()
            all_errors.append(f"{client}: {err_msg}")
            log.warning("  yt-dlp info attempt failed (client=%s): %s", client, err_msg)
            # If oEmbed metadata is available, don't stall further
            if oembed_info:
                break
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    # If yt-dlp timed out or failed but oEmbed succeeded, use oEmbed metadata immediately
    if oembed_info:
        log.info("Using instant oEmbed metadata fallback for %s: %s", video_id, oembed_info.get("title"))
        return oembed_info

    is_bot_block = any("bot" in str(e).lower() or "sign in" in str(e).lower() for e in all_errors)
    if is_bot_block:
        raise RuntimeError(
            "YouTube blocked datacenter server access for this video. "
            "Please use the YT2PDF Slide Companion browser extension to capture slides directly with zero bot blocks."
        )

    raise RuntimeError(f"Could not fetch video info from YouTube: {'; '.join(all_errors)}")


def _download_video(
    url: str,
    output_path: str,
    progress_hook: Optional[Callable] = None,
    duration: Optional[float] = None,
) -> str:
    """Download video to output_path using yt-dlp with fail-fast timeouts."""
    import yt_dlp
    import concurrent.futures

    video_id = _extract_video_id(url)
    hooks = [progress_hook] if progress_hook else []

    # For multi-hour lectures (> 2 hrs), use 480p to conserve memory & disk bandwidth
    # For standard lectures (<= 2 hrs), retain high quality 720p
    if duration and duration > 7200:
        format_str = "bestvideo[height<=480]/best[height<=480]/bestvideo[height<=720]/best[height<=720]/best"
    else:
        format_str = "bestvideo[height<=720]/best[height<=720]/bestvideo/best"

    # Scale download timeout gracefully with lecture duration (up to 30 mins)
    dl_timeout = max(300, min(1800, int(duration * 0.4))) if (duration and duration > 0) else 300

    last_err = None
    variants = _build_ydl_opts_base({
        "format": format_str,
        "outtmpl": output_path,
        "progress_hooks": hooks,
        "merge_output_format": "mp4",
        "postprocessors": [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }] if shutil.which("ffmpeg") else [],
    }, video_id=video_id)

    for opts in variants:
        client = opts.get("extractor_args", {}).get("youtube", {}).get("player_client", ["default"])[0]
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            def _do_dl():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])

            fut = ex.submit(_do_dl)
            fut.result(timeout=dl_timeout)

            candidates = list(Path(output_path).parent.glob(Path(output_path).stem + "*"))
            mp4s = [c for c in candidates if str(c).endswith(".mp4")]
            result = str(sorted(mp4s or candidates)[0]) if (mp4s or candidates) else output_path
            log.info("Download complete: %s", result)
            return result
        except Exception as e:
            logger = opts.get("logger")
            err_msg = str(e).strip()
            if not err_msg and logger and logger.errors:
                err_msg = logger.errors[-1].strip()
            last_err = err_msg or e
            log.warning("  yt-dlp download attempt failed (client=%s): %s", client, err_msg)
            continue
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    is_bot_block = "bot" in str(last_err).lower() or "sign in" in str(last_err).lower() or "verify" in str(last_err).lower()
    if is_bot_block:
        raise RuntimeError(
            "YouTube blocked datacenter server access for this video. "
            "Please use the YT2PDF Slide Companion browser extension for 100% guaranteed 1-click conversion directly in your browser."
        )
    raise RuntimeError(f"All yt-dlp download attempts failed. Last error: {last_err}")


# ─────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────

def run_pipeline(job_id: str, video_url: str, jobs_root: Path, gemini_api_key: str):
    """
    Full pipeline: URL → PDF.
    All status updates are written to jobs_root/<job_id>/status.json.
    The final PDF is written to jobs_root/<job_id>/output.pdf.
    """
    job_dir = jobs_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    slides_dir = job_dir / "slides"
    slides_dir.mkdir(exist_ok=True)

    tmp_dir = None

    try:
        # ── Step 1: Fetch video info ──────────────────────────────────────
        _write_status(job_dir, "fetching", 5, "Fetching video info...")
        log.info("[%s] Fetching info for: %s", job_id, video_url)

        try:
            info = _get_video_info(video_url)
        except Exception as e:
            raise RuntimeError(f"Could not fetch video info: {e}") from e

        video_title = info["title"]
        duration = info["duration"]
        log.info("[%s] Title: %s | Duration: %ds", job_id, video_title, duration)
        _write_status(job_dir, "fetching", 10, f"Found: \"{video_title}\" ({duration//60}m {duration%60}s)")

        # Save metadata
        (job_dir / "meta.json").write_text(json.dumps(info))

        # ── Step 2: Download video ────────────────────────────────────────
        _write_status(job_dir, "downloading", 12, "Downloading video stream...")
        log.info("[%s] Downloading video...", job_id)

        tmp_dir = tempfile.mkdtemp(prefix=f"yt2pdf_{job_id}_")
        video_out = os.path.join(tmp_dir, "video")

        download_progress = {"last_pct": 0}

        def ytdlp_hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 1)
                downloaded = d.get("downloaded_bytes", 0)
                pct = int((downloaded / total) * 30) + 12  # maps 0-100% → 12-42%
                if pct != download_progress["last_pct"]:
                    download_progress["last_pct"] = pct
                    speed = d.get("_speed_str", "")
                    eta = d.get("_eta_str", "")
                    _write_status(job_dir, "downloading", pct,
                                  f"Downloading... {speed} (ETA: {eta})")

        try:
            video_path = _download_video(video_url, video_out, progress_hook=ytdlp_hook, duration=duration)
        except Exception as e:
            raise RuntimeError(f"Download failed: {e}") from e

        log.info("[%s] Downloaded to: %s", job_id, video_path)
        _write_status(job_dir, "downloading", 42, "Download complete. Starting analysis...")

        # ── Step 3: CV Pass (OpenCV SSIM) ─────────────────────────────────
        from slide_extractor import ExtractorConfig, pass1_find_candidates, save_candidates_debug

        # Adapt sample_fps for long videos so CV scanning completes swiftly without excessive memory
        sample_fps = 1.0
        if duration > 21600:       # > 6 hours: sample every 10s
            sample_fps = 0.1
        elif duration > 7200:      # 2-6 hours: sample every 5s
            sample_fps = 0.2
        elif duration > 3600:      # 1-2 hours: sample every 2s
            sample_fps = 0.5
        else:                      # <= 1 hour (standard lectures, completely unchanged)
            sample_fps = 1.0

        config = ExtractorConfig(
            sample_fps=sample_fps,
            ssim_threshold=0.94,
            debounce_seconds=2,
            gemini_api_key=gemini_api_key,
            gemini_model="gemini-2.0-flash",
            output_dir=slides_dir,
            save_candidates=False,
        )

        _write_status(job_dir, "cv_scanning", 43, "Scanning frames with OpenCV...")
        log.info("[%s] Starting Pass 1 (OpenCV)...", job_id)

        import cv2
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        cap.release()
        total_sampled = max(1, int(total_frames / max(1, int(fps / config.sample_fps))))

        cv_progress = {"count": 0}

        def p1_cb(frame_count, total, ts):
            cv_progress["count"] = frame_count
            pct = 43 + int((frame_count / total) * 20)  # 43-63%
            total_sec = int(ts)
            hrs = total_sec // 3600
            mins = (total_sec % 3600) // 60
            secs = total_sec % 60
            time_str = f"{hrs:d}:{mins:02d}:{secs:02d}" if hrs > 0 else f"{mins:02d}:{secs:02d}"
            _write_status(job_dir, "cv_scanning", min(pct, 63),
                          f"Scanning frames... {frame_count}/{total} @ {time_str}")

        candidates = pass1_find_candidates(video_path, config, progress_cb=p1_cb)
        log.info("[%s] Pass 1 complete: %d candidates", job_id, len(candidates))

        if not candidates:
            _write_status(job_dir, "completed", 100,
                          "No slide transitions detected. Try a different video.",
                          slide_count=0)
            return

        _write_status(job_dir, "cv_scanning", 63,
                      f"Found {len(candidates)} candidate frames. Starting AI verification...")

        # ── Step 4: AI Pass (Gemini) ───────────────────────────────────────
        from slide_extractor import pass2_ai_verify, VerifiedSlide

        log.info("[%s] Starting Pass 2 (Gemini AI)...", job_id)

        ai_progress = {"i": 0}

        def p2_cb(i, total):
            ai_progress["i"] = i
            pct = 63 + int((i / total) * 25)  # 63-88%
            _write_status(job_dir, "ai_verifying", min(pct, 88),
                          f"AI verifying frame {i} of {total}...")

        verified = pass2_ai_verify(candidates, config, progress_cb=p2_cb)
        log.info("[%s] Pass 2 complete: %d verified slides", job_id, len(verified))

        if not verified:
            _write_status(job_dir, "completed", 100,
                          "AI found no clean slides (all were webcam or annotation-only).",
                          slide_count=0)
            return

        # ── Step 5: Save PNGs ──────────────────────────────────────────────
        _write_status(job_dir, "building_pdf", 78, "Saving slide images...")
        from slide_extractor import save_slides
        saved_paths = save_slides(verified, slides_dir)
        for s_obj, p in zip(verified, saved_paths):
            s_obj.image_path = Path(p)

        # ── Step 6: Build Slides PDF ───────────────────────────────────────
        _write_status(job_dir, "building_pdf", 82, f"Building slides PDF from {len(saved_paths)} slides...")
        log.info("[%s] Building slides PDF...", job_id)

        from pdf_builder import build_pdf
        titles = [s.slide_title for s in verified]
        slides_pdf_path = job_dir / "output.pdf"
        build_pdf(
            image_paths=saved_paths,
            slide_titles=titles,
            output_path=slides_pdf_path,
            video_title=video_title,
            include_cover=True,
        )
        log.info("[%s] Slides PDF ready: %s", job_id, slides_pdf_path)

        # ── Done with slides: Ready for download immediately ───────────────
        _write_status(
            job_dir, "completed", 100,
            f"Done! {len(verified)} slides extracted.",
            slide_count=len(verified),
        )
        (job_dir / "outputs.json").write_text(json.dumps({
            "slides_pdf": True,
            "study_guide_pdf": False,
            "slide_count": len(verified),
        }))

        # ── Step 7: AI Comprehensive Study Guide Synthesis ─────────────────
        guide_ready = False
        crops_dir = job_dir / "diagram_crops"
        crops_dir.mkdir(parents=True, exist_ok=True)

        transcript_segments = []
        try:
            from transcript_fetcher import fetch_transcript
            transcript_tmp = tempfile.mkdtemp(prefix=f"yt2pdf_tr_{job_id}_")
            try:
                transcript_segments = fetch_transcript(video_url, tmp_dir=transcript_tmp)
            finally:
                shutil.rmtree(transcript_tmp, ignore_errors=True)
        except Exception as tr_err:
            log.warning("[%s] Server transcript fetch failed: %s", job_id, tr_err)
            transcript_segments = []

        try:
            (job_dir / "transcript.json").write_text(json.dumps(transcript_segments, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

        if gemini_api_key and gemini_api_key != "YOUR_GEMINI_API_KEY_HERE":
            try:
                log.info("[%s] Synthesizing study guide with Gemini (%d transcript segments)...", job_id, len(transcript_segments))
                from study_guide_generator import generate_study_guide_content
                from pdf_study_guide import build_study_guide_pdf

                study_guide = generate_study_guide_content(
                    slides=verified,
                    transcript_segments=transcript_segments,
                    total_duration=float(duration),
                    gemini_api_key=gemini_api_key,
                    crops_dir=crops_dir,
                    gemini_model="gemini-2.0-flash",
                    video_title=video_title,
                )
                guide_pdf_path = job_dir / "study_guide.pdf"
                build_study_guide_pdf(
                    study_guide=study_guide,
                    output_path=guide_pdf_path,
                    video_title=video_title,
                )
                guide_ready = guide_pdf_path.exists() and guide_pdf_path.stat().st_size > 1500
                log.info("[%s] Study guide PDF ready: %s (size: %d bytes)", job_id, guide_ready, guide_pdf_path.stat().st_size if guide_pdf_path.exists() else 0)
            except Exception as guide_err:
                log.warning("[%s] Study guide synthesis error: %s", job_id, guide_err)

        if not guide_ready and verified:
            try:
                from study_guide_generator import generate_deterministic_study_guide
                from pdf_study_guide import build_study_guide_pdf
                det_guide = generate_deterministic_study_guide(
                    slides=verified,
                    transcript_segments=transcript_segments,
                    video_title=video_title,
                    total_duration=float(duration),
                    crops_dir=crops_dir,
                )
                guide_pdf_path = job_dir / "study_guide.pdf"
                build_study_guide_pdf(
                    study_guide=det_guide,
                    output_path=guide_pdf_path,
                    video_title=video_title,
                )
                guide_ready = guide_pdf_path.exists() and guide_pdf_path.stat().st_size > 1500
            except Exception as det_err:
                log.warning("[%s] Fallback study guide compilation error: %s", job_id, det_err)

        if guide_ready:
            (job_dir / "outputs.json").write_text(json.dumps({
                "slides_pdf": True,
                "study_guide_pdf": True,
                "slide_count": len(verified),
            }))

        # ── Cache completed job ───────────────────────────────────────────
        try:
            from drive_cache import cache_manager, extract_youtube_id
            vid_id = extract_youtube_id(video_url)
            if vid_id:
                cache_manager.save_job_to_cache(vid_id, job_dir)
        except Exception as cache_err:
            log.warning("[%s] Failed to save to cache: %s", job_id, cache_err)

    except Exception as e:
        log.exception("[%s] Pipeline failed: %s", job_id, e)
        _write_status(job_dir, "failed", 0, "Processing failed.", error=str(e))

    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            try:
                shutil.rmtree(tmp_dir)
                log.info("[%s] Cleaned up temp dir: %s", job_id, tmp_dir)
            except Exception:
                pass


def run_pipeline_from_frames(
    job_id: str,
    frames_data: list,
    video_title: str,
    video_url: str,
    duration: float,
    jobs_root: Path,
    gemini_api_key: str,
    client_transcript: Optional[list] = None,
    audio_url: Optional[str] = None,
    audio_data: Optional[str] = None,
    audio_mime: Optional[str] = "audio/mp4",
):
    """
    Orchestration pipeline for client-captured frames (YT2PDF Slide Companion Extension).
    Directly processes browser-captured frames, bypassing YouTube datacenter bot detection.
    Runs AI verification or deduplication, builds slides PDF, and compiles a comprehensive study guide.
    """
    import base64
    from slide_extractor import CandidateFrame, ExtractorConfig, pass2_ai_verify, save_slides, VerifiedSlide

    job_dir = jobs_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    slides_dir = job_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    raw_frames_dir = job_dir / "_raw_frames"
    raw_frames_dir.mkdir(parents=True, exist_ok=True)

    try:
        log.info("[%s] Companion pipeline starting with %d frames", job_id, len(frames_data))
        _write_status(job_dir, "companion_received", 30, f"Decoding {len(frames_data)} frames from browser...")

        # Save meta.json
        meta = {
            "video_title": video_title,
            "video_url": video_url,
            "duration": duration,
            "source": "chrome_companion",
            "frame_count": len(frames_data),
        }
        (job_dir / "meta.json").write_text(json.dumps(meta, indent=2))

        # Decode base64 frames to candidate image files
        candidates = []
        for idx, item in enumerate(frames_data, 1):
            ts = float(item.get("timestamp", idx * 10))
            b64_str = item.get("data", "")
            if not b64_str:
                continue
            if "," in b64_str:
                b64_str = b64_str.split(",", 1)[1]
            try:
                img_bytes = base64.b64decode(b64_str)
                frame_path = raw_frames_dir / f"cand_{idx:03d}_t{int(ts):05d}s.jpg"
                frame_path.write_bytes(img_bytes)
                candidates.append(CandidateFrame(frame_index=idx, timestamp_sec=ts, image_or_path=frame_path))
            except Exception as dec_err:
                log.warning("[%s] Failed to decode frame %d: %s", job_id, idx, dec_err)

        if not candidates:
            _write_status(job_dir, "failed", 0, "No valid frames could be decoded.", error="No frames decoded")
            return

        log.info("[%s] Decoded %d valid candidate frames", job_id, len(candidates))

        # AI verification pass (Gemini)
        verified = []
        if gemini_api_key and gemini_api_key != "YOUR_GEMINI_API_KEY_HERE":
            _write_status(job_dir, "ai_verifying", 50, f"AI verifying {len(candidates)} frames...")
            config = ExtractorConfig(
                gemini_api_key=gemini_api_key,
                gemini_model="gemini-2.0-flash",
                output_dir=slides_dir,
            )

            def p2_cb(i, total):
                pct = 50 + int((i / total) * 25)  # 50-75%
                _write_status(job_dir, "ai_verifying", min(pct, 75), f"AI verifying frame {i} of {total}...")

            try:
                verified = pass2_ai_verify(candidates, config, progress_cb=p2_cb)
                log.info("[%s] AI verification accepted %d/%d frames", job_id, len(verified), len(candidates))
            except Exception as ai_err:
                log.warning("[%s] Gemini verification error: %s. Falling back to all candidate frames.", job_id, ai_err)
                verified = []

        # Fallback if AI rejected all or was unavailable
        if not verified:
            log.info("[%s] Using all %d candidate frames as slides", job_id, len(candidates))
            for idx, c in enumerate(candidates, 1):
                total_sec = int(c.timestamp_sec)
                hrs = total_sec // 3600
                mins = (total_sec % 3600) // 60
                secs = total_sec % 60
                time_str = f"{hrs:d}:{mins:02d}:{secs:02d}" if hrs > 0 else f"{mins:02d}:{secs:02d}"
                verified.append(VerifiedSlide(
                    frame_index=c.frame_index,
                    timestamp_sec=c.timestamp_sec,
                    image_or_path=c.image_path,
                    slide_title=f"Slide {idx} ({time_str})",
                    is_slide=True,
                    is_new_content=True,
                ))

        # Save slide images to final slides_dir as PNGs
        _write_status(job_dir, "building_pdf", 78, "Saving slide images...")
        saved_paths = save_slides(verified, slides_dir)
        for s_obj, p in zip(verified, saved_paths):
            s_obj.image_path = Path(p)

        # Build Slides PDF
        _write_status(job_dir, "building_pdf", 82, f"Building slides PDF from {len(saved_paths)} slides...")
        from pdf_builder import build_pdf
        titles = [s.slide_title for s in verified]
        slides_pdf_path = job_dir / "output.pdf"
        build_pdf(
            image_paths=saved_paths,
            slide_titles=titles,
            output_path=slides_pdf_path,
            video_title=video_title,
            include_cover=True,
        )
        log.info("[%s] Slides PDF ready: %s", job_id, slides_pdf_path)

        # Cleanup raw frames
        shutil.rmtree(raw_frames_dir, ignore_errors=True)

        # Update status: slides compiled, now synthesizing AI study guide
        _write_status(
            job_dir, "generating_guide", 90,
            f"Slides compiled ({len(verified)} slides). Synthesizing AI Study Guide...",
            slide_count=len(verified),
        )
        (job_dir / "outputs.json").write_text(json.dumps({
            "slides_pdf": True,
            "study_guide_pdf": False,
            "slide_count": len(verified),
        }))

        # Handle Audio Track (decoded base64, server fetch from audio_url, or uploaded file)
        audio_file_candidate = None
        for cand_name in ["audio.mp4", "audio.webm", "audio.mp3", "audio.m4a"]:
            if (job_dir / cand_name).exists() and (job_dir / cand_name).stat().st_size > 1000:
                audio_file_candidate = job_dir / cand_name
                break

        if not audio_file_candidate and audio_data:
            try:
                audio_ext = ".webm" if "webm" in (audio_mime or "") else ".mp4"
                audio_file_candidate = job_dir / f"audio{audio_ext}"
                b64_aud = audio_data.split(",", 1)[1] if "," in audio_data else audio_data
                audio_file_candidate.write_bytes(base64.b64decode(b64_aud))
                log.info("[%s] Decoded audio_data (%d bytes)", job_id, audio_file_candidate.stat().st_size)
            except Exception as aud_dec_err:
                log.warning("[%s] Failed to decode audio_data: %s", job_id, aud_dec_err)
                audio_file_candidate = None

        if not audio_file_candidate and audio_url:
            try:
                import urllib.request
                log.info("[%s] Attempting server download of audio_url...", job_id)
                audio_ext = ".webm" if "webm" in (audio_mime or "") else ".mp4"
                temp_aud = job_dir / f"audio{audio_ext}"
                req = urllib.request.Request(audio_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=12) as response, open(temp_aud, "wb") as out_f:
                    # Stream up to 25MB
                    written = 0
                    while chunk := response.read(65536):
                        out_f.write(chunk)
                        written += len(chunk)
                        if written > 25 * 1024 * 1024:
                            break
                if temp_aud.stat().st_size > 1000:
                    audio_file_candidate = temp_aud
                    log.info("[%s] Downloaded audio track (%d bytes)", job_id, audio_file_candidate.stat().st_size)
            except Exception as aud_dl_err:
                log.debug("[%s] audio_url server fetch notice: %s", job_id, aud_dl_err)

        # Generate Comprehensive Study Guide with Gemini
        guide_ready = False
        crops_dir = job_dir / "diagram_crops"
        crops_dir.mkdir(parents=True, exist_ok=True)
        transcript_segments = client_transcript or []

        if not transcript_segments and video_url:
            log.info("[%s] Client transcript empty, falling back to server fetch_transcript(%s)...", job_id, video_url)
            try:
                from transcript_fetcher import fetch_transcript
                transcript_segments = fetch_transcript(video_url, tmp_dir=job_dir)
                log.info("[%s] Server successfully fetched %d transcript segments.", job_id, len(transcript_segments))
            except Exception as tr_err:
                log.warning("[%s] Server transcript fetch notice: %s", job_id, tr_err)

        try:
            (job_dir / "transcript.json").write_text(json.dumps(transcript_segments, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

        if gemini_api_key and gemini_api_key != "YOUR_GEMINI_API_KEY_HERE":
            try:
                log.info("[%s] Synthesizing companion study guide with Gemini (%d transcript segments, audio=%s)...",
                         job_id, len(transcript_segments), bool(audio_file_candidate))
                from study_guide_generator import generate_study_guide_content
                from pdf_study_guide import build_study_guide_pdf

                study_guide = generate_study_guide_content(
                    slides=verified,
                    transcript_segments=transcript_segments,
                    total_duration=float(duration),
                    gemini_api_key=gemini_api_key,
                    crops_dir=crops_dir,
                    gemini_model="gemini-2.0-flash",
                    audio_path=audio_file_candidate,
                    video_title=video_title,
                )
                guide_pdf_path = job_dir / "study_guide.pdf"
                build_study_guide_pdf(
                    study_guide=study_guide,
                    output_path=guide_pdf_path,
                    video_title=video_title,
                )
                guide_ready = guide_pdf_path.exists() and guide_pdf_path.stat().st_size > 1500
                log.info("[%s] Companion study guide PDF ready: %s (size: %d bytes)", job_id, guide_ready, guide_pdf_path.stat().st_size if guide_pdf_path.exists() else 0)
            except Exception as guide_err:
                log.warning("[%s] Companion study guide generation failed: %s", job_id, guide_err)

        if not guide_ready and verified:
            try:
                from study_guide_generator import generate_deterministic_study_guide
                from pdf_study_guide import build_study_guide_pdf
                det_guide = generate_deterministic_study_guide(
                    slides=verified,
                    transcript_segments=transcript_segments,
                    video_title=video_title,
                    total_duration=float(duration),
                    crops_dir=crops_dir,
                )
                guide_pdf_path = job_dir / "study_guide.pdf"
                build_study_guide_pdf(
                    study_guide=det_guide,
                    output_path=guide_pdf_path,
                    video_title=video_title,
                )
                guide_ready = guide_pdf_path.exists() and guide_pdf_path.stat().st_size > 1500
            except Exception as det_err:
                log.warning("[%s] Companion fallback study guide failed: %s", job_id, det_err)

        (job_dir / "outputs.json").write_text(json.dumps({
            "slides_pdf": True,
            "study_guide_pdf": guide_ready,
            "slide_count": len(verified),
        }))
        _write_status(
            job_dir, "completed", 100,
            f"Done! {len(verified)} slides & AI study guide ready.",
            slide_count=len(verified),
        )

        # Cache completed job in 5TB storage
        try:
            from drive_cache import cache_manager, extract_youtube_id
            vid_id = extract_youtube_id(video_url)
            if vid_id:
                cache_manager.save_job_to_cache(vid_id, job_dir)
        except Exception as cache_err:
            log.warning("[%s] Failed to save to cache: %s", job_id, cache_err)

    except Exception as e:
        log.exception("[%s] Companion pipeline failed: %s", job_id, e)
        _write_status(job_dir, "failed", 0, "Processing failed.", error=str(e))


def run_pipeline_from_video_file(
    job_id: str,
    video_path: Path,
    jobs_root: Path,
    gemini_api_key: str,
):
    """
    Orchestration pipeline for user-uploaded video files (.mp4, .mov, .webm, .mkv).
    Generates presentation slides PDF and comprehensive AI study guide PDF.
    Session-based ephemeral only (NOT stored in global 5TB cache).
    """
    job_dir = jobs_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    slides_dir = job_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    crops_dir = job_dir / "diagram_crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    try:
        video_title = video_path.stem.replace("_", " ").title()
        log.info("[%s] Video upload pipeline starting for %s", job_id, video_path.name)
        _write_status(job_dir, "analyzing_video", 15, "Analyzing uploaded video format and streams...")

        # 1. Probe video metadata with OpenCV
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise IOError(f"Cannot open video file: {video_path}")
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        duration = total_frames / fps if fps > 0 else 0.0
        cap.release()

        # Save meta.json
        meta = {
            "title": video_title,
            "duration": duration,
            "source": "video_upload",
            "filename": video_path.name,
        }
        (job_dir / "meta.json").write_text(json.dumps(meta, indent=2))

        # 2. Extract audio track using ffmpeg
        audio_path = job_dir / "audio.mp3"
        try:
            log.info("[%s] Extracting audio with ffmpeg...", job_id)
            cmd = [
                "ffmpeg", "-y", "-i", str(video_path),
                "-vn", "-acodec", "libmp3lame", "-q:a", "4",
                "-ar", "24000", "-ac", "1",
                str(audio_path)
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=120)
            if audio_path.exists() and audio_path.stat().st_size > 1000:
                log.info("[%s] Audio extracted: %s (%d bytes)", job_id, audio_path, audio_path.stat().st_size)
            else:
                audio_path = None
        except Exception as ff_err:
            log.warning("[%s] ffmpeg audio extraction skipped: %s", job_id, ff_err)
            audio_path = None

        # 3. CV Candidate Detection (Pass 1)
        from slide_extractor import ExtractorConfig, pass1_find_candidates, pass2_ai_verify, save_slides, VerifiedSlide
        from pdf_builder import build_pdf

        sample_fps = 1.0
        if duration > 21600:
            sample_fps = 0.1
        elif duration > 7200:
            sample_fps = 0.2
        elif duration > 3600:
            sample_fps = 0.5

        config = ExtractorConfig(
            sample_fps=sample_fps,
            ssim_threshold=0.94,
            debounce_seconds=2,
            gemini_api_key=gemini_api_key,
            gemini_model="gemini-2.0-flash",
            output_dir=slides_dir,
            save_candidates=False,
        )

        _write_status(job_dir, "cv_scanning", 30, "Scanning video frames with OpenCV...")
        total_sampled = max(1, int(total_frames / max(1, int(fps / config.sample_fps))))

        def p1_cb(frame_count, total, ts):
            pct = 30 + int((frame_count / total) * 30)  # 30-60%
            _write_status(job_dir, "cv_scanning", min(pct, 60), f"Scanning frames... {frame_count}/{total}")

        candidates = pass1_find_candidates(str(video_path), config, progress_cb=p1_cb)
        log.info("[%s] Pass 1 complete: %d candidates found", job_id, len(candidates))

        if not candidates:
            _write_status(job_dir, "failed", 0, "No slides detected in uploaded video.", error="No slides found")
            return

        # 4. AI Verification (Pass 2)
        verified = []
        if gemini_api_key and gemini_api_key != "YOUR_GEMINI_API_KEY_HERE":
            _write_status(job_dir, "ai_verifying", 62, f"AI verifying {len(candidates)} candidate frames...")

            def p2_cb(i, total):
                pct = 62 + int((i / total) * 20)  # 62-82%
                _write_status(job_dir, "ai_verifying", min(pct, 82), f"AI verifying frame {i} of {total}...")

            try:
                verified = pass2_ai_verify(candidates, config, progress_cb=p2_cb)
            except Exception as ai_err:
                log.warning("[%s] AI verification error: %s. Using all candidates.", job_id, ai_err)
                verified = []

        if not verified:
            for idx, c in enumerate(candidates, 1):
                total_sec = int(c.timestamp_sec)
                mins = total_sec // 60
                secs = total_sec % 60
                verified.append(VerifiedSlide(
                    frame_index=c.frame_index,
                    timestamp_sec=c.timestamp_sec,
                    image_or_path=c.image_path,
                    slide_title=f"Slide {idx} ({mins}:{secs:02d})",
                    is_slide=True,
                    is_new_content=True,
                ))

        # 5. Save Slides & Build Presentation Slides PDF
        _write_status(job_dir, "building_pdf", 84, f"Saving {len(verified)} slide images...")
        saved_paths = save_slides(verified, slides_dir)
        for s_obj, p in zip(verified, saved_paths):
            s_obj.image_path = Path(p)

        slides_pdf_path = job_dir / "output.pdf"
        titles = [s.slide_title for s in verified]
        build_pdf(
            image_paths=saved_paths,
            slide_titles=titles,
            output_path=slides_pdf_path,
            video_title=video_title,
            include_cover=True,
        )
        log.info("[%s] Slides PDF ready: %s", job_id, slides_pdf_path)

        _write_status(
            job_dir, "generating_guide", 90,
            f"Slides compiled ({len(verified)} slides). Synthesizing AI Study Guide from video audio...",
            slide_count=len(verified),
        )
        (job_dir / "outputs.json").write_text(json.dumps({
            "slides_pdf": True,
            "study_guide_pdf": False,
            "slide_count": len(verified),
        }))

        # 6. Generate Comprehensive AI Study Guide
        guide_ready = False
        if gemini_api_key and gemini_api_key != "YOUR_GEMINI_API_KEY_HERE":
            try:
                log.info("[%s] Synthesizing study guide for uploaded video...", job_id)
                from study_guide_generator import generate_study_guide_content
                from pdf_study_guide import build_study_guide_pdf

                study_guide = generate_study_guide_content(
                    slides=verified,
                    transcript_segments=[],
                    total_duration=float(duration),
                    gemini_api_key=gemini_api_key,
                    crops_dir=crops_dir,
                    gemini_model="gemini-2.0-flash",
                    audio_path=audio_path,
                    video_title=video_title,
                )
                guide_pdf_path = job_dir / "study_guide.pdf"
                build_study_guide_pdf(
                    study_guide=study_guide,
                    output_path=guide_pdf_path,
                    video_title=video_title,
                )
                guide_ready = guide_pdf_path.exists() and guide_pdf_path.stat().st_size > 1500
            except Exception as guide_err:
                log.warning("[%s] Uploaded video study guide synthesis error: %s", job_id, guide_err)

        if not guide_ready and verified:
            try:
                from study_guide_generator import generate_deterministic_study_guide
                from pdf_study_guide import build_study_guide_pdf
                det_guide = generate_deterministic_study_guide(
                    slides=verified,
                    transcript_segments=[],
                    video_title=video_title,
                    total_duration=float(duration),
                    crops_dir=crops_dir,
                )
                guide_pdf_path = job_dir / "study_guide.pdf"
                build_study_guide_pdf(
                    study_guide=det_guide,
                    output_path=guide_pdf_path,
                    video_title=video_title,
                )
                guide_ready = guide_pdf_path.exists() and guide_pdf_path.stat().st_size > 1500
            except Exception as det_err:
                log.warning("[%s] Deterministic study guide error: %s", job_id, det_err)

        (job_dir / "outputs.json").write_text(json.dumps({
            "slides_pdf": True,
            "study_guide_pdf": guide_ready,
            "slide_count": len(verified),
        }))
        _write_status(
            job_dir, "completed", 100,
            f"Done! {len(verified)} slides & AI study guide ready.",
            slide_count=len(verified),
        )

    except Exception as e:
        log.exception("[%s] Video upload pipeline failed: %s", job_id, e)
        _write_status(job_dir, "failed", 0, "Processing failed.", error=str(e))

