/**
 * background.js — YT2PDF Companion Service Worker
 * Handles network requests with extension host permissions to bypass web-page CORS.
 */

const BACKEND_URLS = [
  "https://yt2pdfs.com",
  "https://yt2pdf-214301889618.europe-west1.run.app",
  "http://localhost:8080",
  "http://localhost:8000",
  "http://127.0.0.1:8080",
  "http://127.0.0.1:8000"
];

// Track pending silent background extractions: tabId -> { sendResponse, timeout }
const activeExtractions = new Map();

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // 1. Silent Background Extraction requested from YT2PDFS Website
  if (message.action === "start_background_extraction") {
    try {
      let rawUrl = message.video_url;
      try {
        const u = new URL(rawUrl);
        let videoId = "";
        if (u.hostname.includes("youtu.be")) {
          videoId = u.pathname.replace(/^\//, "").split("?")[0];
        } else if (u.pathname.includes("/shorts/")) {
          videoId = u.pathname.split("/shorts/")[1]?.split("/")[0];
        } else if (u.searchParams.has("v")) {
          videoId = u.searchParams.get("v");
        }
        if (videoId) {
          rawUrl = `https://www.youtube.com/watch?v=${videoId}`;
        }
      } catch (e) {}

      const target = new URL(rawUrl);
      target.searchParams.set("autoplay", "1");
      target.searchParams.set("mute", "1");
      target.searchParams.set("yt2pdf_headless", "1");

      const originTabId = sender.tab?.id;
      console.log("[YT2PDF Background] Launching high-speed extraction tab for:", target.toString(), "Origin tab:", originTabId);

      chrome.tabs.create({
        url: target.toString(),
        active: false,
        muted: true
      }, (tab) => {
        if (chrome.runtime.lastError || !tab) {
          sendResponse({
            success: false,
            error: chrome.runtime.lastError?.message || "Failed to create extraction tab."
          });
          return;
        }

        const tabId = tab.id;
        const timeout = setTimeout(() => {
          console.warn(`[YT2PDF Background] Tab ${tabId} timed out during slide extraction.`);
          chrome.tabs.remove(tabId).catch(() => {});
          if (originTabId) {
            chrome.tabs.update(originTabId, { active: true }).catch(() => {});
          }
          if (activeExtractions.has(tabId)) {
            const pending = activeExtractions.get(tabId);
            activeExtractions.delete(tabId);
            pending.sendResponse({
              success: false,
              error: "Slide extraction timed out. Falling back to server pipeline."
            });
          }
        }, 35000);

        activeExtractions.set(tabId, { sendResponse, timeout, originTabId });
      });
    } catch (e) {
      sendResponse({ success: false, error: "Invalid video URL: " + e.message });
    }

    return true; // Keep channel open for async response
  }

  // 2. Headless tab reported failure
  if (message.action === "headless_extraction_failed") {
    const tabId = sender.tab?.id;
    if (tabId && activeExtractions.has(tabId)) {
      const pending = activeExtractions.get(tabId);
      clearTimeout(pending.timeout);
      chrome.tabs.remove(tabId).catch(() => {});
      if (pending.originTabId) {
        chrome.tabs.update(pending.originTabId, { active: true }).catch(() => {});
      }
      activeExtractions.delete(tabId);
      pending.sendResponse({ success: false, error: message.error || "Background extraction failed." });
    }
    return false;
  }

  // 3. Upload frames to backend (shared by both YouTube button & silent background tab)
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

            // If this upload came from a silent background tab, complete the pending web bridge request and close tab!
            const tabId = sender.tab?.id;
            if (tabId && activeExtractions.has(tabId)) {
              console.log(`[YT2PDF Background] Resolving silent extraction for tab ${tabId} with Job ID ${data.job_id}`);
              const pending = activeExtractions.get(tabId);
              clearTimeout(pending.timeout);
              chrome.tabs.remove(tabId).catch(() => {});
              if (pending.originTabId) {
                chrome.tabs.update(pending.originTabId, { active: true }).catch(() => {});
              }
              activeExtractions.delete(tabId);
              pending.sendResponse({
                success: true,
                job_id: data.job_id,
                backend_base: base,
                slide_count: message.payload?.frames?.length || 0
              });
            }

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

// Watch for premature background tab closures
chrome.tabs.onRemoved.addListener((closedTabId) => {
  if (activeExtractions.has(closedTabId)) {
    const pending = activeExtractions.get(closedTabId);
    clearTimeout(pending.timeout);
    if (pending.originTabId) {
      chrome.tabs.update(pending.originTabId, { active: true }).catch(() => {});
    }
    activeExtractions.delete(closedTabId);
    pending.sendResponse({
      success: false,
      error: "Extraction tab was closed before completing."
    });
  }
});
