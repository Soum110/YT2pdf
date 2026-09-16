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
    """
    def debug(self, msg):
        if msg.startswith("[debug]"):
            log.debug("yt-dlp: %s", msg)
        else:
            log.info("yt-dlp: %s", msg)

    def info(self, msg):
        log.info("yt-dlp: %s", msg)

    def warning(self, msg):
        log.warning("yt-dlp: %s", msg)

    def error(self, msg):
        log.error("yt-dlp: %s", msg)


def _build_ydl_opts_base(extra: dict = None) -> list[dict]:
    """
    Returns a list of yt-dlp option dicts to try in order.
    Uses a custom logger (never writes to stdout/stderr) to prevent
    Broken pipe errors in background threads.
    Android player client bypasses YouTube SABR/PO token blocking.
    """
    base = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "logger": _YtdlpLogger(),   # ← key fix: no stdout/stderr writes
        "noprogress": True,
    }

    # Support YouTube cookies to bypass cloud data-center anti-bot blocks
    cookie_content = os.environ.get("YOUTUBE_COOKIES", "").strip()
    cookie_file = os.environ.get("YOUTUBE_COOKIE_FILE", "").strip()
    if cookie_content:
        cpath = Path(tempfile.gettempdir()) / "yt_cookies.txt"
        cpath.write_text(cookie_content)
        base["cookiefile"] = str(cpath)
    elif cookie_file and os.path.exists(cookie_file):
        base["cookiefile"] = cookie_file
    elif os.path.exists("cookies.txt"):
        base["cookiefile"] = "cookies.txt"

    if extra:
        base.update(extra)

    # Try android, ios, and mobile clients first
    client_variants = [
        {"extractor_args": {"youtube": {"player_client": ["android"]}}},
        {"extractor_args": {"youtube": {"player_client": ["ios"]}}},
        {"extractor_args": {"youtube": {"player_client": ["android", "web"]}}},
        {"extractor_args": {"youtube": {"player_client": ["tv_embedded"]}}},
        {"extractor_args": {"youtube": {"player_client": ["mweb"]}}},
        {},  # default fallback
    ]

    variants = []
    for cv in client_variants:
        v = dict(base)
        v.update(cv)
        variants.append(v)

    return variants


def _get_video_info(url: str) -> dict:
    """Fetch video title and duration without downloading."""
    import yt_dlp

    last_err = None
    for opts in _build_ydl_opts_base({"skip_download": True}):
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
            last_err = e
            client = opts.get("extractor_args", {}).get("youtube", {}).get("player_client", ["default"])[0]
            log.warning("  yt-dlp info attempt failed (client=%s): %s", client, e)
            continue

    raise RuntimeError(f"All yt-dlp attempts failed. Last error: {last_err}")


def _download_video(url: str, output_path: str, progress_hook: Optional[Callable] = None) -> str:
    """Download video to output_path using yt-dlp."""
    import yt_dlp

    # Use our own progress hook that doesn't touch stdout
    hooks = [progress_hook] if progress_hook else []

    format_str = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"

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
    }):
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
            last_err = e
            client = opts.get("extractor_args", {}).get("youtube", {}).get("player_client", ["default"])[0]
            log.warning("  yt-dlp download attempt failed (client=%s): %s", client, e)
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
            gemini_model="gemini-3.5-flash-lite",
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
                gemini_model="gemini-3.5-flash-lite",
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
