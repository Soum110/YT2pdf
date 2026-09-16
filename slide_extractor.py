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
import time
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
# Configuration dataclass
# ---------------------------------------------------------------------------
@dataclass
class ExtractorConfig:
    """All tunable parameters in one place."""

    # --- Pass 1: OpenCV ---
    sample_fps: int = 1                  # Sample 1 frame per second
    ssim_threshold: float = 0.85         # Drop below this -> slide transition
    debounce_seconds: int = 3            # Ignore frames for N seconds after a transition

    # --- Pass 2: Gemini AI ---
    gemini_api_key: str = "YOUR_GEMINI_API_KEY_HERE"   # <- Replace this
    gemini_model: str = "gemini-3.5-flash-lite"         # Fast, generous free tier
    ai_max_retries: int = 3
    ai_retry_delay: float = 2.0          # Seconds between retries

    # --- Output ---
    output_dir: Path = Path("./slides_out")
    save_candidates: bool = False        # Save Pass-1 candidates (debug)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class CandidateFrame:
    """A frame flagged by Pass 1 (OpenCV)."""
    frame_index: int
    timestamp_sec: float
    image: np.ndarray  # Raw BGR numpy array


@dataclass
class VerifiedSlide:
    """A frame that passed both Pass 1 and Pass 2 (AI)."""
    frame_index: int
    timestamp_sec: float
    image: np.ndarray
    slide_title: str
    is_slide: bool
    is_new_content: bool


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
    try:
        while True:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ret, frame = cap.read()
            if not ret:
                break
            timestamp = frame_index / native_fps
            yield frame, timestamp, frame_index
            frame_index += hop
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

    log.info("=== PASS 1: OpenCV Candidate Detection ===")
    log.info("SSIM threshold: %.2f  |  Debounce: %ds", config.ssim_threshold, config.debounce_seconds)

    for frame, ts, idx in frames_at_fps(video_path, config.sample_fps):
        frame_count += 1
        gray = to_gray_resized(frame)

        if last_gray is None:
            # Always capture the very first frame
            log.info("  [%5.1fs] First frame -> accepted as initial slide", ts)
            candidates.append(CandidateFrame(idx, ts, frame.copy()))
            last_gray = gray
            last_accepted_ts = ts
            if progress_cb:
                progress_cb(frame_count, total_sampled, ts)
            continue

        # Compute SSIM (fast because we downscaled to 320px wide)
        score, _ = ssim(last_gray, gray, full=True)

        if progress_cb:
            progress_cb(frame_count, total_sampled, ts)

        in_debounce = (ts - last_accepted_ts) < config.debounce_seconds

        if score < config.ssim_threshold and not in_debounce:
            log.info(
                "  [%5.1fs] SSIM=%.3f < %.2f -> CANDIDATE #%d",
                ts, score, config.ssim_threshold, len(candidates) + 1,
            )
            candidates.append(CandidateFrame(idx, ts, frame.copy()))
            last_gray = gray
            last_accepted_ts = ts
        else:
            log.debug("  [%5.1fs] SSIM=%.3f  skip (debounce=%s)", ts, score, in_debounce)

    log.info("Pass 1 complete: %d candidate frames found", len(candidates))
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
- "is_slide" = true ONLY if the frame is clearly a presentation slide (PowerPoint, Keynote, Google Slides, whiteboard, PDF slide, etc.).
  Set false for: webcam footage of a person, desktop/file explorer views, video thumbnails, or plain screen transitions.
- "is_new_content" = true if the slide has new printed/digital text or graphics not present in the previous slide.
  Set false if it is clearly the SAME slide with only handwritten pen/marker annotations added on top.
  (For the very first slide, always set true.)
- "slide_title" = the main title text on the slide (exact text, max 80 chars). If none is visible, return "Untitled Slide".
"""

GEMINI_USER_PROMPT = "Evaluate this video frame. Is it a presentation slide? Does it have new content compared to the previous slide?"


def _encode_image_b64(image_bgr: np.ndarray, quality: int = 85) -> str:
    """Encode a BGR numpy array to base64 JPEG string."""
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)
    # Downscale large frames to save API bandwidth (max 1024px wide)
    max_width = 1024
    if pil_img.width > max_width:
        ratio = max_width / pil_img.width
        pil_img = pil_img.resize(
            (max_width, int(pil_img.height * ratio)), Image.LANCZOS
        )
    buf = io.BytesIO()
    pil_img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _call_gemini(client, model_name: str, image_b64: str, retries: int = 3, retry_delay: float = 2.0) -> dict:
    """
    Call the Gemini API with the candidate image.
    Returns the parsed JSON dict from the model or raises on failure.
    """
    from google.genai import types

    for attempt in range(1, retries + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
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
            raw_text = response.text.strip()

            # Strip any accidental markdown fences
            if raw_text.startswith("```"):
                parts = raw_text.split("```")
                raw_text = parts[1] if len(parts) > 1 else raw_text
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
                raw_text = raw_text.strip()

            return json.loads(raw_text)

        except json.JSONDecodeError as e:
            log.warning("  Attempt %d: JSON parse error — %s", attempt, e)
        except Exception as e:
            log.warning("  Attempt %d: API error — %s", attempt, e)

        if attempt < retries:
            time.sleep(retry_delay)

    log.error("  All retries failed. Defaulting to: is_slide=True, is_new_content=True")
    return {"is_slide": True, "is_new_content": True, "slide_title": "Unknown Slide"}


def pass2_ai_verify(
    candidates: list,
    config: ExtractorConfig,
    progress_cb: Optional[Callable] = None,
) -> list:
    """
    Pass 2 — Send each candidate frame to Gemini for verification.
    Returns only frames where both is_slide and is_new_content are True.
    """
    if config.gemini_api_key == "YOUR_GEMINI_API_KEY_HERE":
        log.error("No Gemini API key set!")
        log.error("  Option 1: export GEMINI_API_KEY='your_key' and re-run.")
        log.error("  Option 2: pass --api-key YOUR_KEY on the command line.")
        log.error("  Get a free key at: https://aistudio.google.com/app/apikey")
        sys.exit(1)

    try:
        from google import genai
    except ImportError:
        log.error("google-genai not installed. Run: pip install google-genai")
        sys.exit(1)

    client = genai.Client(api_key=config.gemini_api_key)

    log.info("=== PASS 2: Gemini AI Verification ===")
    log.info("Model: %s  |  Candidates to verify: %d", config.gemini_model, len(candidates))

    verified = []

    for i, candidate in enumerate(candidates, 1):
        log.info("  [%d/%d] Verifying frame @%.1fs ...", i, len(candidates), candidate.timestamp_sec)

        image_b64 = _encode_image_b64(candidate.image)
        result = _call_gemini(
            client, config.gemini_model, image_b64,
            retries=config.ai_max_retries, retry_delay=config.ai_retry_delay,
        )

        is_slide = bool(result.get("is_slide", False))
        is_new_content = bool(result.get("is_new_content", False))
        slide_title = str(result.get("slide_title", "Untitled Slide"))[:80]

        status = "ACCEPTED" if (is_slide and is_new_content) else "REJECTED"
        reason = (
            "not a slide" if not is_slide
            else "annotation-only (no new content)" if not is_new_content
            else ""
        )
        log.info(
            "    [%s] is_slide=%s | is_new_content=%s | title='%s'%s",
            status, is_slide, is_new_content, slide_title,
            f" -> {reason}" if reason else "",
        )

        if is_slide and is_new_content:
            verified.append(
                VerifiedSlide(
                    frame_index=candidate.frame_index,
                    timestamp_sec=candidate.timestamp_sec,
                    image=candidate.image,
                    slide_title=slide_title,
                    is_slide=is_slide,
                    is_new_content=is_new_content,
                )
            )

        if progress_cb:
            progress_cb(i, len(candidates))

        # Courtesy pause between API calls (rate limit)
        if i < len(candidates):
            time.sleep(0.3)

    log.info("Pass 2 complete: %d/%d frames verified as unique slides", len(verified), len(candidates))
    return verified


# ---------------------------------------------------------------------------
# Save frames to disk
# ---------------------------------------------------------------------------

def save_slides(slides: list, output_dir: Path, prefix: str = "slide") -> list:
    """Save verified slide images as PNG files. Returns list of saved paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    saved_paths = []

    for i, slide in enumerate(slides, 1):
        filename = output_dir / f"{prefix}_{i:03d}_t{int(slide.timestamp_sec):05d}s.png"
        rgb = cv2.cvtColor(slide.image, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        pil_img.save(str(filename), format="PNG", optimize=True)
        saved_paths.append(filename)
        log.info("  Saved: %s  (title: '%s')", filename.name, slide.slide_title)

    return saved_paths


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
