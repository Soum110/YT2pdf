/**
 * background.js — YT2PDF Companion Service Worker
 * Handles network requests with extension host permissions to bypass web-page CORS
 * and registers declarativeNetRequest rules for in-page silent iframe extraction.
 */

const BACKEND_URLS = [
  "https://yt2pdfs.com",
  "https://yt2pdf-214301889618.europe-west1.run.app",
  "http://localhost:8080",
  "http://localhost:8000",
  "http://127.0.0.1:8080",
  "http://127.0.0.1:8000"
];

const DNR_RULE_ID = 2001;

async function setupDNRRules() {
  try {
    if (chrome.declarativeNetRequest && chrome.declarativeNetRequest.updateDynamicRules) {
      await chrome.declarativeNetRequest.updateDynamicRules({
        removeRuleIds: [DNR_RULE_ID],
        addRules: [
          {
            id: DNR_RULE_ID,
            priority: 1,
            action: {
              type: "modifyHeaders",
              responseHeaders: [
                { header: "x-frame-options", operation: "remove" },
                { header: "content-security-policy", operation: "remove" },
                { header: "frame-options", operation: "remove" }
              ]
            },
            condition: {
              urlFilter: "*://*.youtube.com/*",
              resourceTypes: ["sub_frame"]
            }
          }
        ]
      });
      console.log("[YT2PDF Background] DeclarativeNetRequest rules registered for in-page silent iframe extraction.");
    }
  } catch (err) {
    console.warn("[YT2PDF Background] DNR rule registration notice:", err.message);
  }
}

setupDNRRules();
if (chrome.runtime?.onInstalled) chrome.runtime.onInstalled.addListener(setupDNRRules);
if (chrome.runtime?.onStartup) chrome.runtime.onStartup.addListener(setupDNRRules);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // 1. Silent Background Extraction handshake
  if (message.action === "start_background_extraction") {
    sendResponse({ success: true, method: "in_page_iframe" });
    return false;
  }

  // 2. Upload frames to backend (bypasses CORS using host permissions)
  if (message.action === "upload_frames") {
    (async () => {
      const candidateBases = [];
      if (message.origin_url && !candidateBases.includes(message.origin_url)) {
        candidateBases.push(message.origin_url);
      }
      for (const b of BACKEND_URLS) {
        if (!candidateBases.includes(b)) {
          candidateBases.push(b);
        }
      }

      let lastError = null;
      for (const base of candidateBases) {
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
