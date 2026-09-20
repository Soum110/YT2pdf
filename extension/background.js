/**
 * background.js — YT2PDF Companion Service Worker
 * Handles network requests with extension host permissions to bypass web-page CORS.
 */

const BACKEND_URLS = [
  "https://yt2pdf-214301889618.europe-west1.run.app",
  "https://yt2pdfs.com",
  "http://localhost:8080",
  "http://localhost:8000",
  "http://127.0.0.1:8080",
  "http://127.0.0.1:8000"
];

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "upload_frames") {
    (async () => {
      let lastError = null;
      for (const base of BACKEND_URLS) {
        try {
          console.log(`[YT2PDF Background] Attempting upload to ${base}...`);
          const res = await fetch(`${base}/api/companion/upload-frames`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Accept": "application/json"
            },
            body: JSON.stringify(message.payload)
          });

          if (res.ok) {
            const data = await res.json();
            console.log(`[YT2PDF Background] Upload successful to ${base}! Job ID:`, data.job_id);
            data.backend_base = base;
            sendResponse({ success: true, data: data });
            return;
          } else {
            const text = await res.text();
            console.warn(`[YT2PDF Background] Server ${base} returned HTTP ${res.status}:`, text);
            lastError = `Server returned HTTP ${res.status}`;
          }
        } catch (err) {
          console.warn(`[YT2PDF Background] Network error on ${base}:`, err.message);
          if (!lastError) lastError = err.message;
        }
      }

      sendResponse({
        success: false,
        error: lastError || "Failed to reach YT2PDFS processing servers."
      });
    })();

    return true; // Keep message channel open for async response
  }
});
