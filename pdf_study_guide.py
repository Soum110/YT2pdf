"""
pdf_study_guide.py — Publication-grade academic study guide PDF compiler.

Features:
  - Textbook chapter structure (Chronological progression from basic to advanced)
  - Curated, deduplicated figures (no repeated diagrams!)
  - KaTeX math rendering (crisp LaTeX formulas)
  - Paged media CSS with Headless Chrome compilation
"""

import html
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger("pdf_study_guide")

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]

def _find_chrome() -> Optional[str]:
    for p in CHROME_PATHS:
        if os.path.exists(p):
            return p
    which_chrome = shutil.which("google-chrome") or shutil.which("chromium")
    return which_chrome


def _build_html(study_guide, video_title: str) -> str:
    """Constructs textbook-grade HTML with KaTeX and responsive paged media styling."""
    display_title = html.escape(study_guide.video_title if hasattr(study_guide, "video_title") and study_guide.video_title else video_title)
    summary_text = html.escape(study_guide.lecture_summary) if hasattr(study_guide, "lecture_summary") and study_guide.lecture_summary else ""

    chapters = getattr(study_guide, "chapters", [])

    # Build TOC
    toc_items = []
    for ch in chapters:
        t = html.escape(ch.title)
        toc_items.append(f'<li><a href="#chapter-{ch.chapter_num}"><span class="toc-num">Chapter {ch.chapter_num}:</span> {t}</a></li>')
    toc_html = "\n".join(toc_items)

    # Build Chapter Blocks
    chapters_html = []
    for ch in chapters:
        c_num = ch.chapter_num
        c_title = html.escape(ch.title)
        c_subtitle = html.escape(ch.subtitle)
        intro_p = f'<div class="chapter-intro"><p>{html.escape(ch.introduction)}</p></div>' if ch.introduction else ''

        # 1. Figures associated with this chapter (Curated & deduplicated!)
        figures_html = ""
        for f_idx, fig in enumerate(ch.associated_figures, 1):
            if fig.image_path and os.path.exists(fig.image_path):
                img_path = Path(fig.image_path).resolve()
                fig_title = html.escape(fig.title) if fig.title else ""
                title_html = f'<div class="figure-title"><strong>{fig_title}</strong></div>' if fig_title else ''
                cap = html.escape(fig.caption)
                exp = html.escape(fig.explanation)
                exp_p = f'<p class="figure-explanation">{exp}</p>' if exp else ''
                badge = '<div class="sim-badge">🔬 Technical Simulation</div>' if fig.fig_type == "simulation" else '<div class="diag-badge">📷 Lecture Schematic</div>'
                card_class = "simulation-card" if fig.fig_type == "simulation" else "figure-card"

                figures_html += f'''
                <div class="figure-card {card_class}">
                  {badge}
                  {title_html}
                  <div class="figure-img-wrap">
                    <img src="{img_path.as_uri()}" alt="{cap}" class="figure-img" />
                  </div>
                  <p class="figure-caption"><strong>Figure {c_num}.{f_idx}:</strong> {cap}</p>
                  {exp_p}
                </div>
                '''

        # 2. Content Paragraphs (Chronological basic -> advanced)
        body_p_html = "".join(f'<p class="explanation-p">{p}</p>' for p in ch.content_paragraphs if p.strip())

        # 3. LaTeX Formulas
        formulas_html = ""
        if ch.latex_formulas:
            formula_cards = []
            for item in ch.latex_formulas:
                raw_formula = item.get("formula", "").strip()
                desc = html.escape(item.get("description", ""))
                if not raw_formula.startswith("$$"):
                    raw_formula = f"$${raw_formula}$$"
                desc_html = f'<p class="formula-desc">{desc}</p>' if desc else ''
                formula_cards.append(f'''
                <div class="formula-card">
                  <div class="latex-eq">{raw_formula}</div>
                  {desc_html}
                </div>
                ''')
            formulas_html = f'''
            <div class="formulas-container">
              <div class="box-header"><span class="box-icon">📐</span> Mathematical Formulations & Identities</div>
              {"".join(formula_cards)}
            </div>
            '''

        # 4. Key Takeaways
        takeaways_html = ""
        if ch.key_takeaways:
            pills = "".join(f'<li class="takeaway-item">{html.escape(str(t))}</li>' for t in ch.key_takeaways)
            takeaways_html = f'''
            <div class="takeaways-box">
              <div class="takeaways-header">✦ Core Principles & Takeaways</div>
              <ul class="takeaways-list">{pills}</ul>
            </div>
            '''

        # 5. Instructor Insights
        notes_html = ""
        if ch.instructor_notes and ch.instructor_notes.strip():
            notes_p = html.escape(ch.instructor_notes.strip())
            notes_html = f'''
            <div class="callout-box note-box">
              <div class="callout-header"><span class="callout-icon">💡</span> Instructor Insight & Practical Application</div>
              <p class="callout-content">{notes_p}</p>
            </div>
            '''

        ch_block = f'''
        <article class="lecture-chapter" id="chapter-{c_num}">
          <header class="chapter-header">
            <div class="chapter-index-badge">Chapter {c_num:02d}</div>
            <h2 class="chapter-heading">{c_title}</h2>
            <div class="chapter-subheading">{c_subtitle}</div>
          </header>

          {intro_p}

          {figures_html}

          <div class="chapter-body">
            {body_p_html}
          </div>

          {formulas_html}

          {takeaways_html}

          {notes_html}
        </article>
        '''
        chapters_html.append(ch_block)

    body_content = "\n".join(chapters_html)

    # Executive Summary Card
    summary_html = f'''
    <div class="executive-summary-card">
      <div class="exec-title">Executive Lecture Overview</div>
      <p class="exec-text">{summary_text}</p>
    </div>
    ''' if summary_text else ''

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{display_title} — Comprehensive Study Guide</title>
  
  <!-- KaTeX for High-Resolution Math Rendering -->
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js"
    onload="renderMathInElement(document.body, {{
      delimiters: [
        {{left: '$$', right: '$$', display: true}},
        {{left: '$', right: '$', display: false}}
      ],
      throwOnError: false
    }});"></script>

  <style>
    @page {{
      size: A4;
      margin: 18mm 16mm 18mm 16mm;
      @top-left {{
        content: "{display_title[:45]}...";
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 8pt;
        color: #94a3b8;
      }}
      @top-right {{
        content: "Definitive Lecture Study Guide";
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 8pt;
        color: #94a3b8;
      }}
      @bottom-center {{
        content: counter(page);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 9pt;
        color: #64748b;
        font-weight: 600;
      }}
    }}

    *, *::before, *::after {{ box-sizing: border-box; }}
    
    body {{
      font-family: Charter, "Bitstream Charter", "Sitka Text", Cambria, Georgia, serif;
      font-size: 10.5pt;
      line-height: 1.75;
      color: #1e293b;
      background: #ffffff;
      margin: 0;
      padding: 0;
    }}

    /* ── Chapter Cover Page ── */
    .chapter-cover {{
      padding: 35px 20px 25px;
      border-bottom: 3px solid #2563eb;
      margin-bottom: 30px;
    }}
    .chapter-badge {{
      display: inline-block;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 8.5pt;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1.5px;
      color: #2563eb;
      background: #eff6ff;
      border: 1px solid #bfdbfe;
      padding: 4px 12px;
      border-radius: 999px;
      margin-bottom: 12px;
    }}
    .chapter-title {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 24pt;
      font-weight: 800;
      line-height: 1.2;
      color: #0f172a;
      margin: 0 0 10px 0;
      letter-spacing: -0.5px;
    }}
    .chapter-subtitle {{
      font-size: 11.5pt;
      color: #475569;
      font-style: italic;
      margin-bottom: 18px;
    }}
    .chapter-meta-bar {{
      display: flex;
      gap: 16px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 9pt;
      color: #64748b;
      background: #f8fafc;
      padding: 8px 14px;
      border-radius: 8px;
      border: 1px solid #e2e8f0;
      margin-bottom: 22px;
    }}

    /* ── Executive Overview ── */
    .executive-summary-card {{
      background: #f0fdf4;
      border: 1px solid #bbf7d0;
      border-left: 4px solid #16a34a;
      border-radius: 8px;
      padding: 14px 18px;
      margin-bottom: 25px;
      break-inside: avoid;
    }}
    .exec-title {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 9.5pt;
      font-weight: 700;
      color: #15803d;
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .exec-text {{
      font-size: 9.5pt;
      color: #166534;
      line-height: 1.6;
      margin: 0;
    }}

    /* ── Table of Contents ── */
    .toc-card {{
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      padding: 18px 22px;
      margin-bottom: 30px;
      break-inside: avoid;
    }}
    .toc-title {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 11pt;
      font-weight: 700;
      color: #0f172a;
      margin: 0 0 10px 0;
      border-bottom: 1.5px solid #cbd5e1;
      padding-bottom: 6px;
    }}
    .toc-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: flex;
      flex-direction: column;
      gap: 7px;
    }}
    .toc-list li a {{
      color: #334155;
      text-decoration: none;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 9.5pt;
      display: flex;
      gap: 8px;
    }}
    .toc-num {{
      font-weight: 700;
      color: #2563eb;
    }}

    /* ── Chapter Section ── */
    .lecture-chapter {{
      margin-bottom: 45px;
      padding-bottom: 35px;
      border-bottom: 2px solid #e2e8f0;
    }}
    .lecture-chapter:last-of-type {{
      border-bottom: none;
    }}
    .chapter-header {{
      margin-bottom: 18px;
      break-inside: avoid;
    }}
    .chapter-index-badge {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 9pt;
      font-weight: 800;
      color: #2563eb;
      text-transform: uppercase;
      letter-spacing: 1.5px;
      margin-bottom: 4px;
    }}
    .chapter-heading {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 17pt;
      font-weight: 700;
      color: #0f172a;
      margin: 0 0 4px 0;
      letter-spacing: -0.3px;
    }}
    .chapter-subheading {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 10pt;
      color: #64748b;
      margin-bottom: 12px;
      font-style: italic;
    }}
    .chapter-intro {{
      background: #f8fafc;
      border-left: 3px solid #64748b;
      padding: 10px 14px;
      margin-bottom: 16px;
      border-radius: 0 6px 6px 0;
      font-style: italic;
      color: #334155;
      break-inside: avoid;
    }}

    /* ── Figures & Simulations ── */
    .figure-card {{
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      padding: 14px;
      margin: 18px 0 20px;
      text-align: center;
      break-inside: avoid;
      box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }}
    .simulation-card {{
      background: #faf5ff;
      border: 1.5px solid #d8b4fe;
      box-shadow: 0 4px 12px rgba(147, 51, 234, 0.06);
    }}
    .sim-badge {{
      display: inline-block;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 8pt;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: #6b21a8;
      background: #f3e8ff;
      border: 1px solid #e9d5ff;
      padding: 3px 10px;
      border-radius: 999px;
      margin-bottom: 8px;
    }}
    .diag-badge {{
      display: inline-block;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 8pt;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: #1e40af;
      background: #dbeafe;
      border: 1px solid #bfdbfe;
      padding: 3px 10px;
      border-radius: 999px;
      margin-bottom: 8px;
    }}
    .figure-img-wrap {{
      display: inline-block;
      max-width: 100%;
      background: #fafafa;
      padding: 6px;
      border-radius: 6px;
      border: 1px solid #f1f5f9;
    }}
    .figure-img {{
      max-width: 100%;
      max-height: 260px;
      object-fit: contain;
      border-radius: 4px;
    }}
    .figure-caption {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 9pt;
      color: #475569;
      margin: 8px 0 4px;
    }}
    .figure-explanation {{
      font-size: 9.5pt;
      color: #334155;
      text-align: left;
      line-height: 1.6;
      margin: 8px 10px 4px;
      padding-top: 8px;
      border-top: 1px solid #f1f5f9;
    }}

    /* ── Chapter Body ── */
    .chapter-body {{
      margin: 16px 0 20px;
    }}
    .explanation-p {{
      margin-bottom: 14px;
      text-align: justify;
      hyphens: auto;
    }}

    /* ── Formulas ── */
    .formulas-container {{
      margin: 18px 0;
      break-inside: avoid;
    }}
    .box-header {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 9.5pt;
      font-weight: 700;
      color: #1d4ed8;
      margin-bottom: 8px;
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .formula-card {{
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-left: 4px solid #2563eb;
      border-radius: 8px;
      padding: 14px 18px;
      margin-bottom: 10px;
      break-inside: avoid;
    }}
    .latex-eq {{
      font-size: 12pt;
      margin: 6px 0;
      overflow-x: auto;
    }}
    .formula-desc {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 8.5pt;
      color: #64748b;
      margin: 6px 0 0;
      line-height: 1.5;
    }}

    /* ── Takeaways ── */
    .takeaways-box {{
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 14px 18px;
      margin: 16px 0;
      break-inside: avoid;
    }}
    .takeaways-header {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 9pt;
      font-weight: 700;
      color: #334155;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      margin-bottom: 8px;
    }}
    .takeaways-list {{
      margin: 0;
      padding-left: 20px;
      color: #334155;
      font-size: 9.5pt;
    }}
    .takeaway-item {{
      margin-bottom: 6px;
    }}

    /* ── Callout Box ── */
    .callout-box {{
      border-radius: 8px;
      padding: 14px 18px;
      margin: 18px 0;
      break-inside: avoid;
    }}
    .note-box {{
      background: #fffbeb;
      border: 1px solid #fef3c7;
      border-left: 4px solid #f59e0b;
    }}
    .callout-header {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 9pt;
      font-weight: 700;
      color: #b45309;
      margin-bottom: 6px;
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .callout-content {{
      font-size: 9.5pt;
      color: #78350f;
      margin: 0;
      line-height: 1.6;
    }}
  </style>
</head>
<body>

  <!-- Chapter Cover -->
  <header class="chapter-cover">
    <div class="chapter-badge">Comprehensive Lecture Study Guide</div>
    <h1 class="chapter-title">{display_title}</h1>
    <div class="chapter-subtitle">Chronological Academic Synthesis & Technical Notes: From Foundations to Advanced Theory</div>
    
    <div class="chapter-meta-bar">
      <span>📖 <strong>{len(chapters)}</strong> Thematic Chapters</span>
      <span>⏱ Full Lecture Synthesis</span>
      <span>📐 KaTeX LaTeX Mathematical Formulations</span>
      <span>🔍 Curated Visual Models & Deduplicated Diagrams</span>
    </div>

    {summary_html}

    <div class="toc-card">
      <div class="toc-title">Table of Contents (Pedagogical Progression)</div>
      <ul class="toc-list">
        {toc_html}
      </ul>
    </div>
  </header>

  <!-- Lecture Content -->
  <main>
    {body_content}
  </main>

</body>
</html>'''


def build_study_guide_pdf(
    study_guide,
    output_path: Path,
    video_title: str = "Study Guide",
) -> Path:
    """
    Renders high-grade study guide HTML with KaTeX and compiles it to PDF
    using Headless Chrome.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Generate clean HTML
    html_content = _build_html(study_guide, video_title)
    
    html_path = output_path.parent / (output_path.stem + ".html")
    html_path.write_text(html_content, encoding="utf-8")
    log.info("Wrote study guide HTML to: %s", html_path)

    # 2. Render to PDF via Headless Chrome
    chrome_bin = _find_chrome()
    if chrome_bin:
        log.info("Found Chrome binary: %s", chrome_bin)
        cmd = [
            chrome_bin,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--virtual-time-budget=6000",
            "--run-all-compositor-stages-before-draw",
            f"--print-to-pdf={output_path.resolve()}",
            str(html_path.resolve()),
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
            if output_path.exists() and output_path.stat().st_size > 5000:
                log.info("Study guide PDF compiled via Chrome! Size: %d bytes", output_path.stat().st_size)
                return output_path
            else:
                log.warning("Chrome PDF generation completed with status %d but file missing or small: %s", res.returncode, res.stderr)
        except Exception as e:
            log.warning("Chrome execution failed: %s", e)

    # Fallback to fpdf2 if Chrome is unavailable
    log.warning("Chrome headless not available or failed. Using fpdf2 fallback.")
    from fpdf import FPDF
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, video_title[:60], ln=True)
    pdf.set_font("Helvetica", "", 10)
    for ch in getattr(study_guide, "chapters", []):
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, f"Chapter {ch.chapter_num}: {ch.title[:60]}", ln=True)
        pdf.set_font("Helvetica", "", 10)
        for p in ch.content_paragraphs[:2]:
            pdf.multi_cell(0, 6, p[:300])
    pdf.output(str(output_path))
    return output_path
