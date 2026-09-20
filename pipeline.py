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
        "socket_timeout": 20,
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

    base_no_cookies = dict(base)
    base_no_cookies.pop("cookiefile", None)

    # 1. Top priority: Web client with genuine BotGuard Proof-of-Origin (PO) Token
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

    # 2. android_vr client without cookies (proven bypass for datacenter IPs)
    v_vr = dict(base_no_cookies)
    v_vr["extractor_args"] = {"youtube": {"player_client": ["android_vr"]}}
    v_vr["logger"] = _YtdlpLogger()
    variants.append(v_vr)

    # 3. Android client without cookies
    va = dict(base_no_cookies)
    va["extractor_args"] = {"youtube": {"player_client": ["android"]}}
    va["logger"] = _YtdlpLogger()
    variants.append(va)

    # 4. Authenticated cookies variants (if cookies are provided)
    if has_valid_cookies and "cookiefile" in base:
        v_c_vr = dict(base)
        v_c_vr["extractor_args"] = {"youtube": {"player_client": ["android_vr"]}}
        v_c_vr["logger"] = _YtdlpLogger()
        variants.append(v_c_vr)

        v_c_web = dict(base)
        v_c_web["extractor_args"] = {"youtube": {"player_client": ["web"]}}
        v_c_web["logger"] = _YtdlpLogger()
        variants.append(v_c_web)

        v_c_mweb = dict(base)
        v_c_mweb["extractor_args"] = {"youtube": {"player_client": ["mweb"]}}
        v_c_mweb["logger"] = _YtdlpLogger()
        variants.append(v_c_mweb)

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
    """Fetch video title and duration without downloading."""
    import yt_dlp

    video_id = _extract_video_id(url)
    all_errors = []
    for opts in _build_ydl_opts_base({"skip_download": True}, video_id=video_id):
        client = opts.get("extractor_args", {}).get("youtube", {}).get("player_client", ["default"])[0]
        logger = opts.get("logger")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            return {
                "title": info.get("title", "Untitled Video"),
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", ""),
                "thumbnail": info.get("thumbnail", ""),
            }
        except Exception as e:
            err_msg = str(e).strip()
            if not err_msg and logger and logger.errors:
                err_msg = logger.errors[-1].strip()
            all_errors.append(f"{client}: {err_msg}")
            log.warning("  yt-dlp info attempt failed (client=%s): %s", client, err_msg)
            continue

    raise RuntimeError(f"All yt-dlp attempts failed. Errors: {'; '.join(all_errors)}")


def _download_video(url: str, output_path: str, progress_hook: Optional[Callable] = None) -> str:
    """Download video to output_path using yt-dlp."""
    import yt_dlp

    video_id = _extract_video_id(url)
    hooks = [progress_hook] if progress_hook else []
    format_str = "bestvideo[height<=720]/best[height<=720]/bestvideo/best"

    last_err = None
    for opts in _build_ydl_opts_base({
        "format": format_str,
        "outtmpl": output_path,
        "progress_hooks": hooks,
        "merge_output_format": "mp4",
        "postprocessors": [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }] if shutil.which("ffmpeg") else [],
    }, video_id=video_id):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            # yt-dlp may append extension; find the actual output file
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
            client = opts.get("extractor_args", {}).get("youtube", {}).get("player_client", ["default"])[0]
            log.warning("  yt-dlp download attempt failed (client=%s): %s", client, err_msg)
            continue

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
            video_path = _download_video(video_url, video_out, progress_hook=ytdlp_hook)
        except Exception as e:
            raise RuntimeError(f"Download failed: {e}") from e

        log.info("[%s] Downloaded to: %s", job_id, video_path)
        _write_status(job_dir, "downloading", 42, "Download complete. Starting analysis...")

        # ── Step 3: CV Pass (OpenCV SSIM) ─────────────────────────────────
        from slide_extractor import ExtractorConfig, pass1_find_candidates, save_candidates_debug

        config = ExtractorConfig(
            sample_fps=1,
            ssim_threshold=0.85,
            debounce_seconds=3,
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
            mins, secs = divmod(int(ts), 60)
            _write_status(job_dir, "cv_scanning", min(pct, 63),
                          f"Scanning frames... {frame_count}/{total} @ {mins:02d}:{secs:02d}")

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

        # ── Step 7: Fetch Transcript ───────────────────────────────────────
        _write_status(job_dir, "fetching_transcript", 85,
                      "Fetching video transcript for study guide...")
        log.info("[%s] Fetching transcript...", job_id)

        transcript_tmp = tempfile.mkdtemp(prefix=f"yt2pdf_tr_{job_id}_")
        try:
            from transcript_fetcher import fetch_transcript
            transcript_segments = fetch_transcript(video_url, tmp_dir=transcript_tmp)
            log.info("[%s] Got %d transcript segments", job_id, len(transcript_segments))
        except Exception as e:
            log.warning("[%s] Transcript fetch failed (will use image-only): %s", job_id, e)
            transcript_segments = []
        finally:
            shutil.rmtree(transcript_tmp, ignore_errors=True)

        # ── Step 8: AI Study Guide Generation ─────────────────────────────
        _write_status(job_dir, "generating_guide", 87,
                      f"AI writing study guide for {len(verified)} slides...")
        log.info("[%s] Starting study guide generation...", job_id)

        from study_guide_generator import generate_study_guide_content

        guide_progress = {"i": 0}

        def guide_cb(i, total):
            guide_progress["i"] = i
            pct = 87 + int((i / total) * 10)   # 87-97%
            _write_status(
                job_dir, "generating_guide", min(pct, 97),
                f"AI writing section {i} of {total}...",
                slide_count=len(verified),
            )

        crops_dir = job_dir / "diagram_crops"
        crops_dir.mkdir(parents=True, exist_ok=True)

        try:
            study_guide = generate_study_guide_content(
                slides=verified,
                transcript_segments=transcript_segments,
                total_duration=float(duration),
                gemini_api_key=gemini_api_key,
                crops_dir=crops_dir,
                gemini_model="gemini-2.0-flash",
                progress_cb=guide_cb,
            )

            # ── Step 9: Build Study Guide PDF ─────────────────────────────
            _write_status(job_dir, "generating_guide", 97,
                          "Compiling study guide PDF...", slide_count=len(verified))
            log.info("[%s] Building study guide PDF...", job_id)

            from pdf_study_guide import build_study_guide_pdf
            guide_pdf_path = job_dir / "study_guide.pdf"
            build_study_guide_pdf(
                study_guide=study_guide,
                output_path=guide_pdf_path,
                video_title=video_title,
            )
            log.info("[%s] Study guide PDF ready: %s", job_id, guide_pdf_path)
            guide_ready = True

        except Exception as e:
            log.exception("[%s] Study guide generation failed: %s", job_id, e)
            guide_ready = False

        # ── Done ───────────────────────────────────────────────────────────
        _write_status(
            job_dir, "completed", 100,
            f"Done! {len(verified)} slides extracted."
            + (" Study guide also ready!" if guide_ready else ""),
            slide_count=len(verified),
        )
        # Write a flags file so the frontend knows what's available
        (job_dir / "outputs.json").write_text(json.dumps({
            "slides_pdf": True,
            "study_guide_pdf": guide_ready,
            "has_transcript": bool(transcript_segments),
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
):
    """
    Orchestration pipeline for client-captured frames (YT2PDF Slide Companion Extension).
    Directly processes browser-captured frames, bypassing YouTube datacenter bot detection.
    Runs AI verification or deduplication, builds slides PDF, and optionally compiles a study guide.
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
                mins, secs = divmod(int(c.timestamp_sec), 60)
                verified.append(VerifiedSlide(
                    frame_index=c.frame_index,
                    timestamp_sec=c.timestamp_sec,
                    image_or_path=c.image_path,
                    slide_title=f"Slide {idx} ({mins:02d}:{secs:02d})",
                    is_slide=True,
                    is_new_content=True,
                ))

        # Save slide images to final slides_dir as PNGs
        _write_status(job_dir, "building_pdf", 78, "Saving slide images...")
        saved_paths = save_slides(verified, slides_dir)

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

        # Transcript & Study guide (Guaranteed Generation)
        guide_ready = False
        transcript_segments = client_transcript or []
        if not transcript_segments:
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

        if gemini_api_key and gemini_api_key != "YOUR_GEMINI_API_KEY_HERE":
            try:
                if transcript_segments:
                    log.info("[%s] Generating study guide with %d transcript segments...", job_id, len(transcript_segments))
                    _write_status(job_dir, "generating_guide", 88, f"AI writing study guide with lecture transcript for {len(verified)} slides...")
                else:
                    log.info("[%s] No audio captions detected; generating study guide directly from slide visual formulas & diagrams...", job_id)
                    _write_status(job_dir, "generating_guide", 88, f"AI synthesizing study guide from slide formulas & diagrams ({len(verified)} slides)...")

                from study_guide_generator import generate_study_guide_content
                crops_dir = job_dir / "diagram_crops"
                crops_dir.mkdir(parents=True, exist_ok=True)
                study_guide = generate_study_guide_content(
                    slides=verified,
                    transcript_segments=transcript_segments,
                    total_duration=float(duration),
                    gemini_api_key=gemini_api_key,
                    crops_dir=crops_dir,
                    gemini_model="gemini-2.0-flash",
                )
                from pdf_study_guide import build_study_guide_pdf
                guide_pdf_path = job_dir / "study_guide.pdf"
                build_study_guide_pdf(
                    study_guide=study_guide,
                    output_path=guide_pdf_path,
                    video_title=video_title,
                )
                guide_ready = True
                log.info("[%s] Study guide PDF ready: %s", job_id, guide_pdf_path)
            except Exception as guide_err:
                log.warning("[%s] Study guide generation failed: %s", job_id, guide_err)

        # Cleanup raw frames
        shutil.rmtree(raw_frames_dir, ignore_errors=True)

        # Write final completion status & outputs
        _write_status(
            job_dir, "completed", 100,
            f"Done! {len(verified)} slides extracted."
            + (" Study guide also ready!" if guide_ready else ""),
            slide_count=len(verified),
        )
        (job_dir / "outputs.json").write_text(json.dumps({
            "slides_pdf": True,
            "study_guide_pdf": guide_ready,
            "has_transcript": bool(transcript_segments),
            "slide_count": len(verified),
        }))

        # Cache completed job
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

