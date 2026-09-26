"""
drive_cache.py — Multi-tier Video Cache (Local Disk + Google Drive / Cloud Storage)
==================================================================================
1. Extracts YouTube Video ID from any URL.
2. Checks local disk cache (fast 0.05ms lookup).
3. If not found locally, checks Google Drive / Cloud Storage (5TB storage).
4. On cache hit: serves PDF instantly (< 1 second) with 0 CPU & 0 Gemini API cost.
5. On new conversion: saves to local cache and uploads to Google Drive in background.
"""

import os
import re
import json
import shutil
import logging
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("drive_cache")

# Root cache directory on local disk
CACHE_DIR = Path("jobs/_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def extract_youtube_id(url: str) -> Optional[str]:
    """Extract standard 11-character YouTube video ID from various URL formats."""
    if not url:
        return None
    url = url.strip()
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?#]|$)',
        r'(?:embed\/|v\/|shorts\/)([0-9A-Za-z_-]{11})',
        r'youtu\.be\/([0-9A-Za-z_-]{11})',
        r'^([0-9A-Za-z_-]{11})$'
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


class DriveCacheManager:
    """Manages local and Google Drive / Cloud Storage caching."""

    def __init__(self):
        self.folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip() or "1RYCL9B8OkOJ0m8VL7f7Rhk3q4gMSjBor"
        self.token_file = os.environ.get("GOOGLE_DRIVE_TOKEN_FILE", "").strip() or "token.json"
        self.token_json = os.environ.get("GOOGLE_DRIVE_OAUTH_TOKEN", "").strip()
        self.service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        self.service_account_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        if not self.service_account_file and Path("service_account.json").exists():
            self.service_account_file = str(Path("service_account.json").resolve())
        self._drive_service = None
        self._init_drive_client()

    def _init_drive_client(self):
        """Initialize Google Drive API client if credentials are provided."""
        if not self.folder_id:
            log.info("GOOGLE_DRIVE_FOLDER_ID not set. Running in local-disk cache mode.")
            return

        try:
            from google.oauth2.credentials import Credentials
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request
            import google.auth
            from googleapiclient.discovery import build

            scopes = ['https://www.googleapis.com/auth/drive']

            creds = None

            # 1. Prioritize OAuth 2.0 User Token (consumes personal 5TB Google One quota)
            if self.token_json:
                try:
                    info = json.loads(self.token_json)
                    creds = Credentials.from_authorized_user_info(info, scopes=scopes)
                except Exception as e:
                    log.error("Failed to parse GOOGLE_DRIVE_OAUTH_TOKEN: %s", e)
            elif Path(self.token_file).exists():
                try:
                    creds = Credentials.from_authorized_user_file(self.token_file, scopes=scopes)
                except Exception as e:
                    log.error("Failed to load token file %s: %s", self.token_file, e)

            # Automatically refresh user token if expired
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    if Path(self.token_file).exists():
                        Path(self.token_file).write_text(creds.to_json())
                    log.info("Google Drive OAuth token refreshed successfully.")
                except Exception as e:
                    log.warning("Failed to refresh OAuth token: %s", e)

            # 2. Fall back to Service Account / ADC if user token is not present
            if not creds:
                if self.service_account_json:
                    try:
                        info = json.loads(self.service_account_json)
                        creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
                    except Exception as e:
                        log.error("Failed to parse GOOGLE_SERVICE_ACCOUNT_JSON: %s", e)
                elif self.service_account_file and os.path.exists(self.service_account_file):
                    creds = service_account.Credentials.from_service_account_file(self.service_account_file, scopes=scopes)
                else:
                    try:
                        creds, _ = google.auth.default(scopes=scopes)
                        log.info("Using Google Cloud Application Default Credentials (native Cloud Run identity).")
                    except Exception as adc_err:
                        log.debug("ADC not available: %s", adc_err)

            if not creds:
                log.info("Google Drive credentials not found. Running in local-disk cache mode.")
                return

            self._drive_service = build('drive', 'v3', credentials=creds, cache_discovery=False)
            log.info("Google Drive 5TB storage client connected successfully.")
        except Exception as e:
            log.warning("Could not initialize Google Drive client (%s). Using local cache.", e)
            self._drive_service = None

    def get_cached_job(self, video_id: str, target_job_dir: Path) -> bool:
        """
        Check if video_id is cached.
        If found (locally or in Google Drive), copy/link files into target_job_dir
        and return True. Otherwise return False.
        """
        if not video_id:
            return False

        local_cached_dir = CACHE_DIR / video_id
        pdf_path = local_cached_dir / "output.pdf"
        meta_path = local_cached_dir / "meta.json"

        # 1. Check Local Cache
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            log.info("[Cache HIT - Local] Video %s found in local cache.", video_id)
            return self._populate_job_from_cache(local_cached_dir, target_job_dir)

        # 2. Check Google Drive Cloud Cache
        if self._drive_service and self.folder_id:
            try:
                found = self._fetch_from_google_drive(video_id, local_cached_dir)
                if found:
                    log.info("[Cache HIT - Google Drive] Video %s downloaded from Google Drive.", video_id)
                    return self._populate_job_from_cache(local_cached_dir, target_job_dir)
            except Exception as e:
                log.warning("Error fetching from Google Drive cache: %s", e)

        return False

    def _populate_job_from_cache(self, source_dir: Path, target_job_dir: Path) -> bool:
        """Copy cached results into the active job directory so the user gets instant results."""
        target_job_dir.mkdir(parents=True, exist_ok=True)

        for filename in ["output.pdf", "study_guide.pdf", "meta.json", "outputs.json"]:
            src = source_dir / filename
            if src.exists():
                dst = target_job_dir / filename
                try:
                    shutil.copy2(src, dst)
                except Exception as e:
                    log.error("Failed to copy %s: %s", filename, e)

        # Copy slides directory if present
        if (source_dir / "slides").exists():
            try:
                shutil.copytree(source_dir / "slides", target_job_dir / "slides", dirs_exist_ok=True)
            except Exception as e:
                log.debug("Slides directory copy skipped: %s", e)

        # Read meta to get title & slide count
        meta_file = target_job_dir / "meta.json"
        slide_count = 0
        video_title = "Video"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text())
                slide_count = meta.get("slide_count", 0)
                video_title = meta.get("title", "Video")
            except Exception:
                pass

        outputs_file = target_job_dir / "outputs.json"
        has_guide = (target_job_dir / "study_guide.pdf").exists()
        if not outputs_file.exists():
            outputs_file.write_text(json.dumps({
                "slides_pdf": True,
                "study_guide_pdf": has_guide,
                "slide_count": slide_count,
            }))

        # Write instant completed status
        (target_job_dir / "status.json").write_text(json.dumps({
            "stage": "completed",
            "progress": 100,
            "message": f"Instant Cache Hit! {slide_count} slides ready.",
            "slide_count": slide_count,
            "error": "",
            "cached": True,
            "updated_at": 9999999999.0,
        }))

        return True

    def save_job_to_cache(self, video_id: str, job_dir: Path):
        """Save a newly completed job to local cache and upload to Google Drive in background."""
        if not video_id:
            return

        local_cached_dir = CACHE_DIR / video_id
        local_cached_dir.mkdir(parents=True, exist_ok=True)

        # Copy completed files into local cache
        for filename in ["output.pdf", "study_guide.pdf", "meta.json", "outputs.json"]:
            src = job_dir / filename
            if src.exists():
                shutil.copy2(src, local_cached_dir / filename)

        if (job_dir / "slides").exists():
            try:
                shutil.copytree(job_dir / "slides", local_cached_dir / "slides", dirs_exist_ok=True)
            except Exception as e:
                log.debug("Slides directory cache store skipped: %s", e)

        log.info("[Cache STORE] Saved video %s to local cache.", video_id)

        # Asynchronously upload to Google Drive so the user request isn't blocked
        if self._drive_service and self.folder_id:
            threading.Thread(
                target=self._upload_to_google_drive,
                args=(video_id, local_cached_dir),
                daemon=True,
            ).start()

    def _fetch_from_google_drive(self, video_id: str, local_cached_dir: Path) -> bool:
        """Download cached PDF and meta from Google Drive folder."""
        from googleapiclient.http import MediaIoBaseDownload
        import io

        query = f"'{self.folder_id}' in parents and name contains '{video_id}_' and trashed = false"
        results = self._drive_service.files().list(
            q=query,
            fields="files(id, name)",
            pageSize=10
        ).execute()

        files = results.get("files", [])
        if not files:
            return False

        local_cached_dir.mkdir(parents=True, exist_ok=True)
        downloaded_any = False

        for f in files:
            name = f["name"]
            file_id = f["id"]

            target_filename = None
            if name == f"{video_id}_slides.pdf":
                target_filename = "output.pdf"
            elif name == f"{video_id}_guide.pdf":
                target_filename = "study_guide.pdf"
            elif name == f"{video_id}_meta.json":
                target_filename = "meta.json"
            elif name == f"{video_id}_outputs.json":
                target_filename = "outputs.json"

            if target_filename:
                request = self._drive_service.files().get_media(fileId=file_id)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                
                (local_cached_dir / target_filename).write_bytes(fh.getvalue())
                downloaded_any = True

        return downloaded_any and (local_cached_dir / "output.pdf").exists()

    def _upload_to_google_drive(self, video_id: str, local_cached_dir: Path):
        """Upload cached PDF and meta into Google Drive folder."""
        try:
            from googleapiclient.http import MediaFileUpload

            uploads = [
                ("output.pdf", f"{video_id}_slides.pdf", "application/pdf"),
                ("study_guide.pdf", f"{video_id}_guide.pdf", "application/pdf"),
                ("meta.json", f"{video_id}_meta.json", "application/json"),
                ("outputs.json", f"{video_id}_outputs.json", "application/json"),
            ]

            for local_name, drive_name, mime_type in uploads:
                file_path = local_cached_dir / local_name
                if not file_path.exists():
                    continue

                # Check if already exists in drive to prevent duplicates
                q = f"'{self.folder_id}' in parents and name = '{drive_name}' and trashed = false"
                res = self._drive_service.files().list(q=q, fields="files(id)").execute()
                if res.get("files"):
                    continue

                media = MediaFileUpload(str(file_path), mimetype=mime_type, resumable=True)
                file_metadata = {
                    "name": drive_name,
                    "parents": [self.folder_id]
                }
                self._drive_service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields="id"
                ).execute()
                log.info("Uploaded %s to Google Drive 5TB cache.", drive_name)

        except Exception as e:
            log.error("Failed to upload %s to Google Drive: %s", video_id, e)


# Global singleton cache instance
cache_manager = DriveCacheManager()
