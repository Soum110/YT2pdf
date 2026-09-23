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
    os.environ.get("CHROME_BIN", ""),
    os.environ.get("PUPPETEER_EXECUTABLE_PATH", ""),
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]

def _find_chrome() -> Optional[str]:
    for p in CHROME_PATHS:
        if p and os.path.exists(p):
            return p
    which_chrome = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
    )
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
                if isinstance(item, dict):
                    raw_formula = str(item.get("formula", "")).strip()
                    desc = html.escape(str(item.get("description", "")).strip())
                elif isinstance(item, str):
                    raw_formula = item.strip()
                    desc = ""
                else:
                    continue
                if not raw_formula:
                    continue
                if not raw_formula.startswith("$$"):
                    raw_formula = f"$${raw_formula}$$"
                desc_html = f'<p class="formula-desc">{desc}</p>' if desc else ''
                formula_cards.append(f'''
                <div class="formula-card">
                  <div class="latex-eq">{raw_formula}</div>
                  {desc_html}
                </div>
                ''')
            if formula_cards:
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


def _clean_text_for_pdf(text: str) -> str:
    """Safely converts arbitrary unicode text to Latin-1 compatible string without crashing."""
    if not text:
        return ""
    import re
    # Remove emojis and non-BMP symbols (surrogates) that crash FPDF fonts
    text = re.sub(r'[\U00010000-\U0010ffff]', '', str(text))
    replacements = {
        "—": "--",
        "–": "-",
        "―": "--",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "•": "*",
        "…": "...",
        "→": "->",
        "←": "<-",
        "⇒": "=>",
        "⇔": "<=>",
        "≥": ">=",
        "≤": "<=",
        "≠": "!=",
        "≈": "~",
        "±": "+/-",
        "×": "x",
        "÷": "/",
        "√": "sqrt",
        "∑": "sum",
        "∏": "prod",
        "∫": "integral",
        "°": " deg",
        "µ": "u",
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "θ": "theta",
        "λ": "lambda",
        "μ": "mu",
        "π": "pi",
        "σ": "sigma",
        "ω": "omega",
        "Δ": "Delta",
        "Ω": "Omega",
        "\u00A0": " ",
        "\u200B": "",
        "\u202F": " ",
        "✦": "*",
        "💡": "",
        "📐": "",
        "🔬": "",
        "📷": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")


def _find_system_ttf_font() -> tuple[Optional[str], Optional[str]]:
    """Locates system Unicode TrueType fonts (Regular, Bold)."""
    candidates = [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/freefont/FreeSans.ttf", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
    ]
    for reg, bold in candidates:
        if os.path.exists(reg):
            return reg, (bold if os.path.exists(bold) else None)
    return None, None


def build_study_guide_pdf(
    study_guide,
    output_path: Path,
    video_title: str = "Study Guide",
) -> Path:
    """
    Renders high-grade study guide HTML with KaTeX and compiles it to PDF
    using Headless Chrome. If Chrome is unavailable or fails, falls back to
    a crash-proof, multi-page Python PDF renderer with full chapter coverage.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Generate clean HTML
    html_content = _build_html(study_guide, video_title)
    html_path = output_path.parent / (output_path.stem + ".html")
    html_path.write_text(html_content, encoding="utf-8")
    log.info("Wrote study guide HTML to: %s", html_path)

    # 2. Render to PDF via Headless Chrome / Chromium
    chrome_bin = _find_chrome()
    if chrome_bin:
        log.info("Attempting PDF compilation via Chrome: %s", chrome_bin)
        cmd = [
            chrome_bin,
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--allow-file-access-from-files",
            "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            f"--print-to-pdf={output_path.resolve()}",
            str(html_path.resolve()),
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
            if output_path.exists() and output_path.stat().st_size > 500:
                log.info("Study guide PDF compiled successfully via Chrome! (%d bytes)", output_path.stat().st_size)
                return output_path
            else:
                log.warning("Chrome PDF generation exited %d (size: %s): %s",
                            res.returncode, output_path.stat().st_size if output_path.exists() else 0, res.stderr.decode("utf-8", errors="ignore"))
        except Exception as e:
            log.warning("Chrome execution failed: %s", e)

    # 3. Crash-Proof Python PDF Fallback (fpdf2)
    log.warning("Compiling study guide PDF via resilient Python fallback...")
    try:
        from fpdf import FPDF

        class StudyGuidePDF(FPDF):
            def footer(self):
                self.set_y(-15)
                self.set_font("Helvetica", "I", 8)
                self.set_text_color(140, 140, 140)
                self.set_x(self.l_margin)
                self.cell(0, 10, f"Page {self.page_no()}", 0, 0, "C")

        pdf = StudyGuidePDF(orientation="P", unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=18)

        # Check for system Unicode fonts
        reg_ttf, bold_ttf = _find_system_ttf_font()
        use_unicode_font = False
        font_family = "Helvetica"
        if reg_ttf:
            try:
                pdf.add_font("SysSans", "", reg_ttf)
                if bold_ttf:
                    pdf.add_font("SysSans", "B", bold_ttf)
                font_family = "SysSans"
                use_unicode_font = True
                log.info("Registered system TrueType font for PDF fallback: %s", reg_ttf)
            except Exception as fe:
                log.warning("Failed to register TrueType font: %s", fe)

        def safe_txt(t: str) -> str:
            if not t:
                return ""
            import re
            cleaned = re.sub(r'[\U00010000-\U0010ffff]', '', str(t))
            return cleaned if use_unicode_font else _clean_text_for_pdf(cleaned)

        def safe_cell(text: str, h: float = 6, ln: bool = True, align: str = "L"):
            pdf.set_x(pdf.l_margin)
            pdf.cell(0, h, safe_txt(text), ln=ln, align=align)

        def safe_multi(text: str, h: float = 5):
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, h, safe_txt(text))

        pdf.add_page()

        # Document Header
        pdf.set_font(font_family, "B", 18)
        pdf.set_text_color(30, 41, 59)
        safe_multi(study_guide.video_title if hasattr(study_guide, "video_title") and study_guide.video_title else video_title, h=8)
        pdf.ln(2)

        pdf.set_font(font_family, "I", 11)
        pdf.set_text_color(100, 116, 139)
        safe_cell("Comprehensive Academic Study Guide & Lecture Notes", h=6)
        pdf.ln(4)

        # Executive Overview
        summary = getattr(study_guide, "lecture_summary", "")
        if summary:
            pdf.set_fill_color(241, 245, 249)
            pdf.set_draw_color(203, 213, 225)
            pdf.set_font(font_family, "B", 11)
            pdf.set_text_color(15, 23, 42)
            safe_cell("Executive Lecture Overview", h=7)
            pdf.set_font(font_family, "", 9)
            safe_multi(summary, h=5)
            pdf.ln(5)

        # Table of Contents
        chapters = getattr(study_guide, "chapters", [])
        if chapters:
            pdf.set_font(font_family, "B", 12)
            pdf.set_text_color(37, 99, 235)
            safe_cell("Table of Contents", h=8)
            pdf.set_font(font_family, "", 9)
            pdf.set_text_color(51, 65, 85)
            for ch in chapters:
                safe_cell(f"  * Chapter {ch.chapter_num}: {ch.title}", h=5)
            pdf.ln(6)

        # Chapters
        for ch in chapters:
            c_num = ch.chapter_num
            c_title = ch.title
            c_sub = ch.subtitle

            pdf.ln(3)
            pdf.set_font(font_family, "B", 14)
            pdf.set_text_color(30, 58, 138)
            safe_cell(f"Chapter {c_num:02d}: {c_title}", h=8)

            if c_sub:
                pdf.set_font(font_family, "I", 10)
                pdf.set_text_color(100, 116, 139)
                safe_cell(c_sub, h=5)
                pdf.ln(2)

            if ch.introduction:
                pdf.set_font(font_family, "", 9)
                pdf.set_text_color(51, 65, 85)
                safe_multi(ch.introduction, h=5)
                pdf.ln(2)

            # Figures
            for fig in getattr(ch, "associated_figures", []):
                if fig.image_path and os.path.exists(fig.image_path):
                    try:
                        pdf.ln(2)
                        pdf.set_x(pdf.l_margin)
                        pdf.image(fig.image_path, w=min(140, pdf.epw))
                        pdf.ln(1)
                        if fig.caption:
                            pdf.set_font(font_family, "I", 8)
                            pdf.set_text_color(100, 116, 139)
                            safe_multi(f"Figure: {fig.caption}", h=4)
                            pdf.ln(2)
                    except Exception as img_err:
                        log.debug("Fallback PDF image render skipped: %s", img_err)

            # Content Paragraphs
            pdf.set_font(font_family, "", 9)
            pdf.set_text_color(30, 41, 59)
            for p in ch.content_paragraphs:
                if p and p.strip():
                    safe_multi(p.strip(), h=5)
                    pdf.ln(2)

            # Formulas
            if ch.latex_formulas:
                pdf.set_font(font_family, "B", 9)
                pdf.set_text_color(29, 78, 216)
                safe_cell("Key Formulations & Relations:", h=6)
                pdf.set_font(font_family, "", 8)
                pdf.set_text_color(30, 41, 59)
                for item in ch.latex_formulas:
                    if isinstance(item, dict):
                        f_eq = str(item.get("formula", "")).strip()
                        f_desc = str(item.get("description", "")).strip()
                    elif isinstance(item, str):
                        f_eq = item.strip()
                        f_desc = ""
                    else:
                        continue
                    if f_eq:
                        safe_multi(f"  Eq: {f_eq}", h=4)
                    if f_desc:
                        safe_multi(f"      {f_desc}", h=4)
                    pdf.ln(1)

            # Key Takeaways
            if ch.key_takeaways:
                pdf.ln(1)
                pdf.set_font(font_family, "B", 9)
                pdf.set_text_color(15, 23, 42)
                safe_cell("Key Takeaways:", h=5)
                pdf.set_font(font_family, "", 8)
                for take in ch.key_takeaways:
                    safe_multi(f"  * {take}", h=4)
                pdf.ln(2)

            # Instructor Notes
            if ch.instructor_notes and ch.instructor_notes.strip():
                pdf.set_font(font_family, "I", 8)
                pdf.set_text_color(180, 83, 9)
                safe_multi(f"Instructor Note: {ch.instructor_notes.strip()}", h=4)
                pdf.ln(2)

        pdf.output(str(output_path))
        log.info("Study guide PDF compiled via resilient fallback! Size: %d bytes", output_path.stat().st_size)
        return output_path

    except Exception as fallback_err:
        log.exception("Detailed fallback failed (%s). Writing resilient chapter PDF.", fallback_err)
        try:
            from fpdf import FPDF
            emergency_pdf = FPDF(orientation="P", unit="mm", format="A4")
            emergency_pdf.set_auto_page_break(auto=True, margin=15)
            emergency_pdf.add_page()
            emergency_pdf.set_font("Helvetica", "B", 16)
            title = getattr(study_guide, "video_title", video_title) or video_title
            emergency_pdf.multi_cell(0, 8, _clean_text_for_pdf(title))
            emergency_pdf.ln(3)

            summary = getattr(study_guide, "lecture_summary", "")
            if summary:
                emergency_pdf.set_font("Helvetica", "B", 12)
                emergency_pdf.cell(0, 8, "Executive Lecture Summary", ln=True)
                emergency_pdf.set_font("Helvetica", "", 10)
                emergency_pdf.multi_cell(0, 5, _clean_text_for_pdf(summary))
                emergency_pdf.ln(4)

            chapters = getattr(study_guide, "chapters", [])
            for ch in chapters:
                emergency_pdf.set_font("Helvetica", "B", 13)
                c_num = getattr(ch, "chapter_num", 1)
                c_title = _clean_text_for_pdf(getattr(ch, "title", "Chapter"))
                emergency_pdf.cell(0, 8, f"Chapter {c_num:02d}: {c_title}", ln=True)

                c_sub = _clean_text_for_pdf(getattr(ch, "subtitle", ""))
                if c_sub:
                    emergency_pdf.set_font("Helvetica", "I", 10)
                    emergency_pdf.cell(0, 5, c_sub, ln=True)

                emergency_pdf.set_font("Helvetica", "", 10)
                intro = getattr(ch, "introduction", "")
                if intro:
                    emergency_pdf.multi_cell(0, 5, _clean_text_for_pdf(intro))
                    emergency_pdf.ln(2)

                for p in getattr(ch, "content_paragraphs", []):
                    if p and p.strip():
                        emergency_pdf.multi_cell(0, 5, _clean_text_for_pdf(p.strip()))
                        emergency_pdf.ln(2)

                formulas = getattr(ch, "latex_formulas", [])
                if formulas:
                    emergency_pdf.set_font("Helvetica", "B", 9)
                    emergency_pdf.cell(0, 5, "Key Formulations & Rules:", ln=True)
                    emergency_pdf.set_font("Helvetica", "", 9)
                    for item in formulas:
                        if isinstance(item, dict):
                            f_eq = _clean_text_for_pdf(str(item.get("formula", "")).strip())
                            f_desc = _clean_text_for_pdf(str(item.get("description", "")).strip())
                        elif isinstance(item, str):
                            f_eq = _clean_text_for_pdf(item.strip())
                            f_desc = ""
                        else:
                            continue
                        if f_eq:
                            emergency_pdf.multi_cell(0, 4, f"  * {f_eq} - {f_desc}" if f_desc else f"  * {f_eq}")
                    emergency_pdf.ln(2)

                takeaways = getattr(ch, "key_takeaways", [])
                if takeaways:
                    emergency_pdf.set_font("Helvetica", "B", 9)
                    emergency_pdf.cell(0, 5, "Key Takeaways:", ln=True)
                    emergency_pdf.set_font("Helvetica", "", 9)
                    for t in takeaways:
                        emergency_pdf.multi_cell(0, 4, f"  - {_clean_text_for_pdf(str(t))}")
                    emergency_pdf.ln(3)

            emergency_pdf.output(str(output_path))
            log.info("Wrote full chapter PDF via resilient fallback (%d bytes)", output_path.stat().st_size)
            return output_path
        except Exception as emer_err:
            log.exception("Emergency PDF write failed: %s", emer_err)
            output_path.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj 3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000102 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n")
            return output_path

