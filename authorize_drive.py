"""
authorize_drive.py — One-time OAuth 2.0 authorization for personal 5TB Google Drive.
Generates token.json containing the user's refresh token and credentials.
"""

import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/drive']
CLIENT_SECRET_FILE = Path("client_secret.json")
TOKEN_FILE = Path("token.json")
FOLDER_ID = "1RYCL9B8OkOJ0m8VL7f7Rhk3q4gMSjBor"


def main():
    if not CLIENT_SECRET_FILE.exists():
        print(f"Error: {CLIENT_SECRET_FILE} not found!")
        sys.exit(1)

    print("=" * 60)
    print("YT2PDF: Google Drive 5TB Storage Authorization")
    print("=" * 60)
    print("\nStarting local authorization flow...")
    print("A browser window will open automatically asking you to log into Google and allow Drive access.")
    print("If it does not open, please copy and paste the URL shown below into your browser.\n")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_SECRET_FILE),
        scopes=SCOPES
    )

    creds = flow.run_local_server(port=0, open_browser=True)

    TOKEN_FILE.write_text(creds.to_json())
    print(f"\nAuthorization successful! Credentials saved to {TOKEN_FILE}.")

    # Test Drive access
    print("\nVerifying access to Google Drive folder...")
    service = build('drive', 'v3', credentials=creds, cache_discovery=False)
    folder = service.files().get(fileId=FOLDER_ID, fields="id, name, capabilities").execute()
    print(f"Folder Name: '{folder.get('name')}' (ID: {folder.get('id')})")
    can_add = folder.get("capabilities", {}).get("canAddChildren", False)
    print(f"Write permissions verified: {can_add}")

    # List any existing files
    results = service.files().list(
        q=f"'{FOLDER_ID}' in parents and trashed = false",
        fields="files(id, name, size)",
        pageSize=10
    ).execute()
    files = results.get("files", [])
    print(f"Existing files in cache folder: {len(files)}")
    for f in files:
        print(f"  - {f.get('name')} (ID: {f.get('id')})")

    print("\nGoogle Drive 5TB Cache is fully connected and ready to use!")


if __name__ == '__main__':
    main()
