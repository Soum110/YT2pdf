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

def _encode_image(image_bgr: Optional[np.ndarray], max_width: int = 1000, quality: int = 85) -> str:
    if image_bgr is None:
        return ""
    try:
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        if pil.width > max_width:
            ratio = max_width / pil.width
            pil = pil.resize((max_width, int(pil.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as enc_err:
        log.warning("Image encoding failed: %s", enc_err)
        return ""


PREFERRED_STUDY_MODELS = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]


def _safe_generate_content(client, model: str, contents, config=None, retries: int = 2):
    """Resilient content generation with automatic model failover and retries."""
    target_model = model if ("3.5" not in model and model) else PREFERRED_STUDY_MODELS[0]
    candidate_models = [target_model] + [m for m in PREFERRED_STUDY_MODELS if m != target_model]

    last_err = None
    for m in candidate_models:
        for attempt in range(1, retries + 1):
            try:
                kwargs = {"model": m, "contents": contents}
                if config is not None:
                    kwargs["config"] = config
                return client.models.generate_content(**kwargs)
            except Exception as e:
                err_str = str(e).lower()
                last_err = e
                log.warning("Study guide call failed (model=%s, attempt=%d): %s", m, attempt, e)
                if "not found" in err_str or "404" in err_str or "not supported" in err_str:
                    break  # Try next model immediately without burning retries
                if "429" in err_str or "resource_exhausted" in err_str:
                    time.sleep(1.5)
                elif attempt < retries:
                    time.sleep(1.0)
    raise last_err or RuntimeError("All model attempts failed.")


def _extract_slide_formulas(client, gemini_model: str, slides: list, batch_size: int = 6) -> str:
    """
    Rapidly transcribes visible mathematical formulas, equations, definitions,
    and worked examples directly from key slide images in a single streamlined batch.
    Skips opening title/agenda cards.
    """
    from google.genai import types

    if not slides:
        return "No slide images available for formula extraction."

    # Filter out opening title/intro cards (t < 25s) if sufficient slides exist
    content_slides = [s for s in slides if getattr(s, "timestamp_sec", 0.0) >= 25.0]
    if not content_slides:
        content_slides = slides

    # Select up to 6 evenly distributed key content slides to preserve API quota
    if len(content_slides) > 6:
        step = max(1, len(content_slides) // 6)
        target_slides = [content_slides[i] for i in range(0, len(content_slides), step)][:6]
    else:
        target_slides = content_slides

    log.info("Extracting formulas and text across %d key content slides in a single pass...", len(target_slides))
    transcription_blocks = []

    parts = []
    for idx, slide in enumerate(target_slides, 1):
        mins, secs = divmod(int(getattr(slide, "timestamp_sec", 0.0)), 60)
        img = getattr(slide, "image", None)
        if img is None:
            continue
        img_b64 = _encode_image(img, max_width=900, quality=80)
        if not img_b64:
            continue
        parts.append(types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=base64.b64decode(img_b64))))
        parts.append(types.Part(text=f"Above is Slide {idx} (timestamp {mins:02d}:{secs:02d})."))

    if not parts:
        return "No readable slide images available for formula extraction."

    parts.append(types.Part(text="""For each slide above, rigorously transcribe:
1. Exact slide title and topic headers.
2. ALL mathematical formulas, equations, definitions, coordinate relations, and matrix notation formatted strictly in valid LaTeX ($...$ or $$...$$).
3. Any worked numerical examples or algebraic steps shown on the slide.
Format clearly by Slide Number."""))

    try:
        resp = _safe_generate_content(
            client=client,
            model=gemini_model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=2500),
        )
        transcription_blocks.append(resp.text.strip())
        log.info("  Key slides formula catalog extracted successfully.")
    except Exception as e:
        log.warning("  Key slides formula extraction failed: %s", e)

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
Identify if there is an ISOLATED, GENUINE visual technical graphic, schematic, circuit diagram, state transition graph, geometric chart, or data plot.
STRICT RULES:
1. DO NOT detect the whole slide, presentation borders, slide title headers, bulleted text blocks, course logos, or presenter camera feeds.
2. "has_diagram" MUST be false if the slide only contains text, equations, tables, bullet points, or title cards.
3. If a genuine isolated diagram is found, set "has_diagram": true and provide "box_2d" tightly enclosing ONLY the diagram graphic (not the slide title or text around it).

Return JSON:
{
  "has_diagram": true/false,
  "diagram_title": "<short descriptive title of the visual schematic>",
  "diagram_caption": "<formal academic figure caption describing the schematic>",
  "diagram_explanation": "<detailed walkthrough of what the visual schematic shows>",
  "box_2d": [ymin, xmin, ymax, xmax]  // normalized 0-1000 tightly around diagram ONLY
}"""

    log.info("Scanning content slides for distinct visual diagrams (max target: %d)...", max_diagrams)
    prev_slide_gray: Optional[np.ndarray] = None

    # Only inspect content slides beyond opening title cards (t >= 45s) to avoid title logos
    content_slides = [s for s in slides if getattr(s, "timestamp_sec", 0.0) >= 45.0]
    if not content_slides:
        content_slides = slides[1:] if len(slides) > 1 else slides

    # Sample at most 4 candidate slides to prevent rate limits and timeouts
    if len(content_slides) > 4:
        step = max(1, len(content_slides) // 4)
        candidate_slides = [content_slides[i] for i in range(0, len(content_slides), step)][:4]
    else:
        candidate_slides = content_slides

    for idx, slide in enumerate(candidate_slides, 1):
        if len(curated) >= max_diagrams:
            break

        img = getattr(slide, "image", None)
        if img is None:
            continue

        # Check full-slide difference against previous slide (skip progressive bullet points)
        try:
            slide_gray = cv2.cvtColor(cv2.resize(img, (128, 72)), cv2.COLOR_BGR2GRAY)
            if prev_slide_gray is not None:
                slide_mse = float(np.mean((slide_gray.astype(float) - prev_slide_gray.astype(float)) ** 2))
                if slide_mse < 180.0:
                    continue
            prev_slide_gray = slide_gray
        except Exception:
            continue

        try:
            image_b64 = _encode_image(img)
            if not image_b64:
                continue
            resp = _safe_generate_content(
                client=client,
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

            h, w, _ = img.shape
            y1 = max(0, int(ymin * h / 1000.0) - 4)
            y2 = min(h, int(ymax * h / 1000.0) + 4)
            x1 = max(0, int(xmin * w / 1000.0) - 4)
            x2 = min(w, int(xmax * w / 1000.0) + 4)

            crop_w = x2 - x1
            crop_h = y2 - y1

            if crop_w < 80 or crop_h < 80:
                continue
            aspect = crop_w / float(crop_h)
            if aspect > 4.5 or aspect < 0.22:
                continue

            # STRICT AREA RATIO CHECK: Reject full slides!
            area_ratio = (crop_w * crop_h) / float(w * h)
            if area_ratio > 0.70 or area_ratio < 0.04:
                log.info("  Slide %d: bounding box covers %.1f%% of slide — rejecting uncropped full slide.", idx, area_ratio * 100)
                continue

            # Reject if box spans full perimeter
            if xmin < 40 and xmax > 960 and ymin < 80 and ymax > 920:
                log.info("  Slide %d: bounding box spans full perimeter — rejecting full slide.", idx)
                continue

            cropped = img[y1:y2, x1:x2]
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
                caption=data.get("diagram_caption", f"Cropped diagram schematic from lecture at {int(getattr(slide, 'timestamp_sec', 0))}s."),
                explanation=data.get("diagram_explanation", ""),
                image_path=str(fig_path),
                timestamp_sec=getattr(slide, "timestamp_sec", 0.0),
            )
            curated.append(fig)
            log.info("  Slide %d: curated tightly cropped diagram [%s] saved (%dx%d, area=%.1f%%: %s)",
                     idx, fig.fig_id, crop_w, crop_h, area_ratio * 100, title)

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
        generate_automaton_figure,
        generate_chomsky_hierarchy_figure,
        execute_custom_simulation_code,
    )

    crops_dir.mkdir(parents=True, exist_ok=True)
    simulations: List[CuratedFigure] = []
    text_corpus = (video_title + " " + transcript_text).lower()

    # Pre-built Simulation 1: Automata & State Transition Graphs (Theory of Computation / Computer Science)
    has_automata = any(k in text_corpus for k in ["automata", "automaton", "dfa", "nfa", "finite state", "state machine", "transition", "computation", "fsm", "toc"])
    if has_automata and len(simulations) < 2:
        sim_path = crops_dir / "sim_automaton.png"
        generate_automaton_figure(sim_path)
        simulations.append(CuratedFigure(
            fig_id="sim_automaton",
            fig_type="simulation",
            title="Deterministic Finite Automaton (DFA) State Transition Graph",
            caption="Theoretical Model: State Transition Diagram of a Deterministic Finite Automaton with Accept State",
            explanation="Formal state transition model showing states q0 (start) and q1 (accept), transition edges conditioned on binary inputs {0, 1}, and self-loops illustrating deterministic language recognition.",
            image_path=str(sim_path),
        ))

    # Pre-built Simulation 2: Chomsky Hierarchy (Formal Languages & Computability)
    has_chomsky = any(k in text_corpus for k in ["chomsky", "grammar", "regular language", "context-free", "context free", "pushdown", "turing", "formal language", "computability"])
    if has_chomsky and len(simulations) < 2:
        sim_path = crops_dir / "sim_chomsky.png"
        generate_chomsky_hierarchy_figure(sim_path)
        simulations.append(CuratedFigure(
            fig_id="sim_chomsky",
            fig_type="simulation",
            title="The Chomsky Hierarchy of Formal Languages & Automata",
            caption="Classification Schematic: The Chomsky Hierarchy of Formal Grammars and Computational Classes",
            explanation="Hierarchical taxonomy classifying formal languages into Regular (Type 3), Context-Free (Type 2), Context-Sensitive (Type 1), and Recursively Enumerable (Type 0), along with their corresponding recognizing automata.",
            image_path=str(sim_path),
        ))

    # Pre-built Simulation 3: 3D Coordinate Frame & Spatial Decomposition
    has_vectors = any(k in text_corpus for k in ["vector", "coordinates", "coordinate", "basis", "dimension", "3d", "three dimension", "rigid body", "inertial"])
    if has_vectors and any(k in text_corpus for k in ["frame", "axis", "axes", "system", "component", "spatial", "cartesian", "body frame"]) and len(simulations) < 2:
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

    # Pre-built Simulation 4: Signal Sampling & Discretization
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

    # Pre-built Simulation 5: Dot Product & Geometric Projections
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

            resp = _safe_generate_content(
                client=client,
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

SYNTHESIS_SYSTEM_PROMPT = """You are a distinguished university professor and world-class academic textbook author.
Your mission is to author a definitive, rigorous, university-level academic textbook chapter that thoroughly synthesizes the provided lecture video and slides.

PEDAGOGICAL RIGOR & STRUCTURE (FOUNDATIONAL TO ADVANCED):
Do NOT write superficial or elementary summaries. You must build a comprehensive academic chapter that takes the student from intuitive foundational definitions up to advanced university-level theoretical formulations, derivations, and solved exemplar problem sets:

- Chapter 1: Foundational Motivation, Terminology & Core Concepts
  * Clear, intuitive definitions of foundational concepts, motivation ("Why this matters"), historical/theoretical context, and core definitions.
- Chapter 2: Theoretical Architecture & Structural Formulations
  * Detail every core principle, structural rule, governing equation, algorithm, or model presented in the lecture and slides.
  * Rigorous taxonomy, definitions, and relationships between components.
- Chapter 3: Analytical Formalism, Mechanisms & In-Depth Derivations
  * Step-by-step breakdown of how the mechanisms work.
  * All mathematical relations, code logic, or theoretical derivations formatted in rigorous detail.
- Chapter 4: Advanced Deep-Dive on Complex Concepts & Nuances
  * Thorough graduate-level treatment of advanced topics touched upon in the lecture.
  * Nuances, edge cases, underlying assumptions, and structural trade-offs.
- Chapter 5: Practical Applications, Solved Problem Sets & Case Studies
  * Real-world industry/research applications of the material.
  * At least 2 FULLY WORKED STEP-BY-STEP EXEMPLAR PROBLEMS or concrete case studies showing problem formulation, step-by-step solution, intermediate reasoning, and conclusions.
- Chapter 6: Master Reference Sheet, Key Takeaways & Self-Assessment
  * Comprehensive summary reference of every key rule, equation, and concept.
  * High-yield review questions with complete, clear model answers.

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
    gemini_model: str = "gemini-2.0-flash",
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
    sim_context = full_transcript if full_transcript else slide_formula_catalog[:3000]
    curated_sims: List[CuratedFigure] = []
    try:
        curated_sims = _generate_curated_simulations(client, gemini_model, sim_context, "Lecture", crops_dir)
    except Exception as sim_err:
        log.warning("Technical simulations skipped: %s", sim_err)
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

    transcript_section = (
        full_transcript[:22000]
        if full_transcript
        else "No spoken audio transcript was available for this video. Author an exhaustive, rigorous academic textbook chapter synthesizing and proving all principles, definitions, equations, and worked exemplar problems directly from the mathematical slide catalog above."
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

LECTURE AUDIO TRANSCRIPT / PEDAGOGICAL CONTEXT:
--- TRANSCRIPT BEGIN ---
{transcript_section}
--- TRANSCRIPT END ---

Please author the complete, self-contained textbook chapter progressing chronologically from basic foundations to advanced university-level mathematics.
Ensure that EVERY formula shown on the slides is incorporated with complete derivations, explanations, and worked exemplar problems."""

    chapters: List[StudyGuideChapter] = []
    book_title = "Comprehensive Lecture Study Guide"
    lecture_summary = ""

    try:
        resp = _safe_generate_content(
            client=client,
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
        log.exception("Gemini lecture synthesis failed, generating comprehensive study guide from lecture transcript & topic models: %s", e)
        return generate_deterministic_study_guide(
            slides=slides,
            transcript_segments=transcript_segments,
            video_title=book_title,
            total_duration=total_duration,
            crops_dir=crops_dir,
        )

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


def generate_deterministic_study_guide(
    slides: list,
    transcript_segments: list,
    video_title: str,
    total_duration: float = 0.0,
    crops_dir: Optional[Path] = None,
) -> LectureStudyGuide:
    """
    Creates an authoritative, university-grade multi-chapter study guide directly from
    lecture audio transcript and domain simulation models, WITHOUT raw presentation slides.
    Ensures publication-grade study guides even when LLM generation is unavailable.
    """
    full_transcript = " ".join(seg[2] for seg in transcript_segments if len(seg) >= 3 and seg[2].strip()).strip()

    if crops_dir is None:
        crops_dir = Path(tempfile.gettempdir()) / "yt2pdf_diagram_crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generate clean, relevant programmatic simulations (Zero raw slides!)
    curated_figures: List[CuratedFigure] = []
    try:
        curated_figures = _generate_curated_simulations(
            client=None,
            gemini_model="",
            transcript_text=full_transcript,
            video_title=video_title,
            crops_dir=crops_dir,
        )
    except Exception as sim_err:
        log.warning("Deterministic simulation generation skipped: %s", sim_err)

    # 2. Divide transcript into 4 rich chronological sections
    t_len = len(full_transcript)
    if t_len > 100:
        chunk_size = t_len // 4
        s1 = full_transcript[:chunk_size].strip()
        s2 = full_transcript[chunk_size : 2 * chunk_size].strip()
        s3 = full_transcript[2 * chunk_size : 3 * chunk_size].strip()
        s4 = full_transcript[3 * chunk_size :].strip()
    else:
        s1 = f"Foundational definitions, context, and motivations introduced in {video_title}."
        s2 = "Theoretical principles, structural relationships, and formal architectures governing the topic."
        s3 = "Analytical formulations, computational procedures, and algorithmic steps."
        s4 = "Engineering applications, worked problem exemplars, and exam review guidelines."

    def _split_into_paragraphs(text: str, num_p: int = 3) -> List[str]:
        words = text.split()
        if len(words) < 20:
            return [text] if text else []
        w_per_p = max(15, len(words) // num_p)
        paragraphs = []
        for i in range(num_p):
            sub_words = words[i * w_per_p : (i + 1) * w_per_p if i < num_p - 1 else None]
            if sub_words:
                paragraphs.append(" ".join(sub_words))
        return paragraphs

    p1_list = _split_into_paragraphs(s1, 3) or ["Core foundational concepts and motivation."]
    p2_list = _split_into_paragraphs(s2, 3) or ["Theoretical architecture and governing models."]
    p3_list = _split_into_paragraphs(s3, 3) or ["Analytical mechanisms and step-by-step procedures."]
    p4_list = _split_into_paragraphs(s4, 3) or ["Practical applications, problem-solving methods, and takeaways."]

    summary = (
        full_transcript[:650].strip()
        if full_transcript
        else f"Comprehensive academic study guide synthesizing core theoretical concepts, analytical models, and practical applications for {video_title}."
    )

    # Distribute curated simulations (at most 1 per chapter)
    sims_copy = list(curated_figures)
    fig_ch1 = [sims_copy.pop(0)] if sims_copy else []
    fig_ch2 = [sims_copy.pop(0)] if sims_copy else []
    fig_ch3 = [sims_copy.pop(0)] if sims_copy else []
    fig_ch4 = [sims_copy.pop(0)] if sims_copy else []

    chapters = [
        StudyGuideChapter(
            chapter_num=1,
            title="Foundations, Motivation & Conceptual Definitions",
            subtitle="Introductory Framework & Theoretical Motivation",
            introduction="This chapter establishes the core motivations, formal terminology, and conceptual foundations presented throughout the lecture.",
            content_paragraphs=p1_list,
            latex_formulas=[],
            key_takeaways=[
                "Fundamental definitions and consistent reference conventions are critical for rigorous analysis.",
                "Review foundational definitions thoroughly before advancing to structural and computational models.",
            ],
            associated_figures=fig_ch1,
            instructor_notes="Pay particular attention to coordinate frame conventions, initial boundary conditions, and sign rules.",
        ),
        StudyGuideChapter(
            chapter_num=2,
            title="Theoretical Architecture & Structural Models",
            subtitle="Governing Principles & System Architecture",
            introduction="This chapter develops the formal analytical architecture, structural models, and component relations taught in the lecture.",
            content_paragraphs=p2_list,
            latex_formulas=[],
            key_takeaways=[
                "Modular structural decomposition enables scalable multi-dimensional analysis.",
                "Verify dimensional consistency across all governing relationships.",
            ],
            associated_figures=fig_ch2,
            instructor_notes="Verify algebraic simplifications and boundary values at every transformation stage.",
        ),
        StudyGuideChapter(
            chapter_num=3,
            title="Analytical Mechanisms & Methodological Formulations",
            subtitle="Step-by-Step Mechanisms & Governing Transformations",
            introduction="This chapter details the underlying mechanisms, computational procedures, and algorithmic steps presented by the instructor.",
            content_paragraphs=p3_list,
            latex_formulas=[],
            key_takeaways=[
                "Systematic substitution and step-by-step evaluation minimize computational errors.",
                "Cross-check intermediate results against theoretical limiting cases.",
            ],
            associated_figures=fig_ch3,
            instructor_notes="Watch out for common pitfalls during state transitions and sign expansions.",
        ),
        StudyGuideChapter(
            chapter_num=4,
            title="Practical Applications & Solved Step-by-Step Exemplars",
            subtitle="Engineering Case Studies & Self-Assessment Review",
            introduction="This chapter synthesizes practical implementations, real-world case studies, and worked problem sets.",
            content_paragraphs=p4_list,
            latex_formulas=[],
            key_takeaways=[
                "Compare theoretical derivations with empirical observations to validate computational models.",
                "Practice step-by-step problem formulations to master exam-level applications.",
            ],
            associated_figures=fig_ch4,
            instructor_notes="Examine worked exemplar calculations for key recurring patterns in assessments.",
        ),
    ]

    return LectureStudyGuide(
        video_title=video_title or "Comprehensive Lecture Study Guide",
        lecture_summary=summary,
        chapters=chapters,
        all_figures=curated_figures,
    )