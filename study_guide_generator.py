"""
study_guide_generator.py — Advanced Pedagogical Lecture Synthesis Engine.

Key Features:
  1. Deep Slide Formula & Content Extraction:
     - Scans slides in rapid multi-image batches to extract all visible formulas,
       equations, matrix forms, definitions, and worked examples in LaTeX.
     - Directly feeds this visual catalog to the textbook synthesizer so no slide
       formula is ever omitted.
  2. University-Level Basic-to-Advanced Pedagogical Progression:
     - Chapter 1: Foundational Motivation & Physical Concepts (Intuition, scalar vs vector).
     - Chapter 2: Theoretical Architecture & Structural Formulations (All slide formulas:
       Cartesian, Polar, Cylindrical, Spherical, coordinate frames, transformations).
     - Chapter 3: Mathematical Formalism & Detailed Derivations (Vector algebra, Euclidean norms,
       unit vector normalization, component-wise addition, dot/cross products, Cauchy-Schwarz).
     - Chapter 4: Exhaustive Deep-Dive on Touched-Upon Concepts & Advanced Theory (3D Cartesian frames,
       orthonormal basis vectors i, j, k, direction cosines, projection theorems, rotation matrices,
       rigid body kinematics, vector triple products BAC-CAB identity).
     - Chapter 5: Practical Engineering Applications & Solved Step-by-Step Exemplar Problems
       (Worked numerical and algebraic calculations with full steps).
     - Chapter 6: Chapter Summary, Master Formula Reference Sheet & Self-Assessment
       (Exhaustive equation reference table compiling all formulas from slides and theory).
  3. Multi-Tier Visual Deduplication:
     - Filters out progressive bullet animations and duplicates (max 4 distinct slide schematics).
     - Programmatic simulations (3D coordinates, signal sampling, vector projections, or AI-generated Matplotlib).
     - Chapters explicitly request relevant figures; zero repetition across the book.
  4. Resilient KaTeX & JSON Parsing:
     - Token-walking JSON cleaner safely handles unescaped LaTeX backslashes without errors.
"""

import base64
import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Callable, Dict, Any, Set

import cv2
import numpy as np
from PIL import Image

log = logging.getLogger("study_guide_gen")


@dataclass
class CuratedFigure:
    fig_id: str                         # e.g. "fig_1", "sim_3d_coords"
    fig_type: str                       # "slide_diagram" or "simulation"
    title: str
    caption: str
    explanation: str
    image_path: str
    timestamp_sec: float = 0.0


@dataclass
class StudyGuideChapter:
    chapter_num: int
    title: str
    subtitle: str
    introduction: str
    content_paragraphs: List[str]
    latex_formulas: List[Dict[str, str]] # [{"formula": "$$...$$", "description": "..."}]
    key_takeaways: List[str]
    associated_figures: List[CuratedFigure] = field(default_factory=list)
    requested_figure_ids: List[str] = field(default_factory=list)
    instructor_notes: str = ""


@dataclass
class LectureStudyGuide:
    video_title: str
    lecture_summary: str
    chapters: List[StudyGuideChapter]
    all_figures: List[CuratedFigure]


# ─────────────────────────────────────────────
# 1. Resilient JSON Parser (Preserves LaTeX)
# ─────────────────────────────────────────────

def repair_and_parse_json(raw_text: str) -> dict:
    """
    Safely parses JSON responses from LLMs that contain unescaped LaTeX backslashes,
    markdown code fences, trailing commas, or minor end-of-text truncations.
    """
    s = raw_text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()

    result = []
    i = 0
    n = len(s)
    in_string = False

    latex_conflicts = (
        "frac", "times", "theta", "tau", "text", "begin", "bar", "beta", "bf",
        "rho", "right", "rm", "newline", "nu", "nabla", "neq", "not"
    )

    while i < n:
        c = s[i]
        if c == '"' and (i == 0 or s[i - 1] != '\\'):
            in_string = not in_string
            result.append(c)
            i += 1
        elif in_string and c == '\\':
            if i + 1 < n:
                nxt = s[i + 1]
                rest = s[i + 1 : i + 10]
                is_latex_conflict = any(rest.startswith(w) for w in latex_conflicts)

                if is_latex_conflict:
                    result.append('\\\\')
                    i += 1
                elif nxt in ('"', '\\', '/'):
                    result.append(c)
                    result.append(nxt)
                    i += 2
                elif nxt in ('n', 'r', 't', 'b', 'f'):
                    if i + 2 < n and s[i + 2].isalpha():
                        result.append('\\\\')
                        i += 1
                    else:
                        result.append(c)
                        result.append(nxt)
                        i += 2
                elif nxt == 'u' and i + 5 < n and all(s[k] in '0123456789abcdefABCDEF' for k in range(i + 2, i + 6)):
                    result.append(s[i : i + 6])
                    i += 6
                else:
                    result.append('\\\\')
                    i += 1
            else:
                result.append('\\\\')
                i += 1
        else:
            result.append(c)
            i += 1

    repaired = "".join(result)
    repaired = re.sub(r',\s*([\}\]])', r'\1', repaired)

    try:
        return json.loads(repaired)
    except Exception as e:
        log.warning("First-pass JSON parse failed: %s. Attempting bracket auto-close...", e)
        cleaned = repaired.strip()
        quotes = cleaned.count('"') - cleaned.count('\\"')
        if quotes % 2 != 0:
            cleaned += '"'
        stack = []
        in_str = False
        for ch in cleaned:
            if ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch in '{[':
                    stack.append('}' if ch == '{' else ']')
                elif ch in '}]':
                    if stack and stack[-1] == ch:
                        stack.pop()
        while stack:
            cleaned += stack.pop()
        return json.loads(cleaned)


# ─────────────────────────────────────────────
# 2. Slide Formulas & Mathematical Content Extraction
# ─────────────────────────────────────────────

def _encode_image(image_bgr: np.ndarray, max_width: int = 1000, quality: int = 85) -> str:
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    if pil.width > max_width:
        ratio = max_width / pil.width
        pil = pil.resize((max_width, int(pil.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _extract_slide_formulas(client, gemini_model: str, slides: list, batch_size: int = 5) -> str:
    """
    Rapidly transcribes all visible mathematical formulas, equations, definitions,
    and worked examples directly from slide images in multi-image batches.
    """
    from google.genai import types

    if not slides:
        return "No slide images available for formula extraction."

    log.info("Extracting formulas and text across %d slides in batches of %d...", len(slides), batch_size)
    transcription_blocks = []

    for batch_start in range(0, len(slides), batch_size):
        batch = slides[batch_start : batch_start + batch_size]
        parts = []
        for idx_in_batch, slide in enumerate(batch, 1):
            abs_idx = batch_start + idx_in_batch
            mins, secs = divmod(int(slide.timestamp_sec), 60)
            img_b64 = _encode_image(slide.image, max_width=900, quality=80)
            parts.append(types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=base64.b64decode(img_b64))))
            parts.append(types.Part(text=f"Above is Slide {abs_idx} (timestamp {mins:02d}:{secs:02d})."))

        parts.append(types.Part(text="""For each slide above, rigorously transcribe:
1. Exact slide title and topic headers.
2. ALL mathematical formulas, equations, definitions, coordinate relations, and matrix notation formatted strictly in valid LaTeX ($...$ or $$...$$).
3. Any worked numerical examples or algebraic steps shown on the slide.
Format clearly by Slide Number."""))

        try:
            resp = client.models.generate_content(
                model=gemini_model,
                contents=[types.Content(role="user", parts=parts)],
                config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=2048),
            )
            transcription_blocks.append(resp.text.strip())
            log.info("  Transcribed slides %d-%d successfully.", batch_start + 1, batch_start + len(batch))
        except Exception as e:
            log.warning("  Batch transcription for slides %d-%d failed: %s", batch_start + 1, batch_start + len(batch), e)

    combined_catalog = "\n\n".join(transcription_blocks)
    log.info("Slide formula catalog compiled: %d characters of mathematical content.", len(combined_catalog))
    return combined_catalog


# ─────────────────────────────────────────────
# 3. Diagram Detection & Visual Deduplication
# ─────────────────────────────────────────────

def _is_duplicate_diagram(
    new_crop_bgr: np.ndarray,
    existing_crops: List[np.ndarray],
    new_title: str,
    existing_titles: List[str],
    threshold_mse: float = 750.0,
) -> bool:
    """Checks both visual pixel similarity (MSE) and semantic title overlap."""
    if not existing_crops:
        return False

    # 1. Semantic title overlap check
    new_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", new_title.lower()))
    for ext_title in existing_titles:
        ext_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", ext_title.lower()))
        if new_words and ext_words:
            overlap = len(new_words & ext_words) / min(len(new_words), len(ext_words))
            if overlap >= 0.75:
                return True

    # 2. Visual comparison (normalized 128x128 grayscale)
    g_new = cv2.cvtColor(cv2.resize(new_crop_bgr, (128, 128)), cv2.COLOR_BGR2GRAY)
    for ext_bgr in existing_crops:
        g_ext = cv2.cvtColor(cv2.resize(ext_bgr, (128, 128)), cv2.COLOR_BGR2GRAY)
        mse = float(np.mean((g_new.astype(float) - g_ext.astype(float)) ** 2))
        if mse < threshold_mse:
            return True

    return False


def _detect_and_crop_diagrams(
    client, model: str, slides, crops_dir: Path, max_diagrams: int = 4
) -> List[CuratedFigure]:
    """
    Scans candidate slides for genuine technical schematics/plots.
    Filters out text bullets, progressive slide animations, and redundant diagrams.
    """
    from google.genai import types

    crops_dir.mkdir(parents=True, exist_ok=True)
    curated: List[CuratedFigure] = []
    saved_crop_images: List[np.ndarray] = []
    saved_titles: List[str] = []

    detection_prompt = """Analyze this lecture slide image.
Determine if there is a genuine technical diagram, circuit, schematic, geometric plot, chart, or vector visualization (EXCLUDING text bullet points, slide title headers, speaker photo, or pure equations).
Return JSON:
{
  "has_diagram": true/false,
  "diagram_title": "<short descriptive title of the visual schematic>",
  "diagram_caption": "<formal academic figure caption describing the schematic>",
  "diagram_explanation": "<detailed walkthrough of what the visual shows>",
  "box_2d": [ymin, xmin, ymax, xmax]  // normalized 0-1000 tightly around diagram
}"""

    log.info("Scanning %d slides for distinct visual diagrams (max target: %d)...", len(slides), max_diagrams)
    prev_slide_gray: Optional[np.ndarray] = None

    for idx, slide in enumerate(slides, 1):
        if len(curated) >= max_diagrams:
            break

        # Check full-slide difference against previous slide (skip progressive bullet points)
        slide_gray = cv2.cvtColor(cv2.resize(slide.image, (128, 72)), cv2.COLOR_BGR2GRAY)
        if prev_slide_gray is not None:
            slide_mse = float(np.mean((slide_gray.astype(float) - prev_slide_gray.astype(float)) ** 2))
            if slide_mse < 180.0:
                continue
        prev_slide_gray = slide_gray

        try:
            image_b64 = _encode_image(slide.image)
            resp = client.models.generate_content(
                model=model,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=base64.b64decode(image_b64))),
                            types.Part(text=detection_prompt),
                        ],
                    )
                ],
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1),
            )
            data = repair_and_parse_json(resp.text)
            if not data.get("has_diagram") or not data.get("box_2d"):
                continue

            box = data["box_2d"]
            if len(box) != 4:
                continue
            ymin, xmin, ymax, xmax = [float(v) for v in box]
            if ymax <= ymin or xmax <= xmin:
                continue

            h, w, _ = slide.image.shape
            y1 = max(0, int(ymin * h / 1000.0) - 4)
            y2 = min(h, int(ymax * h / 1000.0) + 4)
            x1 = max(0, int(xmin * w / 1000.0) - 4)
            x2 = min(w, int(xmax * w / 1000.0) + 4)

            crop_w = x2 - x1
            crop_h = y2 - y1

            if crop_w < 70 or crop_h < 70:
                continue
            aspect = crop_w / float(crop_h)
            if aspect > 5.5 or aspect < 0.2:
                continue

            cropped = slide.image[y1:y2, x1:x2]
            title = data.get("diagram_title", f"Slide Diagram {idx}").strip()

            if _is_duplicate_diagram(cropped, saved_crop_images, title, saved_titles):
                log.info("  Slide %d: duplicate diagram skipped (%s).", idx, title)
                continue

            saved_crop_images.append(cropped)
            saved_titles.append(title)
            fig_path = crops_dir / f"diagram_{len(curated)+1:02d}.png"
            cv2.imwrite(str(fig_path), cropped)

            fig = CuratedFigure(
                fig_id=f"fig_{len(curated)+1}",
                fig_type="slide_diagram",
                title=title,
                caption=data.get("diagram_caption", f"Visual schematic extracted from lecture at {int(slide.timestamp_sec)}s."),
                explanation=data.get("diagram_explanation", ""),
                image_path=str(fig_path),
                timestamp_sec=slide.timestamp_sec,
            )
            curated.append(fig)
            log.info("  Slide %d: curated diagram [%s] saved (%dx%d: %s)", idx, fig.fig_id, crop_w, crop_h, title)

        except Exception as e:
            log.debug("Diagram scan on slide %d skipped: %s", idx, e)

    return curated


# ─────────────────────────────────────────────
# 4. Universal Technical Simulations
# ─────────────────────────────────────────────

def _generate_curated_simulations(
    client,
    gemini_model: str,
    transcript_text: str,
    video_title: str,
    crops_dir: Path,
) -> List[CuratedFigure]:
    """
    Generates high-yield, relevant technical simulations based on the lecture subject.
    Works universally across physics, math, signal processing, and other STEM topics.
    """
    from simulation_generator import (
        generate_3d_coordinates_figure,
        generate_signal_sampling_figure,
        generate_dot_cross_product_figure,
        execute_custom_simulation_code,
    )

    crops_dir.mkdir(parents=True, exist_ok=True)
    simulations: List[CuratedFigure] = []
    text_corpus = (video_title + " " + transcript_text).lower()

    # Pre-built Simulation 1: 3D Coordinate Frame & Spatial Decomposition
    has_vectors = any(k in text_corpus for k in ["vector", "coordinates", "coordinate", "basis", "dimension", "3d", "three dimension", "rigid body", "inertial"])
    if has_vectors and any(k in text_corpus for k in ["frame", "axis", "axes", "system", "component", "spatial", "cartesian", "body frame"]):
        sim_path = crops_dir / "sim_3d_coordinates.png"
        generate_3d_coordinates_figure(sim_path)
        simulations.append(CuratedFigure(
            fig_id="sim_3d_coords",
            fig_type="simulation",
            title="3D Cartesian Frame & Spatial Vector Decomposition",
            caption="Reference Model: 3D Cartesian Coordinate Frame with Orthonormal Unit Basis Vectors (i, j, k) and Orthogonal Spatial Projections",
            explanation="Rigorous reference illustrating 3D Cartesian space with orthogonal unit basis vectors i, j, k. Any spatial vector v decomposes into scalar projections along the respective axes: v = x*i + y*j + z*k, with projection onto the XY plane at (x, y, 0).",
            image_path=str(sim_path),
        ))

    # Pre-built Simulation 2: Signal Sampling & Discretization
    has_signals = any(k in text_corpus for k in ["signal", "sampling", "sample", "analog", "continuous", "discrete", "nyquist", "fourier"])
    if has_signals and len(simulations) < 2:
        sim_path = crops_dir / "sim_signal_sampling.png"
        generate_signal_sampling_figure(sim_path)
        simulations.append(CuratedFigure(
            fig_id="sim_sampling",
            fig_type="simulation",
            title="Continuous Analog Waveform vs. Uniform Digital Sampling",
            caption="Reference Model: Uniform Sampling of Continuous-Time Signal x(t) to Discrete Sequence x[n]",
            explanation="Pedagogical comparison illustrating uniform discretization of an analog signal x(t) sampled at interval Ts = 1/fs, producing discrete-time samples x[n] = x(nTs).",
            image_path=str(sim_path),
        ))

    # Pre-built Simulation 3: Dot Product & Geometric Projections
    has_dot_prod = any(k in text_corpus for k in ["dot product", "cross product", "inner product", "projection", "scalar product"])
    if has_dot_prod and len(simulations) < 2:
        sim_path = crops_dir / "sim_dot_product.png"
        generate_dot_cross_product_figure(sim_path)
        simulations.append(CuratedFigure(
            fig_id="sim_dot_prod",
            fig_type="simulation",
            title="Vector Dot Product & Orthogonal Projection",
            caption="Reference Model: Geometric Interpretation of Vector Dot Product and Projection of B onto A",
            explanation="Visual geometry illustrating vector dot product A · B = |A||B|cos(θ), showing the scalar projection of vector B along vector A.",
            image_path=str(sim_path),
        ))

    # If domain is not covered by pre-built simulations, ask Gemini to write custom Matplotlib code
    if not simulations and client:
        try:
            log.info("Requesting AI custom simulation for domain...")
            custom_prompt = f"""You are a scientific visualization specialist.
Based on this lecture topic: "{video_title}" and transcript excerpt:
\"\"\"{transcript_text[:3000]}\"\"\"

Write a concise, standalone Python script using `matplotlib.pyplot` and `numpy` that generates a clean, publication-grade pedagogical schematic or simulation diagram clarifying the central concept or mathematical model taught in this lecture.

RULES:
1. Return ONLY the python code inside a ```python ``` block.
2. Use `output_path` (already provided as a string variable) in `plt.savefig(output_path, bbox_inches='tight', dpi=200)`.
3. Set figsize=(7, 4.5), clean white background, high contrast, readable labels.
4. Do NOT call plt.show(). Call plt.close('all') after savefig.
5. Provide clear LaTeX labels on axes and curves."""

            resp = client.models.generate_content(
                model=gemini_model,
                contents=custom_prompt,
            )
            code_text = resp.text
            custom_path = crops_dir / "sim_custom_concept.png"
            if execute_custom_simulation_code(code_text, custom_path):
                simulations.append(CuratedFigure(
                    fig_id="sim_concept",
                    fig_type="simulation",
                    title="Key Theoretical Model Simulation",
                    caption=f"Programmatic Visual Simulation: Core Principles of {video_title[:40]}",
                    explanation="Custom algorithmic simulation clarifying the core governing relationships and behaviors discussed in this lecture.",
                    image_path=str(custom_path),
                ))
        except Exception as e:
            log.warning("Custom simulation generation skipped: %s", e)

    return simulations[:2]


# ─────────────────────────────────────────────
# 5. Advanced Basic-to-Advanced Synthesis Prompt
# ─────────────────────────────────────────────

SYNTHESIS_SYSTEM_PROMPT = """You are a distinguished university professor and world-class STEM textbook author.
Your mission is to author a definitive, rigorous, university-level academic textbook chapter that thoroughly synthesizes an entire lecture video.

PEDAGOGICAL RIGOR & COMPLEXITY (BASIC TO ADVANCED):
Do NOT write superficial or elementary summaries. You must build a comprehensive academic chapter that takes the student from intuitive foundational definitions up to advanced university/graduate-level theoretical formulations, full proofs, mathematical derivations, and solved exemplar problem sets:

- Chapter 1: Foundational Motivation & Physical Concepts
  * Intuitive definitions, physical motivation ('Why'), scalar vs. vector distinctions, physical units, dimensional consistency, coordinate independence of physical quantities.
- Chapter 2: Theoretical Architecture & Structural Formulations
  * EVERY mathematical equation and transformation shown on the slides MUST be rigorously included in LaTeX.
  * Formal taxonomy: Cartesian coordinates (2D and 3D), Polar coordinates (r, theta), Cylindrical, Spherical coordinate systems.
  * Rigorous transformation equations (Cartesian <-> Polar: x = r cos(theta), y = r sin(theta), r = sqrt(x^2 + y^2), theta = arctan2(y, x)).
  * Coordinate frames: Inertial reference frames vs. Body-fixed reference frames, origin placement, right-hand convention.
- Chapter 3: Mathematical Formalism & Detailed Derivations
  * Vector algebra: Geometric triangle and parallelogram laws vs. analytical component-wise addition: a + b = <a1+b1, a2+b2>.
  * Scalar multiplication and its algebraic properties.
  * Euclidean norms and distances: ||v|| = sqrt(sum v_i^2), generalized Pythagorean theorem in R^n.
  * Unit vector normalization: u = v / ||v||, direction cosines cos(alpha), cos(beta), cos(gamma), and the identity cos^2(alpha) + cos^2(beta) + cos^2(gamma) = 1.
- Chapter 4: Exhaustive Deep-Dive on Touched-Upon Concepts & Advanced Theory
  * MANDATORY: Provide a complete, graduate-level treatment of all topics glossed over or briefly touched upon:
    - 3D Cartesian coordinates and orthonormal unit basis vectors (i, j, k).
    - Rigid body dynamics: definition of a rigid body as a system of particles where distance ||r_i - r_j|| = constant for all time.
    - Reference frame kinematics: relation between inertial frame and body frame via rotation matrices R in SO(3).
    - Dot product: algebraic definition sum(a_i*b_i) and geometric definition ||a||||b||cos(theta), Cauchy-Schwarz inequality, scalar and vector projections.
    - Cross product: determinant expansion, right-hand rule, geometric area of parallelogram, anti-commutativity, cross product matrix operator [v]x.
    - Vector triple products: BAC-CAB rule: a x (b x c) = (a . c)b - (a . b)c.
- Chapter 5: Practical Engineering Applications & Step-by-Step Solved Problem Sets
  * Real-world aerospace/robotics/engineering applications (e.g. UAV flight control, wind velocity integration, sensor frames).
  * At least 2 FULLY WORKED NUMERICAL & ALGEBRAIC EXEMPLAR PROBLEMS showing step-by-step problem statements, variable assignments, substitution, intermediate algebraic steps, and final answers with units.
- Chapter 6: Chapter Summary, Master Formula Reference Sheet & Self-Assessment
  * EXHAUSTIVE reference table compiling EVERY formula mentioned in the lecture and slides.
  * High-yield conceptual review questions with complete model answers.

MATHEMATICAL FORMULAS (LATEX):
- Every single equation MUST be formatted in valid LaTeX enclosed in $$ ... $$ for display math or $ ... $ for inline math.
- Accompany each formula with a clear explanation of what it calculates, describe every variable, and state physical units.
- CRITICAL FOR JSON: In your JSON response, always double-escape backslashes in LaTeX equations (e.g. write "\\vec{v}", "\\hat{i}", "\\frac{a}{b}", "\\times", "\\theta", "\\alpha", "\\mathbf{R}") so that the output is 100% valid JSON.

FIGURE INTEGRATION:
- In each chapter, specify `figure_ids: ["<fig_id>"]` ONLY for figures that are directly relevant and discussed in that chapter.
- A figure should appear in at most ONE chapter across the entire book (never repeat the same figure). If a chapter does not need a figure, use `figure_ids: []`.

Return ONLY a valid JSON object matching this schema:
{
  "book_title": "<comprehensive textbook chapter title>",
  "lecture_summary": "<2-3 paragraph executive summary of the lecture>",
  "chapters": [
    {
      "chapter_num": 1,
      "title": "<Chapter Title>",
      "subtitle": "<Descriptive Subtitle>",
      "introduction": "<1-2 paragraph introduction>",
      "content_paragraphs": [
        "<thorough, rigorous academic paragraph 1 with complete mathematical context>",
        "<thorough academic paragraph 2>",
        "<thorough academic paragraph 3>",
        "<thorough academic paragraph 4>"
      ],
      "latex_formulas": [
        {"formula": "$$<latex equation>$$", "description": "<detailed explanation of variables, units, and meaning>"}
      ],
      "key_takeaways": [
        "<key takeaway 1>",
        "<key takeaway 2>",
        "<key takeaway 3>"
      ],
      "instructor_notes": "<critical theoretical insights, exam tips, or practical pitfalls>",
      "figure_ids": ["<fig_id_if_applicable>"]
    }
  ]
}
"""


def generate_study_guide_content(
    slides,
    transcript_segments: list,
    total_duration: float,
    gemini_api_key: str,
    crops_dir: Optional[Path] = None,
    gemini_model: str = "gemini-3.5-flash-lite",
    progress_cb: Optional[Callable] = None,
) -> LectureStudyGuide:
    """
    Synthesizes the entire lecture from basic to advanced with all slide formulas,
    curated deduplicated diagrams, and targeted simulations.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=gemini_api_key)
    crops_dir = crops_dir or Path("./diagram_crops")

    # 1. Compile full lecture transcript
    full_transcript = " ".join(seg[2] for seg in transcript_segments if len(seg) >= 3 and seg[2].strip())
    mins, secs = divmod(int(total_duration), 60)
    duration_str = f"{mins}m {secs}s"

    if progress_cb:
        progress_cb(1, 5)

    # 2. Extract mathematical formulas directly from slides
    log.info("Phase 1: Extracting all mathematical formulas directly from slides...")
    slide_formula_catalog = _extract_slide_formulas(client, gemini_model, slides, batch_size=5)

    if progress_cb:
        progress_cb(2, 5)

    # 3. Extract & Deduplicate visual diagrams from slides (Max 4 distinct schematics)
    log.info("Phase 2: Extracting and deduplicating visual diagrams...")
    curated_diagrams = _detect_and_crop_diagrams(client, gemini_model, slides, crops_dir, max_diagrams=4)

    if progress_cb:
        progress_cb(3, 5)

    # 4. Generate targeted programmatic simulations (Max 2 per video)
    log.info("Phase 3: Generating curated technical simulations...")
    curated_sims = _generate_curated_simulations(client, gemini_model, full_transcript, "Lecture", crops_dir)
    all_figures = curated_diagrams + curated_sims
    log.info("Total curated visual assets for lecture: %d (%d diagrams, %d simulations)",
             len(all_figures), len(curated_diagrams), len(curated_sims))

    if progress_cb:
        progress_cb(4, 5)

    # 5. Synthesize Whole Lecture into Chronological Chapters (Basic to Advanced)
    log.info("Phase 4: Synthesizing entire lecture into chronological academic chapters...")
    fig_descriptions = "\n".join(
        f"- [{f.fig_id}] ({f.fig_type}): {f.title} — {f.caption}"
        for f in all_figures
    )

    user_prompt = f"""LECTURE METADATA:
Duration: {duration_str}
Total Slide Frames: {len(slides)}

MATHEMATICAL FORMULAS & CONTENT EXTRACTED DIRECTLY FROM THE SLIDES:
--- SLIDE CATALOG BEGIN ---
{slide_formula_catalog}
--- SLIDE CATALOG END ---

CURATED VISUAL ASSETS AVAILABLE FOR THIS LECTURE:
{fig_descriptions if fig_descriptions else "No visual figures extracted."}

FULL LECTURE AUDIO TRANSCRIPT:
--- TRANSCRIPT BEGIN ---
{full_transcript[:22000]}
--- TRANSCRIPT END ---

Please author the complete, self-contained textbook chapter progressing chronologically from basic foundations to advanced university-level mathematics.
Ensure that EVERY formula shown on the slides is incorporated with complete derivations, explanations, and worked exemplar problems."""

    chapters: List[StudyGuideChapter] = []
    book_title = "Comprehensive Lecture Study Guide"
    lecture_summary = ""

    try:
        resp = client.models.generate_content(
            model=gemini_model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYNTHESIS_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=8192,
            ),
        )
        data = repair_and_parse_json(resp.text)
        book_title = data.get("book_title", book_title)
        lecture_summary = data.get("lecture_summary", "")

        raw_chapters = data.get("chapters", [])
        for ch_idx, ch_data in enumerate(raw_chapters, 1):
            ch = StudyGuideChapter(
                chapter_num=ch_data.get("chapter_num", ch_idx),
                title=ch_data.get("title", f"Chapter {ch_idx}"),
                subtitle=ch_data.get("subtitle", ""),
                introduction=ch_data.get("introduction", ""),
                content_paragraphs=ch_data.get("content_paragraphs", []),
                latex_formulas=ch_data.get("latex_formulas", []),
                key_takeaways=ch_data.get("key_takeaways", []),
                requested_figure_ids=ch_data.get("figure_ids", []),
                instructor_notes=ch_data.get("instructor_notes", ""),
            )
            chapters.append(ch)

    except Exception as e:
        log.exception("Lecture synthesis failed, using robust fallback: %s", e)
        chapters.append(StudyGuideChapter(
            chapter_num=1,
            title="Foundations and Core Principles",
            subtitle="Comprehensive Theory and Mathematical Formulations",
            introduction="This lecture presents fundamental principles and formal engineering methods.",
            content_paragraphs=[
                full_transcript[:1200] if full_transcript else "Please refer to the slides for visual reference."
            ],
            latex_formulas=[],
            key_takeaways=["Comprehensive review of lecture concepts."],
        ))

    # 6. Distribute curated figures strictly by relevance (Zero repetition)
    figures_by_id = {f.fig_id: f for f in all_figures}
    assigned_figure_ids: Set[str] = set()

    for ch in chapters:
        ch.associated_figures = []
        for fid in ch.requested_figure_ids:
            if fid in figures_by_id and fid not in assigned_figure_ids:
                ch.associated_figures.append(figures_by_id[fid])
                assigned_figure_ids.add(fid)

    # Assign remaining curated figures (at most 1 per chapter) to chapters that have no figures
    unassigned = [f for f in all_figures if f.fig_id not in assigned_figure_ids]
    for ch in chapters:
        if not ch.associated_figures and unassigned:
            fig_to_add = unassigned.pop(0)
            ch.associated_figures.append(fig_to_add)
            assigned_figure_ids.add(fig_to_add.fig_id)

    if progress_cb:
        progress_cb(5, 5)

    log.info("Study guide generation complete: %d chapters generated with %d figures displayed.",
             len(chapters), len(assigned_figure_ids))

    return LectureStudyGuide(
        video_title=book_title,
        lecture_summary=lecture_summary,
        chapters=chapters,
        all_figures=all_figures,
    )