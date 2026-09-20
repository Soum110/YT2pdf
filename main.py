"""
main.py — FastAPI web server for YT2PDFS
"""
from __future__ import annotations

# Ensure our project directory is first on sys.path to avoid
# conflicts with installed packages (e.g. yt-dlp extractor plugins)
import sys, os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import json
import logging
import os
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import aiofiles
from typing import Any, Dict, List, Optional
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl
from starlette.exceptions import HTTPException as StarletteHTTPException

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("main")

JOBS_ROOT = Path("./jobs")
JOBS_ROOT.mkdir(exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
CONTACT_RECIPIENT_EMAIL = os.environ.get("CONTACT_EMAIL", "sensoumen176@gmail.com")

# Thread pool for background processing (max 3 concurrent jobs)
executor = ThreadPoolExecutor(max_workers=3)

app = FastAPI(title="YT2PDFS", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ensure_pot_server():
    """Starts the bgutil-pot HTTP server on 127.0.0.1:4416 if binary is present."""
    import shutil
    import subprocess
    import urllib.request

    bgutil_bin = shutil.which("bgutil-pot") or "/usr/local/bin/bgutil-pot"
    if not os.path.exists(bgutil_bin) and not shutil.which("bgutil-pot"):
        log.info("bgutil-pot binary not found; skipping POT server startup")
        return

    # Check if already running on port 4416
    try:
        req = urllib.request.Request("http://127.0.0.1:4416/ping")
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            if resp.status == 200:
                log.info("bgutil-pot server is already running on 127.0.0.1:4416")
                return
    except Exception:
        pass

    try:
        log_file = open("/tmp/bgutil-server.log", "a")
        proc = subprocess.Popen(
            [bgutil_bin, "server", "--host", "0.0.0.0", "--port", "4416"],
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
        log.info("Started bgutil-pot server (PID %d) on 0.0.0.0:4416", proc.pid)
    except Exception as e:
        log.warning("Failed to launch bgutil-pot server: %s", e)


def ensure_extension_zip():
    """Packages the extension/ folder into static/yt2pdf-extension.zip if needed."""
    import zipfile
    ext_dir = Path("extension")
    zip_path = Path("static/yt2pdf-extension.zip")
    if not ext_dir.exists():
        return
    ext_files = [
        f for f in ext_dir.rglob("*")
        if f.is_file() and not f.name.startswith(".") and not f.name.endswith(".py") and "__pycache__" not in str(f)
    ]
    if not ext_files:
        return
    max_mtime = max(f.stat().st_mtime for f in ext_files)
    if zip_path.exists() and zip_path.stat().st_mtime >= max_mtime:
        return

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in ext_files:
            rel = f.relative_to(ext_dir)
            zf.write(f, arcname=str(rel))
    log.info("Packaged Chrome extension to %s (%d bytes)", zip_path, zip_path.stat().st_size)


@app.on_event("startup")
async def on_startup():
    _ensure_pot_server()
    ensure_extension_zip()


# ─────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────
class ProcessRequest(BaseModel):
    video_url: str


class ProcessResponse(BaseModel):
    job_id: str
    message: str


class CompanionFrame(BaseModel):
    timestamp: float
    time_formatted: Optional[str] = None
    data: str


class CompanionUploadRequest(BaseModel):
    video_url: str
    title: str = "Presentation Slides"
    duration: Optional[float] = 0.0
    frames: list[CompanionFrame]


class RebuildSlidesRequest(BaseModel):
    selected_filenames: list[str]


class ContactRequest(BaseModel):
    name: str
    email: str
    subject: str = ""
    message: str


# ─────────────────────────────────────────────
# Background job runner
# ─────────────────────────────────────────────
def _run_job(job_id: str, video_url: str):
    """Runs in a background thread via ThreadPoolExecutor."""
    from pipeline import run_pipeline
    run_pipeline(
        job_id=job_id,
        video_url=video_url,
        jobs_root=JOBS_ROOT,
        gemini_api_key=GEMINI_API_KEY,
    )


# ─────────────────────────────────────────────
# API Endpoints
# ─────────────────────────────────────────────

@app.post("/api/process", response_model=ProcessResponse)
async def process_video(req: ProcessRequest):
    """Start a new slide extraction job."""
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY environment variable is not set on the server.",
        )

    url = req.video_url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="video_url is required.")

    job_id = str(uuid.uuid4())[:8]  # Short ID like "a3f92c1d"
    job_dir = JOBS_ROOT / job_id

    # Check cache first (instant 0-second response if previously converted)
    from drive_cache import cache_manager, extract_youtube_id
    video_id = extract_youtube_id(url)
    if video_id and cache_manager.get_cached_job(video_id, job_dir):
        log.info("Instant Cache Hit for video %s! Serving as job %s", video_id, job_id)
        return ProcessResponse(job_id=job_id, message="Cached! Result ready instantly.")

    job_dir.mkdir(parents=True, exist_ok=True)

    # Write initial status immediately
    import json
    (job_dir / "status.json").write_text(json.dumps({
        "stage": "queued",
        "progress": 1,
        "message": "Job queued. Starting soon...",
        "slide_count": 0,
        "error": "",
        "updated_at": time.time(),
    }))

    # Launch in thread pool (non-blocking)
    executor.submit(_run_job, job_id, url)

    log.info("Job %s queued for: %s", job_id, url)
    return ProcessResponse(job_id=job_id, message="Processing started.")


@app.post("/api/companion/upload-frames")
async def upload_companion_frames(req: CompanionUploadRequest):
    """
    Accepts client-captured slide frames directly from the YT2PDF Slide Companion Chrome extension.
    Bypasses YouTube datacenter bot detection by capturing frames in the client's browser.
    """
    if not req.frames:
        raise HTTPException(status_code=400, detail="No frames provided in payload.")

    job_id = str(uuid.uuid4())[:8]
    job_dir = JOBS_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    from pipeline import _write_status, run_pipeline_from_frames

    _write_status(
        job_dir,
        "companion_received",
        20,
        f"Received {len(req.frames)} frames from browser extension. Starting AI pipeline...",
    )

    frames_dicts = [f.model_dump() if hasattr(f, "model_dump") else f.dict() for f in req.frames]

    executor.submit(
        run_pipeline_from_frames,
        job_id=job_id,
        frames_data=frames_dicts,
        video_title=req.title,
        video_url=req.video_url,
        duration=req.duration or 0.0,
        jobs_root=JOBS_ROOT,
        gemini_api_key=GEMINI_API_KEY,
    )

    log.info("Companion job %s started for %s with %d frames", job_id, req.video_url, len(req.frames))
    return JSONResponse(content={
        "job_id": job_id,
        "status": "processing",
        "message": f"Successfully received {len(req.frames)} frames. Processing started."
    })


@app.api_route("/api/extension/download", methods=["GET", "HEAD"])
async def download_extension():
    """Direct download for the packaged YT2PDF Slide Companion Chrome Extension (.zip)."""
    zip_path = Path("static/yt2pdf-extension.zip")
    if not zip_path.exists():
        ensure_extension_zip()
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="Extension package not found.")
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename="yt2pdf-companion-extension.zip",
        headers={"Content-Disposition": "attachment; filename=yt2pdf-companion-extension.zip"},
    )



@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    """Poll job status."""
    status_file = JOBS_ROOT / job_id / "status.json"
    if not status_file.exists():
        raise HTTPException(status_code=404, detail="Job not found.")

    import json
    data = json.loads(status_file.read_text())
    return JSONResponse(content=data)


@app.api_route("/api/download/{job_id}", methods=["GET", "HEAD"])
async def download_slides_pdf(job_id: str):
    """Download the slides PDF."""
    pdf_path = JOBS_ROOT / job_id / "output.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Slides PDF not ready yet or job not found.")

    import json
    meta_file = JOBS_ROOT / job_id / "meta.json"
    filename = "slides.pdf"
    if meta_file.exists():
        meta = json.loads(meta_file.read_text())
        raw_title = meta.get("title", "slides")
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in raw_title)
        filename = f"{safe[:55].strip()}_slides.pdf"

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.api_route("/api/download/{job_id}/guide", methods=["GET", "HEAD"])
async def download_guide_pdf(job_id: str):
    """Download the AI study guide PDF."""
    pdf_path = JOBS_ROOT / job_id / "study_guide.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Study guide PDF not ready yet or job not found.")

    import json
    meta_file = JOBS_ROOT / job_id / "meta.json"
    filename = "study_guide.pdf"
    if meta_file.exists():
        meta = json.loads(meta_file.read_text())
        raw_title = meta.get("title", "study_guide")
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in raw_title)
        filename = f"{safe[:50].strip()}_study_guide.pdf"

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/outputs/{job_id}")
async def get_outputs(job_id: str):
    """Check which output files are available for a completed job."""
    import json
    outputs_file = JOBS_ROOT / job_id / "outputs.json"
    if outputs_file.exists():
        return JSONResponse(content=json.loads(outputs_file.read_text()))
    # Fallback: check files directly
    return JSONResponse(content={
        "slides_pdf": (JOBS_ROOT / job_id / "output.pdf").exists(),
        "study_guide_pdf": (JOBS_ROOT / job_id / "study_guide.pdf").exists(),
        "slide_count": 0,
    })


@app.get("/api/slides/{job_id}")
async def list_job_slides(job_id: str):
    """List all extracted slides for preview and selective curation."""
    slides_dir = JOBS_ROOT / job_id / "slides"
    if not slides_dir.exists():
        raise HTTPException(status_code=404, detail="Slides directory not found for this job.")

    files = sorted(slides_dir.glob("slide_*.png"))
    slide_list = []
    import re
    for idx, f in enumerate(files, 1):
        m = re.search(r"t(\d+)s", f.name)
        ts = int(m.group(1)) if m else 0
        mins, secs = divmod(ts, 60)
        time_str = f"{mins:02d}:{secs:02d}"
        slide_list.append({
            "index": idx,
            "filename": f.name,
            "timestamp_sec": ts,
            "timestamp_str": time_str,
            "title": f"Slide {idx:02d} ({time_str})",
            "image_url": f"/api/slide_image/{job_id}/{f.name}",
        })
    return JSONResponse(content={"slides": slide_list, "total": len(slide_list)})


@app.get("/api/slide_image/{job_id}/{filename}")
async def get_slide_image(job_id: str, filename: str):
    """Serve slide image file with caching for rapid gallery thumbnail rendering."""
    # Sanitize filename
    safe_name = os.path.basename(filename)
    img_path = JOBS_ROOT / job_id / "slides" / safe_name
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Slide image not found.")
    return FileResponse(
        path=str(img_path),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.post("/api/rebuild_slides_pdf/{job_id}")
async def rebuild_slides_pdf(job_id: str, req: RebuildSlidesRequest):
    """Rebuild slides PDF with only the user-approved subset of slides."""
    slides_dir = JOBS_ROOT / job_id / "slides"
    if not slides_dir.exists():
        raise HTTPException(status_code=404, detail="Slides not found for this job.")

    selected = [s.strip() for s in req.selected_filenames if s.strip()]
    if not selected:
        raise HTTPException(status_code=400, detail="At least one slide must be selected.")

    valid_paths = []
    titles = []
    import re
    for fn in selected:
        safe_name = os.path.basename(fn)
        p = slides_dir / safe_name
        if p.exists():
            valid_paths.append(p)
            m = re.search(r"t(\d+)s", safe_name)
            ts = int(m.group(1)) if m else 0
            mins, secs = divmod(ts, 60)
            titles.append(f"Slide ({mins:02d}:{secs:02d})")

    if not valid_paths:
        raise HTTPException(status_code=400, detail="None of the selected slide files exist on disk.")

    from pdf_builder import build_pdf
    import json
    meta_file = JOBS_ROOT / job_id / "meta.json"
    video_title = "Presentation Slides"
    if meta_file.exists():
        meta = json.loads(meta_file.read_text())
        video_title = meta.get("title", video_title)

    out_pdf = JOBS_ROOT / job_id / "output.pdf"
    build_pdf(
        image_paths=valid_paths,
        slide_titles=titles,
        output_path=out_pdf,
        video_title=video_title,
        include_cover=True,
    )

    # Update outputs.json with new slide count
    outputs_file = JOBS_ROOT / job_id / "outputs.json"
    guide_ready = (JOBS_ROOT / job_id / "study_guide.pdf").exists()
    outputs_file.write_text(json.dumps({
        "slides_pdf": True,
        "study_guide_pdf": guide_ready,
        "slide_count": len(valid_paths),
    }))

    log.info("Rebuilt slides PDF for job %s with %d slides (was %d)", job_id, len(valid_paths), len(selected))
    return JSONResponse(content={"success": True, "slide_count": len(valid_paths)})


@app.post("/api/contact")
async def handle_contact_form(req: ContactRequest):
    """Handle contact form submissions, record locally, and forward to sensoumen176@gmail.com."""
    name = req.name.strip()
    email = req.email.strip()
    subject = req.subject.strip()
    message = req.message.strip()

    if not name or not email or not message:
        raise HTTPException(status_code=400, detail="Name, email, and message are required.")

    # 1. Always record the inquiry to disk
    inquiry_record = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "name": name,
        "email": email,
        "subject": subject,
        "message": message,
    }
    inquiries_file = JOBS_ROOT / "inquiries.jsonl"
    try:
        with open(inquiries_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(inquiry_record) + "\n")
    except Exception as e:
        log.warning("Could not persist contact inquiry: %s", e)

    # 2. Forward email to recipient via FormSubmit relay
    try:
        payload = {
            "name": name,
            "email": email,
            "_replyto": email,
            "_subject": f"[YT2PDFS Contact] {subject or 'New Inquiry from ' + name}",
            "message": message,
            "_template": "table",
        }
        formsubmit_url = f"https://formsubmit.co/ajax/{CONTACT_RECIPIENT_EMAIL}"
        post_data = json.dumps(payload).encode("utf-8")
        req_obj = urllib.request.Request(
            formsubmit_url,
            data=post_data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": "https://yt2pdfs.com/contact",
                "Origin": "https://yt2pdfs.com",
            },
        )
        with urllib.request.urlopen(req_obj, timeout=8) as resp:
            resp_body = resp.read().decode("utf-8")
            log.info("Contact form relayed to %s: %s", CONTACT_RECIPIENT_EMAIL, resp_body)
    except Exception as e:
        log.error("Failed to forward contact form email: %s", e)

    return JSONResponse(content={"success": True, "message": "Your message has been received! We will get back to you shortly."})


@app.get("/api/health")
async def health():
    import shutil
    from pipeline import _normalize_netscape_cookies
    cookies_val = os.environ.get("YOUTUBE_COOKIES", "")
    cookie_file = Path("cookies.txt")
    if not cookie_file.exists():
        cookie_file = Path(__file__).parent / "cookies.txt"
    valid_cookies_count = 0
    cookies_source = "none"

    if cookie_file.exists():
        cookies_source = "cookies.txt"
        try:
            import http.cookiejar
            cj = http.cookiejar.MozillaCookieJar(str(cookie_file))
            cj.load()
            valid_cookies_count = len(cj)
        except Exception:
            pass
    elif cookies_val:
        cookies_source = "env"
        try:
            import http.cookiejar, tempfile
            clean = _normalize_netscape_cookies(cookies_val)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8") as f:
                f.write(clean)
                f.flush()
                cj = http.cookiejar.MozillaCookieJar(f.name)
                cj.load()
                valid_cookies_count = len(cj)
        except Exception:
            pass

    pot_server_running = False
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:4416/ping", timeout=0.5) as resp:
            pot_server_running = (resp.status == 200)
    except Exception:
        pass

    bgutil_diag = {}
    bgutil_bin = shutil.which("bgutil-pot") or "/usr/local/bin/bgutil-pot"
    if os.path.exists(bgutil_bin):
        try:
            import subprocess
            r = subprocess.run([bgutil_bin, "--version"], capture_output=True, text=True, timeout=2)
            bgutil_diag["version"] = r.stdout.strip() or r.stderr.strip()
            bgutil_diag["returncode"] = r.returncode
        except Exception as e:
            bgutil_diag["error"] = str(e)
    else:
        bgutil_diag["found"] = False

    deno_diag = {}
    deno_bin = shutil.which("deno") or "/usr/local/bin/deno"
    if os.path.exists(deno_bin):
        try:
            import subprocess
            r = subprocess.run([deno_bin, "--version"], capture_output=True, text=True, timeout=2)
            deno_diag["version"] = r.stdout.strip().splitlines()[0] if r.stdout else r.stderr.strip()
            deno_diag["returncode"] = r.returncode
        except Exception as e:
            deno_diag["error"] = str(e)
    else:
        deno_diag["found"] = False

    server_log = ""
    if os.path.exists("/tmp/bgutil-server.log"):
        try:
            server_log = Path("/tmp/bgutil-server.log").read_text(encoding="utf-8", errors="ignore")[-500:]
        except Exception:
            pass

    return {
        "status": "ok",
        "commit": "companion-v1",
        "api_key_set": bool(GEMINI_API_KEY),
        "cookies_set": bool(cookies_val) or cookie_file.exists(),
        "cookies_source": cookies_source,
        "valid_cookies_count": valid_cookies_count,
        "bgutil_available": bool(shutil.which("bgutil-pot") or os.path.exists("/usr/local/bin/bgutil-pot")),
        "bgutil_diag": bgutil_diag,
        "deno_available": bool(shutil.which("deno") or os.path.exists("/usr/local/bin/deno")),
        "deno_diag": deno_diag,
        "pot_server_running": pot_server_running,
        "pot_server_log": server_log,
    }



@app.api_route("/", methods=["GET", "HEAD"])
async def serve_index():
    """Serve index.html with strict no-cache headers so browser always gets latest UI."""
    response = FileResponse("static/index.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.api_route("/robots.txt", methods=["GET", "HEAD"])
async def serve_robots(request: Request):
    base_url = os.environ.get("SITE_URL", "https://yt2pdfs.com").rstrip("/")
    robots_path = Path("static/robots.txt")
    if robots_path.exists():
        content = robots_path.read_text(encoding="utf-8")
        return Response(content=content, media_type="text/plain")
    return Response(
        content=f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /jobs/\n\nSitemap: {base_url}/sitemap.xml\n",
        media_type="text/plain",
    )


@app.api_route("/sitemap.xml", methods=["GET", "HEAD"])
async def serve_sitemap(request: Request):
    base_url = os.environ.get("SITE_URL", "https://yt2pdfs.com").rstrip("/")
    sitemap_path = Path("static/sitemap.xml")
    if sitemap_path.exists():
        content = sitemap_path.read_text(encoding="utf-8")
        return Response(content=content, media_type="application/xml")
    return Response(content="<xml></xml>", media_type="application/xml", status_code=404)


@app.api_route("/favicon.ico", methods=["GET", "HEAD"])
async def serve_favicon():
    return FileResponse("static/favicon.ico", media_type="image/x-icon")


@app.api_route("/brand-icon.png", methods=["GET", "HEAD"])
async def serve_brand_icon():
    return FileResponse("static/brand-icon.png", media_type="image/png")


@app.api_route("/brand-icon-tight.png", methods=["GET", "HEAD"])
async def serve_brand_icon_tight():
    return FileResponse("static/brand-icon-tight.png", media_type="image/png")


@app.api_route("/apple-touch-icon.png", methods=["GET", "HEAD"])
async def serve_apple_touch_icon():
    return FileResponse("static/apple-touch-icon.png", media_type="image/png")


@app.api_route("/about", methods=["GET", "HEAD"])
async def serve_about():
    response = FileResponse("static/about.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.api_route("/contact", methods=["GET", "HEAD"])
async def serve_contact():
    response = FileResponse("static/contact.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.api_route("/privacy", methods=["GET", "HEAD"])
async def serve_privacy():
    response = FileResponse("static/privacy.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.api_route("/terms", methods=["GET", "HEAD"])
async def serve_terms():
    response = FileResponse("static/terms.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.api_route("/404", methods=["GET", "HEAD"])
async def serve_404():
    response = FileResponse("static/404.html", status_code=404)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.api_route("/500", methods=["GET", "HEAD"])
async def serve_500():
    response = FileResponse("static/500.html", status_code=500)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# ─────────────────────────────────────────────
# Custom Exception Handlers (HTML Error Pages)
# ─────────────────────────────────────────────
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        # Serve custom 404.html for browser navigation
        if not request.url.path.startswith("/api/") and "text/html" in request.headers.get("accept", "text/html"):
            return FileResponse("static/404.html", status_code=404)
        return JSONResponse(status_code=404, content={"detail": exc.detail or "Not Found"})
    if exc.status_code == 500:
        if not request.url.path.startswith("/api/") and "text/html" in request.headers.get("accept", "text/html"):
            return FileResponse("static/500.html", status_code=500)
        return JSONResponse(status_code=500, content={"detail": exc.detail or "Internal Server Error"})
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("Unhandled server exception on %s: %s", request.url.path, exc)
    if not request.url.path.startswith("/api/") and "text/html" in request.headers.get("accept", "text/html"):
        return FileResponse("static/500.html", status_code=500)
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


# ─────────────────────────────────────────────
# Serve static assets
# ─────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static_assets")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    _ensure_pot_server()
    port = int(os.environ.get("PORT", 8000))
    log.info("Starting YT2PDFS server on http://localhost:%d", port)
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
