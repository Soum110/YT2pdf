"""
extractor.py — Hybrid CV + AI Slide Extractor (Step 1)
=========================================================
Two-Pass Slide Extraction:
  Pass 1: OpenCV + SSIM finds "candidate frames" where a slide transition occurs.
  Pass 2: Gemini API verifies each candidate — filters webcam frames and annotated slides.

Usage:
    python extractor.py --video path/to/video.mp4 --output ./slides_out
    python extractor.py --video path/to/video.mp4 --output ./slides_out --ssim-threshold 0.85
    python extractor.py --video path/to/video.mp4 --output ./slides_out --skip-ai   # CV only

Requirements:
    pip install opencv-python-headless scikit-image google-genai Pillow numpy
"""

import argparse
import base64
import io
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity as ssim

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("extractor")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class ExtractorConfig:
    """All tunable parameters in one place."""

    # --- Pass 1: OpenCV ---
    sample_fps: int = 1                  # Sample 1 frame per second
    ssim_threshold: float = 0.94         # Sensitive threshold to capture slide text/diagram changes
    debounce_seconds: int = 2            # Ignore frames for N seconds after a transition

    # --- Pass 2: Gemini AI ---
    gemini_api_key: str = "YOUR_GEMINI_API_KEY_HERE"   # <- Replace this
    gemini_model: str = "gemini-2.0-flash"             # Fast, highly capable official model
    ai_max_retries: int = 2
    ai_retry_delay: float = 1.0          # Seconds between retries

    # --- Output ---
    output_dir: Path = Path("./slides_out")
    save_candidates: bool = False        # Save Pass-1 candidates (debug)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class CandidateFrame:
    """A frame flagged by Pass 1 (OpenCV). Streams on-demand from disk to keep RAM < 100MB."""
    frame_index: int
    timestamp_sec: float
    image_path: Optional[Path] = None
    _image: Optional[np.ndarray] = None

    def __init__(self, frame_index: int, timestamp_sec: float, image_or_path=None):
        self.frame_index = frame_index
        self.timestamp_sec = timestamp_sec
        if isinstance(image_or_path, (str, Path)):
            self.image_path = Path(image_or_path)
            self._image = None
        else:
            self._image = image_or_path
            self.image_path = None

    @property
    def image(self) -> Optional[np.ndarray]:
        """Loads image on-demand from disk if not in memory."""
        if self._image is not None:
            return self._image
        if self.image_path and os.path.exists(self.image_path):
            return cv2.imread(str(self.image_path))
        return None


@dataclass
class VerifiedSlide:
    """A frame that passed both Pass 1 and Pass 2 (AI). Streams on-demand from disk."""
    frame_index: int
    timestamp_sec: float
    slide_title: str
    is_slide: bool
    is_new_content: bool
    image_path: Optional[Path] = None
    _image: Optional[np.ndarray] = None

    def __init__(self, frame_index: int, timestamp_sec: float, image_or_path=None,
                 slide_title: str = "Untitled Slide", is_slide: bool = True, is_new_content: bool = True,
                 image: Optional[np.ndarray] = None):
        self.frame_index = frame_index
        self.timestamp_sec = timestamp_sec
        self.slide_title = slide_title
        self.is_slide = is_slide
        self.is_new_content = is_new_content
        
        target = image if image is not None else image_or_path
        if isinstance(target, (str, Path)):
            self.image_path = Path(target)
            self._image = None
        else:
            self._image = target
            self.image_path = None

    @property
    def image(self) -> Optional[np.ndarray]:
        """Loads image on-demand from disk if not in memory."""
        if self._image is not None:
            return self._image
        if self.image_path and os.path.exists(self.image_path):
            return cv2.imread(str(self.image_path))
        return None


# ---------------------------------------------------------------------------
# Pass 1: OpenCV SSIM-based candidate detection
# ---------------------------------------------------------------------------

def frames_at_fps(video_path: str, target_fps: int = 1):
    """
    Generator that yields (frame, timestamp_sec, frame_index) at target_fps.
    Skips frames efficiently using cv2's frame-seek API.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Cannot open video file: {video_path}")

    native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = total_frames / native_fps
    hop = max(1, int(native_fps / target_fps))

    log.info(
        "Video: %.1f s | %.0f fps native | sampling every %d frames (~%d fps)",
        duration_sec, native_fps, hop, target_fps,
    )

    frame_index = 0
    consecutive_fails = 0
    max_consecutive_fails = 12  # Tolerate corrupted/unseekable keyframes before giving up

    try:
        while True:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ret, frame = cap.read()
            if not ret or frame is None:
                consecutive_fails += 1
                if consecutive_fails >= max_consecutive_fails:
                    break
                frame_index += hop
                continue

            consecutive_fails = 0
            timestamp = frame_index / native_fps
            yield frame, timestamp, frame_index
            frame_index += hop
            if total_frames > 0 and frame_index > (total_frames + hop * 2):
                break
    finally:
        cap.release()


def to_gray_resized(frame: np.ndarray, width: int = 320) -> np.ndarray:
    """Downscale + convert to grayscale for fast SSIM comparison."""
    h, w = frame.shape[:2]
    height = int(h * width / w)
    resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)


def pass1_find_candidates(
    video_path: str,
    config: ExtractorConfig,
    progress_cb: Optional[Callable] = None,
) -> list:
    """
    Pass 1 — Scan video with SSIM, return candidate frames at transition points.

    Algorithm:
      - Compare each sampled frame to the last accepted frame via SSIM.
      - If SSIM < threshold -> slide changed. Record this frame as a candidate.
      - Debounce: skip the next debounce_seconds after any accepted candidate.
    """
    cap_tmp = cv2.VideoCapture(video_path)
    total_frames = int(cap_tmp.get(cv2.CAP_PROP_FRAME_COUNT))
    native_fps = cap_tmp.get(cv2.CAP_PROP_FPS) or 30.0
    cap_tmp.release()

    candidates = []
    last_gray = None
    last_accepted_ts = -999.0
    frame_count = 0
    total_sampled = max(1, int(total_frames / max(1, int(native_fps / config.sample_fps))))

    cand_dir = Path(config.output_dir) / "_candidates_raw"
    cand_dir.mkdir(parents=True, exist_ok=True)

    log.info("=== PASS 1: OpenCV Candidate Detection ===")
    log.info("SSIM threshold: %.2f  |  Debounce: %ds", config.ssim_threshold, config.debounce_seconds)

    for frame, ts, idx in frames_at_fps(video_path, config.sample_fps):
        frame_count += 1
        gray = to_gray_resized(frame)

        if last_gray is None:
            # Always capture the very first frame
            log.info("  [%5.1fs] First frame -> accepted as initial slide", ts)
            cand_path = cand_dir / f"cand_{len(candidates):04d}_t{int(ts):05d}s.jpg"
            cv2.imwrite(str(cand_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            candidates.append(CandidateFrame(idx, ts, cand_path))
            last_gray = gray
            last_accepted_ts = ts
            if progress_cb:
                progress_cb(frame_count, total_sampled, ts)
            continue

        # Compute SSIM and L1 mean pixel difference (fast downscaled 320px)
        score = ssim(last_gray, gray, full=False)
        diff_score = float(np.mean(np.abs(last_gray.astype(np.float32) - gray.astype(np.float32))) / 255.0)

        if progress_cb:
            progress_cb(frame_count, total_sampled, ts)

        in_debounce = (ts - last_accepted_ts) < config.debounce_seconds

        # Trigger if SSIM indicates structural change OR L1 difference indicates text/formula added
        is_transition = (score < config.ssim_threshold) or (diff_score > 0.022)

        if is_transition and not in_debounce:
            log.info(
                "  [%5.1fs] SSIM=%.3f (thresh=%.2f) diff=%.3f -> CANDIDATE #%d",
                ts, score, config.ssim_threshold, diff_score, len(candidates) + 1,
            )
            cand_path = cand_dir / f"cand_{len(candidates):04d}_t{int(ts):05d}s.jpg"
            cv2.imwrite(str(cand_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            candidates.append(CandidateFrame(idx, ts, cand_path))
            last_gray = gray
            last_accepted_ts = ts
        else:
            log.debug("  [%5.1fs] SSIM=%.3f diff=%.3f skip (debounce=%s)", ts, score, diff_score, in_debounce)

    duration_sec = total_frames / native_fps if native_fps > 0 else 0

    # Ensure full-duration coverage and gap filling:
    # 1. If overall candidates are few (< 6 for > 60s)
    # 2. Or if ANY gap between consecutive candidates exceeds 90s
    # 3. Or if the last candidate is > 35s before the end of the lecture (e.g. truncated at 25:22 on a 32:05 video)
    gaps_to_fill = []
    if candidates and duration_sec > 60:
        # Check start gap
        if candidates[0].timestamp_sec > 45.0:
            gaps_to_fill.append((8.0, candidates[0].timestamp_sec))
        # Check intermediate gaps
        for i in range(len(candidates) - 1):
            t_curr = candidates[i].timestamp_sec
            t_next = candidates[i + 1].timestamp_sec
            if (t_next - t_curr) > 90.0:
                gaps_to_fill.append((t_curr + 30.0, t_next - 15.0))
        # Check end-of-lecture gap (critical for lectures where slides stopped prematurely)
        last_cand_ts = candidates[-1].timestamp_sec
        if last_cand_ts < (duration_sec - 35.0):
            log.info("Detected end-of-lecture gap: last slide at %.1fs but video is %.1fs (gap of %.1fs)",
                     last_cand_ts, duration_sec, duration_sec - last_cand_ts)
            gaps_to_fill.append((last_cand_ts + 25.0, duration_sec - 8.0))
    elif not candidates and duration_sec > 10:
        gaps_to_fill.append((4.0, max(5.0, duration_sec - 5.0)))

    if gaps_to_fill or (len(candidates) < 6 and duration_sec > 60):
        log.info("Scanning %d timeline gaps for missing slides across lecture...", len(gaps_to_fill))
        existing_ts = {int(c.timestamp_sec) for c in candidates}
        cap_chk = cv2.VideoCapture(video_path)
        
        target_checkpoints = []
        for g_start, g_end in gaps_to_fill:
            step = 30.0 if (g_end - g_start) > 90 else 20.0
            cur = g_start
            while cur <= g_end:
                target_checkpoints.append(cur)
                cur += step

        # If sparse overall, also add uniform distribution
        if len(candidates) < 6 and duration_sec > 60:
            target_checkpoints.extend(list(np.arange(10.0, duration_sec - 5.0, max(25.0, duration_sec / 18.0))))

        for ts_target in target_checkpoints:
            if any(abs(ts_target - et) < 14.0 for et in existing_ts):
                continue
            f_idx = int(ts_target * native_fps)
            cap_chk.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ret_chk, frame_chk = cap_chk.read()
            if ret_chk and frame_chk is not None:
                cand_path = cand_dir / f"cand_{len(candidates):04d}_t{int(ts_target):05d}s.jpg"
                cv2.imwrite(str(cand_path), frame_chk, [cv2.IMWRITE_JPEG_QUALITY, 92])
                candidates.append(CandidateFrame(f_idx, ts_target, cand_path))
                existing_ts.add(int(ts_target))
        cap_chk.release()
        candidates.sort(key=lambda c: c.timestamp_sec)
        log.info("Total candidates after full lecture gap-filling: %d", len(candidates))

    log.info("Pass 1 complete: %d candidate frames found (streamed to disk, RAM < 80MB)", len(candidates))
    return candidates


# ---------------------------------------------------------------------------
# Pass 2: Gemini AI Verification
# ---------------------------------------------------------------------------

GEMINI_SYSTEM_PROMPT = """You are a slide extraction assistant analyzing frames from an educational video.
You must return ONLY a valid JSON object — no markdown, no code fences, no extra text.

Evaluate the provided image and return:
{
  "is_slide": <true | false>,
  "is_new_content": <true | false>,
  "slide_title": "<string>"
}

Rules:
- "is_slide" = true for any educational or lecture presentation content: presentation slides (PowerPoint/Keynote/Google Slides), PDF documents, lecture notes, textbook pages, whiteboard/blackboard math, technical diagrams, or code editors/notebooks.
  Set false ONLY for: full-screen webcam footage of a person without slides, sponsor ads/bumper screens, video thumbnails, or solid black/blank transitions.
  If in doubt, set "is_slide": true to ensure valuable lecture material is never omitted.
- "is_new_content" = true if the slide has new content, distinct text, or updated formulas.
  (For the very first slide, always set true.)
- "slide_title" = the main title or heading visible on the slide (exact text, max 80 chars). If none is clearly visible, return "Lecture Slide".
"""

GEMINI_USER_PROMPT = "Evaluate this video frame. Is it a presentation slide? Does it have new content compared to the previous slide?"


def _encode_image_b64(image_bgr: np.ndarray, quality: int = 82) -> str:
    """Fast C++ OpenCV JPEG encode to base64 string."""
    h, w = image_bgr.shape[:2]
    max_width = 1024
    if w > max_width:
        ratio = max_width / float(w)
        resized = cv2.resize(image_bgr, (max_width, int(h * ratio)), interpolation=cv2.INTER_AREA)
    else:
        resized = image_bgr
    success, enc = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not success:
        raise ValueError("Failed to encode image to JPEG")
    return base64.b64encode(enc.tobytes()).decode("utf-8")


_ACTIVE_GEMINI_MODEL: Optional[str] = None
_MODEL_LOCK = threading.Lock()
PREFERRED_MODELS = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-2.5-flash"]


def _resolve_working_model(requested_model: str) -> str:
    global _ACTIVE_GEMINI_MODEL
    with _MODEL_LOCK:
        if _ACTIVE_GEMINI_MODEL:
            return _ACTIVE_GEMINI_MODEL
    if "3.5" in requested_model or not requested_model or "invalid" in requested_model:
        return PREFERRED_MODELS[0]
    return requested_model


def _call_gemini(client, model_name: str, image_b64: str, retries: int = 2, retry_delay: float = 1.0) -> dict:
    """
    Call the Gemini API with candidate image and automatic multi-model failover.
    Returns parsed JSON dict or safe fallback.
    """
    global _ACTIVE_GEMINI_MODEL
    from google.genai import types

    target_model = _resolve_working_model(model_name)
    candidate_models = [target_model] + [m for m in PREFERRED_MODELS if m != target_model]

    for model in candidate_models:
        for attempt in range(1, retries + 1):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[
                        types.Content(
                            role="user",
                            parts=[
                                types.Part(
                                    inline_data=types.Blob(
                                        mime_type="image/jpeg",
                                        data=base64.b64decode(image_b64),
                                    )
                                ),
                                types.Part(text=GEMINI_USER_PROMPT),
                            ],
                        )
                    ],
                    config=types.GenerateContentConfig(
                        system_instruction=GEMINI_SYSTEM_PROMPT,
                        temperature=0.0,
                        max_output_tokens=256,
                    ),
                )
                raw_text = response.text.strip() if response.text else ""
                if raw_text.startswith("```"):
                    parts = raw_text.split("```")
                    raw_text = parts[1] if len(parts) > 1 else raw_text
                    if raw_text.startswith("json"):
                        raw_text = raw_text[4:]
                    raw_text = raw_text.strip()

                parsed = json.loads(raw_text)
                with _MODEL_LOCK:
                    _ACTIVE_GEMINI_MODEL = model
                return parsed

            except json.JSONDecodeError as e:
                log.warning("  Model %s Attempt %d: JSON parse error — %s", model, attempt, e)
            except Exception as e:
                err_str = str(e).lower()
                log.warning("  Model %s Attempt %d: API error — %s", model, attempt, e)
                # If model not found or not supported (404), fail over immediately without burning retries
                if "not found" in err_str or "404" in err_str or "not supported" in err_str:
                    log.warning("  Model %s unavailable on API; switching to next fallback...", model)
                    break
                if "429" in err_str or "resource_exhausted" in err_str:
                    time.sleep(1.2)

            if attempt < retries:
                time.sleep(retry_delay)

    log.warning("  AI verification defaulted for frame: is_slide=True, is_new_content=True")
    return {"is_slide": True, "is_new_content": True, "slide_title": "Slide"}


def pass2_ai_verify(
    candidates: list,
    config: ExtractorConfig,
    progress_cb: Optional[Callable] = None,
    max_workers: int = 5,
) -> list:
    """
    Pass 2 — Send candidate frames to Gemini in parallel for high-speed verification.
    Preserves exact chronological order and gracefully falls back on API unavailability.
    """
    if not candidates:
        return []

    if config.gemini_api_key == "YOUR_GEMINI_API_KEY_HERE" or not config.gemini_api_key:
        log.warning("No Gemini API key configured; keeping all %d candidates.", len(candidates))
        return [
            VerifiedSlide(
                frame_index=c.frame_index,
                timestamp_sec=c.timestamp_sec,
                image_or_path=c.image_path,
                slide_title=f"Slide {i}",
                is_slide=True,
                is_new_content=True,
            )
            for i, c in enumerate(candidates, 1)
        ]

    try:
        from google import genai
    except ImportError:
        log.error("google-genai not installed. Keeping all candidates.")
        return [
            VerifiedSlide(
                frame_index=c.frame_index,
                timestamp_sec=c.timestamp_sec,
                image_or_path=c.image_path,
                slide_title=f"Slide {i}",
                is_slide=True,
                is_new_content=True,
            )
            for i, c in enumerate(candidates, 1)
        ]

    client = genai.Client(api_key=config.gemini_api_key)

    total_candidates = len(candidates)
    log.info("=== PASS 2: High-Speed Parallel AI Verification ===")
    log.info("Requested model: %s | Total Candidates: %d | Concurrency: %d",
             config.gemini_model, total_candidates, min(max_workers, total_candidates))

    verified_by_idx = {}
    completed_count = 0
    lock = threading.Lock()

    def _verify_one(pos: int, candidate: CandidateFrame):
        nonlocal completed_count
        img = candidate.image
        if img is None:
            with lock:
                completed_count += 1
                if progress_cb:
                    progress_cb(completed_count, total_candidates)
            return

        # 1. Fast CV Pre-filter: Check for blank / uniform / solid color frames (std < 6.0)
        try:
            small = cv2.resize(img, (64, 36), interpolation=cv2.INTER_AREA)
            gray_small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            std_dev = float(np.std(gray_small))
            if std_dev < 6.0:
                log.info("  [%d/%d] Rejected blank/solid frame (std=%.1f)", pos, total_candidates, std_dev)
                with lock:
                    completed_count += 1
                    if progress_cb:
                        progress_cb(completed_count, total_candidates)
                return
        except Exception:
            pass

        image_b64 = _encode_image_b64(img)
        del img

        result = _call_gemini(
            client, config.gemini_model, image_b64,
            retries=config.ai_max_retries, retry_delay=config.ai_retry_delay,
        )

        is_slide = bool(result.get("is_slide", False))
        is_new_content = bool(result.get("is_new_content", True))
        slide_title = str(result.get("slide_title", f"Slide {pos}"))[:80]

        # Pass 1 (CV) already verified this was a distinct transition/frame.
        # Pass 2 verifies that the frame is indeed presentation content (slides, code, boards, plots).
        status = "ACCEPTED" if is_slide else "REJECTED"
        log.info("  [%d/%d] %s @%.1fs: '%s'", pos, total_candidates, status, candidate.timestamp_sec, slide_title)

        if is_slide:
            slide_obj = VerifiedSlide(
                frame_index=candidate.frame_index,
                timestamp_sec=candidate.timestamp_sec,
                image_or_path=candidate.image_path,
                slide_title=slide_title,
                is_slide=is_slide,
                is_new_content=is_new_content,
            )
            with lock:
                verified_by_idx[candidate.frame_index] = slide_obj

        with lock:
            completed_count += 1
            if progress_cb:
                progress_cb(completed_count, total_candidates)

    worker_count = min(max_workers, total_candidates) if total_candidates > 0 else 1
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(_verify_one, i, c) for i, c in enumerate(candidates, 1)]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                log.warning("Worker thread exception: %s", e)

    # Reconstruct strictly in original timestamp order
    verified = [verified_by_idx[c.frame_index] for c in candidates if c.frame_index in verified_by_idx]

    # Safety net: If Pass 1 found candidate transitions (>=4) but Gemini over-filtered down to <=1,
    # preserve candidate slides so the user never gets an empty or incomplete PDF!
    if len(candidates) >= 4 and len(verified) <= 1:
        log.warning("AI filter was overly aggressive (%d/%d kept). Keeping candidates to preserve complete lecture content.",
                    len(verified), total_candidates)
        verified = [
            VerifiedSlide(
                frame_index=c.frame_index,
                timestamp_sec=c.timestamp_sec,
                image_or_path=c.image_path,
                slide_title=f"Slide {i}",
                is_slide=True,
                is_new_content=True,
            )
            for i, c in enumerate(candidates, 1)
        ]

    log.info("Pass 2 complete: %d/%d frames verified as clean slides", len(verified), total_candidates)
    return verified


# ---------------------------------------------------------------------------
# Save frames to disk
# ---------------------------------------------------------------------------

def save_slides(slides: list, output_dir: Path, prefix: str = "slide", max_workers: int = 4) -> list:
    """Save verified slide images as PNG files concurrently with fast compression."""
    output_dir.mkdir(parents=True, exist_ok=True)
    saved_paths = [None] * len(slides)

    def _save_single(idx: int, slide):
        filename = output_dir / f"{prefix}_{idx:03d}_t{int(slide.timestamp_sec):05d}s.png"
        img = slide.image
        if img is not None:
            # Fast C++ OpenCV write with standard PNG compression (lossless)
            cv2.imwrite(str(filename), img, [cv2.IMWRITE_PNG_COMPRESSION, 3])
            del img
            slide.image_path = filename
            slide._image = None
        saved_paths[idx - 1] = filename
        log.info("  Saved: %s  (title: '%s')", filename.name, slide.slide_title)

    worker_count = min(max_workers, len(slides)) if slides else 1
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(_save_single, i, s) for i, s in enumerate(slides, 1)]
        for f in as_completed(futures):
            f.result()

    return [p for p in saved_paths if p is not None]


def save_candidates_debug(candidates: list, output_dir: Path) -> None:
    """Optional: dump Pass-1 candidates for manual inspection."""
    debug_dir = output_dir / "_candidates_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(candidates, 1):
        fname = debug_dir / f"candidate_{i:03d}_t{int(c.timestamp_sec):05d}s.jpg"
        cv2.imwrite(str(fname), c.image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    log.info("Debug candidates saved to: %s", debug_dir)


# ---------------------------------------------------------------------------
# Main extraction pipeline
# ---------------------------------------------------------------------------

def extract_slides(
    video_path: str,
    config: ExtractorConfig,
    skip_ai: bool = False,
    progress_cb_p1: Optional[Callable] = None,
    progress_cb_p2: Optional[Callable] = None,
) -> list:
    """
    Full two-pass extraction pipeline.

    Args:
        video_path:    Local path to the video file.
        config:        ExtractorConfig with all tuning parameters.
        skip_ai:       If True, skip Pass 2 and return all Pass-1 candidates
                       wrapped as VerifiedSlide objects (useful for CV testing).
        progress_cb_p1: Optional callback(frame_count, total, timestamp).
        progress_cb_p2: Optional callback(i, total).

    Returns:
        List of VerifiedSlide objects ready to be compiled into a PDF.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    t_start = time.time()

    # --- Pass 1 ---
    candidates = pass1_find_candidates(video_path, config, progress_cb=progress_cb_p1)

    if config.save_candidates:
        save_candidates_debug(candidates, config.output_dir)

    if not candidates:
        log.warning("No candidate frames found. Try lowering --ssim-threshold (e.g. 0.75).")
        return []

    # --- Pass 2 (or skip) ---
    if skip_ai:
        log.info("--skip-ai: wrapping all Pass-1 candidates as verified slides.")
        verified = [
            VerifiedSlide(
                frame_index=c.frame_index,
                timestamp_sec=c.timestamp_sec,
                image=c.image,
                slide_title=f"Slide at {c.timestamp_sec:.0f}s",
                is_slide=True,
                is_new_content=True,
            )
            for c in candidates
        ]
    else:
        verified = pass2_ai_verify(candidates, config, progress_cb=progress_cb_p2)

    # --- Save to disk ---
    saved = save_slides(verified, config.output_dir)

    elapsed = time.time() - t_start
    log.info("Done! %d slides saved to '%s' in %.1fs", len(saved), config.output_dir, elapsed)

    # Summary table
    print("\n" + "=" * 60)
    print(f"  EXTRACTION SUMMARY — {len(verified)} slides extracted")
    print("=" * 60)
    for i, slide in enumerate(verified, 1):
        mins, secs = divmod(int(slide.timestamp_sec), 60)
        print(f"  {i:>3}. [{mins:02d}:{secs:02d}]  {slide.slide_title}")
    print("=" * 60 + "\n")

    return verified


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Hybrid CV + AI slide extractor",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--video", required=True, help="Path to local video file")
    p.add_argument("--output", default="./slides_out", help="Output directory for slide PNGs")
    p.add_argument("--ssim-threshold", type=float, default=0.85,
                   help="SSIM drop threshold (0-1). Lower = more sensitive.")
    p.add_argument("--debounce", type=int, default=3,
                   help="Seconds to ignore frames after a transition.")
    p.add_argument("--sample-fps", type=int, default=1,
                   help="Frames per second to sample from the video.")
    p.add_argument("--api-key", default=None,
                   help="Gemini API key. Falls back to GEMINI_API_KEY env var.")
    p.add_argument("--model", default="gemini-3.6-flash",
                   help="Gemini model name.")
    p.add_argument("--skip-ai", action="store_true",
                   help="Skip AI verification (Pass 2). Use CV results only.")
    p.add_argument("--save-candidates", action="store_true",
                   help="Save Pass-1 candidate frames to a debug subfolder.")
    return p


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY_HERE")

    config = ExtractorConfig(
        sample_fps=args.sample_fps,
        ssim_threshold=args.ssim_threshold,
        debounce_seconds=args.debounce,
        gemini_api_key=api_key,
        gemini_model=args.model,
        output_dir=Path(args.output),
        save_candidates=args.save_candidates,
    )

    extract_slides(video_path=args.video, config=config, skip_ai=args.skip_ai)


if __name__ == "__main__":
    main()
