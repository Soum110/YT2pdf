"""
pdf_study_guide_compiler.py — High-fidelity Markdown-to-PDF Study Guide Compiler.

Converts structured markdown into a publication-grade, professionally styled PDF
featuring:
  - Clean cover title page with lecture title
  - Modern typography (Inter / Roboto sans-serif) with ample line height
  - Distinct styling for H1, H2, and H3 headers
  - Proper styling for bold text, bulleted lists, and code blocks
  - Page numbers and CSS page-break rules preventing orphaned headers
  - Crisp KaTeX math rendering
"""

import html
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Union

import markdown

log = logging.getLogger("pdf_study_guide_compiler")

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


def compile_markdown_to_pdf(
    markdown_content: str,
    output_path: Union[str, Path],
    video_title: str = "Lecture Study Guide",
    total_duration: float = 0.0,
) -> bool:
    """
    Renders study guide markdown into a styled, publication-grade PDF.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Convert Markdown to HTML
    md_html = markdown.markdown(
        markdown_content,
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
    )

    # 2. Build Full HTML Document with Cover Page and Paged Media CSS
    full_html = _build_html_document(
        body_html=md_html,
        video_title=video_title,
        total_duration=total_duration,
    )

    # 3. Compile to PDF via Headless Chromium
    chrome_bin = _find_chrome()
    if chrome_bin:
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tf:
                tf.write(full_html)
                temp_html_path = Path(tf.name)

            cmd = [
                chrome_bin,
                "--headless=new" if "Chrome" in chrome_bin else "--headless",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--run-all-compositor-stages-before-draw",
                f"--print-to-pdf={output_path.resolve()}",
                "--no-pdf-header-footer",
                temp_html_path.resolve().as_uri(),
            ]
            log.info("Compiling PDF with Chromium (%s)...", chrome_bin)
            res = subprocess.run(cmd, capture_output=True, timeout=60)
            try:
                temp_html_path.unlink(missing_ok=True)
            except Exception:
                pass

            if output_path.exists() and output_path.stat().st_size > 1500:
                log.info("Successfully compiled study guide PDF (%d bytes).", output_path.stat().st_size)
                return True
            else:
                log.warning("Chromium PDF compilation output invalid: %s", res.stderr.decode("utf-8", errors="ignore"))
        except Exception as e:
            log.warning("Chromium PDF compilation error: %s", e)

    # 4. Fallback: WeasyPrint if available
    try:
        import weasyprint
        log.info("Compiling PDF via WeasyPrint fallback...")
        weasyprint.HTML(string=full_html).write_pdf(str(output_path))
        if output_path.exists() and output_path.stat().st_size > 1500:
            log.info("WeasyPrint study guide PDF ready (%d bytes).", output_path.stat().st_size)
            return True
    except Exception as wp_err:
        log.warning("WeasyPrint fallback skipped: %s", wp_err)

    # 5. Fallback: Simple FPDF2 Text Compiler
    log.warning("Compiling fallback basic text PDF via FPDF2...")
    return _compile_fpdf_fallback(markdown_content, output_path, video_title)


def _build_html_document(body_html: str, video_title: str, total_duration: float = 0.0) -> str:
    """Builds a complete HTML document with cover page, KaTeX, and typography."""
    escaped_title = html.escape(video_title)
    mins = int(total_duration // 60)
    duration_str = f"{mins} min" if mins > 0 else "Lecture"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escaped_title} — Study Guide</title>
  <!-- KaTeX for Crisp LaTeX Math Rendering -->
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
    onload="renderMathInElement(document.body, {{delimiters: [{{left: '$$', right: '$$', display: true}}, {{left: '$', right: '$', display: false}}]}});"></script>

  <style>
    /* ─── Typography & Page Layout ─── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

    @page {{
      size: A4;
      margin: 20mm 15mm 20mm 15mm;
      @bottom-center {{
        content: counter(page);
        font-family: 'Inter', -apple-system, sans-serif;
        font-size: 8.5pt;
        color: #94a3b8;
      }}
    }}

    @page:first {{
      margin: 0;
      @bottom-center {{
        content: normal;
      }}
    }}

    * {{
      box-sizing: border-box;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }}

    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      font-size: 9.5pt;
      line-height: 1.65;
      color: #1e293b;
      background: #ffffff;
      margin: 0;
      padding: 0;
    }}

    /* ─── Premium Title Page ─── */
    .cover-page {{
      width: 100vw;
      height: 100vh;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      text-align: center;
      padding: 40mm 25mm;
      page-break-after: always;
      background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
      color: #ffffff;
    }}

    .cover-badge {{
      display: inline-block;
      font-size: 9pt;
      font-weight: 700;
      letter-spacing: 2px;
      text-transform: uppercase;
      color: #38bdf8;
      background: rgba(56, 189, 248, 0.15);
      border: 1px solid rgba(56, 189, 248, 0.3);
      padding: 5px 14px;
      border-radius: 20px;
      margin-bottom: 24px;
    }}

    .cover-title {{
      font-size: 26pt;
      font-weight: 800;
      line-height: 1.25;
      color: #ffffff;
      margin: 0 0 16px 0;
      max-width: 600px;
    }}

    .cover-subtitle {{
      font-size: 13pt;
      font-weight: 400;
      color: #94a3b8;
      margin: 0 0 36px 0;
      max-width: 500px;
      line-height: 1.5;
    }}

    .cover-meta-box {{
      display: flex;
      gap: 20px;
      justify-content: center;
      align-items: center;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.1);
      padding: 10px 24px;
      border-radius: 12px;
      font-size: 9pt;
      color: #cbd5e1;
    }}

    .cover-meta-item {{
      display: flex;
      align-items: center;
      gap: 6px;
    }}

    /* ─── Content Styling ─── */
    .content-container {{
      padding: 0;
    }}

    h1 {{
      font-size: 18pt;
      font-weight: 800;
      color: #0f172a;
      border-bottom: 2px solid #0284c7;
      padding-bottom: 6px;
      margin-top: 24pt;
      margin-bottom: 12pt;
      break-after: avoid;
    }}

    h2 {{
      font-size: 13pt;
      font-weight: 700;
      color: #0369a1;
      background: #f0f9ff;
      border-left: 4px solid #0284c7;
      padding: 6px 12px;
      border-radius: 0 6px 6px 0;
      margin-top: 18pt;
      margin-bottom: 10pt;
      break-after: avoid;
    }}

    h3 {{
      font-size: 10.5pt;
      font-weight: 700;
      color: #334155;
      margin-top: 12pt;
      margin-bottom: 6pt;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      break-after: avoid;
    }}

    p {{
      margin-top: 0;
      margin-bottom: 8pt;
      text-align: justify;
    }}

    strong {{
      font-weight: 700;
      color: #0f172a;
    }}

    ul, ol {{
      margin-top: 4pt;
      margin-bottom: 10pt;
      padding-left: 18pt;
    }}

    li {{
      margin-bottom: 4pt;
    }}

    /* ─── Code & Equations ─── */
    code {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 8.5pt;
      background: #f1f5f9;
      color: #0f172a;
      padding: 2px 5px;
      border-radius: 4px;
      border: 1px solid #e2e8f0;
    }}

    pre {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 8.5pt;
      background: #0f172a;
      color: #f8fafc;
      padding: 10pt 12pt;
      border-radius: 6px;
      overflow-x: auto;
      margin: 8pt 0 12pt 0;
      break-inside: avoid;
    }}

    pre code {{
      background: transparent;
      color: inherit;
      padding: 0;
      border: none;
    }}

    blockquote {{
      border-left: 3px solid #38bdf8;
      background: #f8fafc;
      margin: 8pt 0 12pt 0;
      padding: 8pt 14pt;
      color: #475569;
      border-radius: 0 6px 6px 0;
      break-inside: avoid;
    }}

    hr {{
      border: none;
      height: 1px;
      background: #e2e8f0;
      margin: 20pt 0;
      break-after: avoid;
    }}

    /* ─── Clean Page Breaks (No Orphans) ─── */
    h1, h2, h3, h4 {{
      break-after: avoid;
    }}

    table, blockquote, pre {{
      break-inside: avoid;
    }}
  </style>
</head>
<body>
  <!-- Title / Cover Page -->
  <div class="cover-page">
    <div class="cover-badge">Academic Monograph</div>
    <h1 class="cover-title">{escaped_title}</h1>
    <div class="cover-subtitle">Comprehensive, Pedagogical Study Guide & Master Lecture Notes</div>
    <div class="cover-meta-box">
      <div class="cover-meta-item">⏱ <strong>Duration:</strong> {duration_str}</div>
      <div class="cover-meta-item">🎓 <strong>Format:</strong> 6-Part Structured Pedagogy</div>
    </div>
  </div>

  <!-- Study Guide Content -->
  <main class="content-container">
    {body_html}
  </main>
</body>
</html>"""


def _compile_fpdf_fallback(markdown_text: str, output_path: Path, video_title: str) -> bool:
    """Basic fallback text-based PDF using fpdf2."""
    try:
        from fpdf import FPDF
        pdf = FPDF(orientation="P", unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 18)
        pdf.multi_cell(0, 10, video_title)
        pdf.ln(5)
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 6, "Comprehensive Academic Study Guide", ln=True)
        pdf.ln(8)

        pdf.set_font("Helvetica", "", 9)
        # Strip markdown syntax for basic fallback
        clean_text = re.sub(r'[*_#`]', '', markdown_text)
        for line in clean_text.split("\n"):
            line = line.strip()
            if line.startswith("---"):
                pdf.ln(3)
                continue
            if not line:
                pdf.ln(2)
                continue
            pdf.multi_cell(0, 5, line)

        pdf.output(str(output_path))
        return output_path.exists() and output_path.stat().st_size > 1000
    except Exception as e:
        log.error("FPDF fallback failed: %s", e)
        return False
