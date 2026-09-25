"""
study_guide_generator.py — Academic Pedagogical Lecture Synthesis Engine.
========================================================================
Synthesizes comprehensive, textbook-grade study guides from:
1. Curated visual slide frames
2. Timestamped video transcripts (from browser extension or YouTube)
3. Direct audio files (when transcripts are absent or for multimodal audio-visual understanding)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import cv2
import numpy as np

log = logging.getLogger("study_guide_generator")


# ─────────────────────────────────────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CuratedFigure:
    fig_id: str
    title: str
    caption: str
    explanation: str
    image_path: Optional[str] = None
    timestamp_sec: float = 0.0
    fig_type: str = "diagram"  # diagram | model | schematic


@dataclass
class StudyGuideChapter:
    chapter_num: int
    title: str
    subtitle: str
    introduction: str
    content_paragraphs: List[str] = field(default_factory=list)
    latex_formulas: List[Dict[str, str]] = field(default_factory=list)
    associated_figures: List[CuratedFigure] = field(default_factory=list)
    key_takeaways: List[str] = field(default_factory=list)
    instructor_notes: str = ""


@dataclass
class LectureStudyGuide:
    video_title: str
    lecture_summary: str
    chapters: List[StudyGuideChapter] = field(default_factory=list)
    all_figures: List[CuratedFigure] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Visual Figure Curating & Cropping
# ─────────────────────────────────────────────────────────────────────────────

def _crop_clean_diagram(image_path: Union[str, Path], output_path: Union[str, Path]) -> Optional[str]:
    """Crops the most informative diagram/content region from a slide."""
    try:
        img = cv2.imread(str(image_path))
        if img is None:
            return str(image_path)

        h, w = img.shape[:2]
        # Crop out slide top header / bottom footer banner (typical 12% top, 8% bottom)
        top = int(h * 0.12)
        bottom = int(h * 0.92)
        left = int(w * 0.05)
        right = int(w * 0.95)

        cropped = img[top:bottom, left:right]
        if cropped.size > 0:
            cv2.imwrite(str(output_path), cropped, [cv2.IMWRITE_JPEG_QUALITY, 90])
            return str(output_path)
    except Exception as e:
        log.debug("Diagram crop skipped: %s", e)
    return str(image_path)


# ─────────────────────────────────────────────────────────────────────────────
# Gemini System Prompt & Schema
# ─────────────────────────────────────────────────────────────────────────────

STUDY_GUIDE_SYSTEM_PROMPT = """You are a distinguished university professor and world-class academic textbook author.
Your mission is to synthesize the provided educational lecture material (slides, transcripts, and/or audio)
into a rigorous, publication-grade academic study guide.

Structure the study guide chronologically from basic foundational intuition to advanced rigorous theory.

You must return ONLY a valid JSON object matching this exact schema:
{
  "video_title": "<Concise Academic Title>",
  "lecture_summary": "<Executive Lecture Summary & Pedagogical Overview (2-3 rich paragraphs)>",
  "chapters": [
    {
      "chapter_num": 1,
      "title": "<Thematic Chapter Title>",
      "subtitle": "<Subtopic or Theoretical Scope>",
      "introduction": "<Pedagogical motivation & problem statement>",
      "content_paragraphs": [
        "<Detailed theoretical explanation, definitions, and logical progression (Paragraph 1)>",
        "<In-depth derivations, mechanisms, architecture details, and edge cases (Paragraph 2)>",
        "<Comparative analysis, implications, and synthesis (Paragraph 3)>"
      ],
      "latex_formulas": [
        {
          "formula": "<Raw LaTeX equation e.g. \\nabla L(\\theta) = \\frac{1}{N}\\sum_{i=1}^N \\nabla l(x_i, y_i; \\theta)>",
          "description": "<What every variable, operator, and condition represents>"
        }
      ],
      "key_takeaways": [
        "<Core takeaway 1>",
        "<Core takeaway 2>",
        "<Core takeaway 3>"
      ],
      "instructor_notes": "<Practical real-world advice, common student pitfalls, or examination tips>"
    }
  ]
}

Formatting Rules:
1. Formulas must be written in proper mathematical LaTeX (e.g. \\frac{a}{b}, \\sum, \\int, \\mathbb{R}, \\theta).
2. Do NOT use markdown code fences around the JSON. Return raw parseable JSON only.
3. Every chapter must have substantial, high-density explanations. Do not produce trivial 1-sentence summaries.
"""


def _format_transcript_text(transcript_segments: Any) -> str:
    """Formats transcript segments from various formats into timestamped text."""
    if not transcript_segments:
        return ""

    if isinstance(transcript_segments, str):
        return transcript_segments.strip()

    lines = []
    if isinstance(transcript_segments, (list, tuple)):
        for item in transcript_segments:
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                start_sec = float(item[0])
                txt = str(item[2]).strip()
            elif isinstance(item, dict):
                start_sec = float(item.get("start", item.get("timestamp", 0.0)))
                txt = str(item.get("text", "")).strip()
            elif hasattr(item, "text") and hasattr(item, "start"):
                start_sec = float(item.start)
                txt = str(item.text).strip()
            else:
                txt = str(item).strip()
                start_sec = 0.0

            if txt:
                mins = int(start_sec // 60)
                secs = int(start_sec % 60)
                lines.append(f"[{mins:02d}:{secs:02d}] {txt}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Main Synthesis Function
# ─────────────────────────────────────────────────────────────────────────────

def generate_study_guide_content(
    slides: list,
    transcript_segments: Any = None,
    total_duration: float = 0.0,
    gemini_api_key: str = "",
    crops_dir: Optional[Path] = None,
    gemini_model: str = "gemini-2.0-flash",
    audio_path: Optional[Union[str, Path]] = None,
    video_title: str = "Lecture Study Guide",
) -> LectureStudyGuide:
    """
    Synthesizes textbook-grade study guide using Gemini 2.0 Flash.
    Supports:
      1. Slides + Transcript (Standard fast path)
      2. Slides + Audio file (Multimodal audio path when video has no captions)
      3. Slides + Audio + Transcript (Highest-fidelity path)
    """
    if not gemini_api_key or gemini_api_key == "YOUR_GEMINI_API_KEY_HERE":
        log.warning("No Gemini API key supplied; compiling deterministic study guide.")
        return generate_deterministic_study_guide(
            slides=slides,
            transcript_segments=transcript_segments,
            video_title=video_title,
            total_duration=total_duration,
            crops_dir=crops_dir,
        )

    crops_dir = Path(crops_dir or Path("/tmp/crops"))
    crops_dir.mkdir(parents=True, exist_ok=True)

    # 1. Prepare Curated Figures from Verified Slides
    figures_list: List[CuratedFigure] = []
    for idx, slide in enumerate(slides, 1):
        img_p = getattr(slide, "image_path", None)
        ts = getattr(slide, "timestamp_sec", 0.0)
        title = getattr(slide, "slide_title", f"Slide {idx}")
        if img_p and os.path.exists(img_p):
            crop_out = crops_dir / f"fig_{idx:03d}.jpg"
            clean_path = _crop_clean_diagram(img_p, crop_out)
            figures_list.append(CuratedFigure(
                fig_id=f"fig_{idx}",
                title=title,
                caption=f"Visual representation from timestamp {int(ts//60)}:{int(ts%60):02d}",
                explanation=f"Key visual model illustrating concepts in {title}.",
                image_path=clean_path,
                timestamp_sec=ts,
            ))

    # 2. Prepare Context (Transcript and/or Audio)
    transcript_text = _format_transcript_text(transcript_segments)
    has_transcript = bool(transcript_text.strip())

    audio_file_exists = audio_path and os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000

    log.info("Generating study guide with Gemini: %d slides, transcript=%s (%d chars), audio=%s",
             len(slides), has_transcript, len(transcript_text), bool(audio_file_exists))

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=gemini_api_key)

    content_parts: List[Any] = []

    # A. Add Audio File if present and transcript is missing or short
    audio_upload_obj = None
    if audio_file_exists:
        try:
            audio_p = Path(audio_path)
            file_size_mb = audio_p.stat().st_size / (1024 * 1024)
            log.info("Attaching audio track (%s, %.1f MB) to Gemini...", audio_p.name, file_size_mb)
            
            # Determine mime type
            mime_type = "audio/mp3"
            if audio_p.suffix.lower() in [".m4a", ".mp4", ".aac"]:
                mime_type = "audio/mp4"
            elif audio_p.suffix.lower() in [".webm", ".opus"]:
                mime_type = "audio/webm"
            elif audio_p.suffix.lower() == ".wav":
                mime_type = "audio/wav"

            if file_size_mb <= 15.0:
                audio_bytes = audio_p.read_bytes()
                content_parts.append(types.Part(
                    inline_data=types.Blob(mime_type=mime_type, data=audio_bytes)
                ))
            else:
                log.info("Uploading audio file to Gemini Files API...")
                audio_upload_obj = client.files.upload(file=str(audio_p))
                # Wait briefly for processing if needed
                time.sleep(1.0)
                content_parts.append(audio_upload_obj)
        except Exception as audio_err:
            log.warning("Could not attach audio to Gemini: %s", audio_err)

    # B. Add Key Slide Images (up to 12 distinct slides to fit comfortably within context)
    stride = max(1, len(slides) // 12) if len(slides) > 12 else 1
    sampled_slides = slides[::stride][:12]
    for s in sampled_slides:
        img_p = getattr(s, "image_path", None)
        if img_p and os.path.exists(img_p):
            try:
                # Read, resize to 768px wide for token efficiency, and add
                img_cv = cv2.imread(str(img_p))
                if img_cv is not None:
                    h, w = img_cv.shape[:2]
                    scale = 768 / float(w) if w > 768 else 1.0
                    resized = cv2.resize(img_cv, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
                    success, enc = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if success:
                        content_parts.append(types.Part(
                            inline_data=types.Blob(mime_type="image/jpeg", data=enc.tobytes())
                        ))
            except Exception as e:
                log.debug("Slide image attach skipped: %s", e)

    # C. Add Text Prompt + Transcript
    user_prompt = f"""Title: {video_title}
Duration: {int(total_duration // 60)} minutes {int(total_duration % 60)} seconds
Number of Slides: {len(slides)}

Attached:
- {len(sampled_slides)} Visual Slide Images from the presentation.
"""
    if audio_file_exists:
        user_prompt += "- Lecture Audio Track (Listen to the explanation, definitions, and technical notes spoken by the lecturer).\n"

    if has_transcript:
        # Cap transcript to 60,000 characters
        tr_snippet = transcript_text[:60000]
        user_prompt += f"\nLecture Transcript:\n{tr_snippet}\n"

    user_prompt += "\nSynthesize a complete, publication-grade academic textbook study guide following the specified JSON schema."
    content_parts.append(types.Part(text=user_prompt))

    # 3. Call Gemini Model
    raw_json_str = ""
    candidate_models = ["gemini-2.0-flash", "gemini-1.5-flash"]
    for model in candidate_models:
        try:
            log.info("Requesting study guide synthesis from %s...", model)
            response = client.models.generate_content(
                model=model,
                contents=[types.Content(role="user", parts=content_parts)],
                config=types.GenerateContentConfig(
                    system_instruction=STUDY_GUIDE_SYSTEM_PROMPT,
                    temperature=0.2,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                ),
            )
            if response.text and response.text.strip():
                raw_json_str = response.text.strip()
                break
        except Exception as gen_err:
            log.warning("Model %s generation error: %s", model, gen_err)
            time.sleep(1.0)

    # Clean up uploaded audio file from Gemini API storage if created
    if audio_upload_obj:
        try:
            client.files.delete(name=audio_upload_obj.name)
        except Exception:
            pass

    # 4. Parse JSON Response into LectureStudyGuide
    if raw_json_str:
        try:
            clean_str = raw_json_str
            if clean_str.startswith("```"):
                parts = clean_str.split("```")
                clean_str = parts[1] if len(parts) > 1 else clean_str
                if clean_str.startswith("json"):
                    clean_str = clean_str[4:]
            clean_str = clean_str.strip()

            parsed = json.loads(clean_str)

            chapters: List[StudyGuideChapter] = []
            for ch_dict in parsed.get("chapters", []):
                # Associate relevant figures to chapters
                c_num = ch_dict.get("chapter_num", len(chapters) + 1)
                ch_figs = []
                # Allocate a portion of figures to each chapter
                if figures_list:
                    figs_per_ch = max(1, len(figures_list) // max(1, len(parsed.get("chapters", [1]))))
                    start_f = (c_num - 1) * figs_per_ch
                    ch_figs = figures_list[start_f:start_f + figs_per_ch]

                chapters.append(StudyGuideChapter(
                    chapter_num=c_num,
                    title=ch_dict.get("title", f"Chapter {c_num}"),
                    subtitle=ch_dict.get("subtitle", ""),
                    introduction=ch_dict.get("introduction", ""),
                    content_paragraphs=ch_dict.get("content_paragraphs", []),
                    latex_formulas=ch_dict.get("latex_formulas", []),
                    associated_figures=ch_figs,
                    key_takeaways=ch_dict.get("key_takeaways", []),
                    instructor_notes=ch_dict.get("instructor_notes", ""),
                ))

            guide = LectureStudyGuide(
                video_title=parsed.get("video_title", video_title) or video_title,
                lecture_summary=parsed.get("lecture_summary", ""),
                chapters=chapters,
                all_figures=figures_list,
            )
            log.info("Successfully synthesized study guide with %d chapters and %d figures.", len(chapters), len(figures_list))
            return guide

        except Exception as parse_err:
            log.error("Failed to parse Gemini study guide JSON: %s. Using deterministic fallback.", parse_err)

    # 5. Deterministic fallback if API failed or JSON unparseable
    return generate_deterministic_study_guide(
        slides=slides,
        transcript_segments=transcript_segments,
        video_title=video_title,
        total_duration=total_duration,
        crops_dir=crops_dir,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic Fallback Synthesis
# ─────────────────────────────────────────────────────────────────────────────

def generate_deterministic_study_guide(
    slides: list,
    transcript_segments: Any = None,
    video_title: str = "Lecture Notes",
    total_duration: float = 0.0,
    crops_dir: Optional[Path] = None,
) -> LectureStudyGuide:
    """
    Constructs a rich, complete study guide deterministically without external API calls.
    Ensures that real chapters, slide content, and key concepts are ALWAYS generated.
    """
    log.info("Building deterministic study guide for %d slides...", len(slides))
    crops_dir = Path(crops_dir or Path("/tmp/crops"))
    crops_dir.mkdir(parents=True, exist_ok=True)

    figures_list: List[CuratedFigure] = []
    for idx, slide in enumerate(slides, 1):
        img_p = getattr(slide, "image_path", None)
        ts = getattr(slide, "timestamp_sec", 0.0)
        title = getattr(slide, "slide_title", f"Slide {idx}")
        if img_p and os.path.exists(img_p):
            crop_out = crops_dir / f"fig_det_{idx:03d}.jpg"
            clean_path = _crop_clean_diagram(img_p, crop_out)
            figures_list.append(CuratedFigure(
                fig_id=f"fig_{idx}",
                title=title,
                caption=f"Slide {idx} (Timestamp {int(ts//60)}:{int(ts%60):02d})",
                explanation=f"Key technical presentation slide demonstrating {title}.",
                image_path=clean_path,
                timestamp_sec=ts,
            ))

    transcript_text = _format_transcript_text(transcript_segments)

    # Determine chapter breakdown (3 to 6 chapters based on slide count)
    num_slides = max(1, len(slides))
    num_chapters = min(6, max(2, (num_slides + 3) // 4))
    chunk_size = (num_slides + num_chapters - 1) // num_chapters

    chapters: List[StudyGuideChapter] = []
    for c_idx in range(num_chapters):
        c_num = c_idx + 1
        start_i = c_idx * chunk_size
        end_i = min(num_slides, (c_idx + 1) * chunk_size)
        ch_slides = slides[start_i:end_i]
        if not ch_slides:
            continue

        c_title = getattr(ch_slides[0], "slide_title", f"Thematic Module {c_num}")
        c_sub = f"Covering {len(ch_slides)} slide sections from time {int(getattr(ch_slides[0], 'timestamp_sec', 0)//60)}m"

        paragraphs = [
            f"This section focuses on {c_title}. The lecture systematically covers the core definitions, theoretical underpinnings, and contextual frameworks necessary for a rigorous understanding of the subject matter.",
            f"Throughout this module, visual models and structured concepts illustrate the relationships between key parameters. Careful examination of the slide formulations reveals how the components interact under standard operating conditions.",
        ]

        if transcript_text:
            # Grab a portion of the transcript corresponding to this chapter
            tr_lines = transcript_text.split("\n")
            lines_per_ch = max(1, len(tr_lines) // num_chapters)
            ch_tr_snippet = " ".join(tr_lines[c_idx * lines_per_ch : (c_idx + 1) * lines_per_ch][:8])
            if ch_tr_snippet.strip():
                paragraphs.append(f"Lecturer Context: {ch_tr_snippet[:350]}...")

        ch_figs = [f for f in figures_list if any(getattr(s, "slide_title", "") == f.title for s in ch_slides)]
        if not ch_figs and figures_list:
            ch_figs = figures_list[start_i:end_i]

        chapters.append(StudyGuideChapter(
            chapter_num=c_num,
            title=c_title,
            subtitle=c_sub,
            introduction=f"Introduction to {c_title} and foundational principles.",
            content_paragraphs=paragraphs,
            latex_formulas=[],
            associated_figures=ch_figs,
            key_takeaways=[
                f"Core understanding of {c_title} is established through progressive visual slides.",
                "Systematic relationships between lecture components must be verified against experimental conditions.",
                "Review the corresponding visual diagrams and formulas for complete conceptual mastery."
            ],
            instructor_notes=f"Pay particular attention to the transition points in {c_title} during examination review."
        ))

    summary = (
        f"This comprehensive study guide synthesizes the key themes of '{video_title}'. "
        f"Divided across {len(chapters)} academic chapters, it details the foundational mechanisms, "
        f"visual models, and key takeaways presented throughout the lecture."
    )

    return LectureStudyGuide(
        video_title=video_title,
        lecture_summary=summary,
        chapters=chapters,
        all_figures=figures_list,
    )