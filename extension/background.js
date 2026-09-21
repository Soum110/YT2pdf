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

// Track pending silent background extractions: tabId -> { sendResponse, timeout, originTabId, windowId, originUrl }
const activeExtractions = new Map();

function cleanupExtraction(tabId, errorMsg = null, successData = null) {
  let pending = (tabId && activeExtractions.has(tabId)) ? activeExtractions.get(tabId) : null;
  if (!pending && activeExtractions.size > 0) {
    tabId = activeExtractions.keys().next().value;
    pending = activeExtractions.get(tabId);
  }
  if (!pending) return;

  if (pending.timeout) {
    clearTimeout(pending.timeout);
  }
  activeExtractions.delete(tabId);

  // Close the offscreen extraction window completely (or tab if fallback)
  if (pending.windowId) {
    chrome.windows.remove(pending.windowId).catch(() => {});
  } else if (tabId) {
    chrome.tabs.remove(tabId).catch(() => {});
  }

  // Refocus user's origin tab if needed
  if (pending.originTabId) {
    chrome.tabs.update(pending.originTabId, { active: true }).catch(() => {});
  }

  try {
    if (successData) {
      pending.sendResponse({
        success: true,
        job_id: successData.job_id,
        backend_base: successData.backend_base || "https://yt2pdfs.com",
        slide_count: successData.slide_count || 0
      });
    } else {
      pending.sendResponse({
        success: false,
        error: errorMsg || "Background slide extraction failed."
      });
    }
  } catch (e) {}
}

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
      const originUrl = message.origin_url || null;
      console.log("[YT2PDF Background] Launching offscreen extraction window for:", target.toString(), "Origin:", originUrl);

      // Open in a completely isolated off-screen popup window so NO TAB appears in the user's tab section
      chrome.windows.create({
        url: target.toString(),
        type: "popup",
        focused: false,
        left: 25000,
        top: 25000,
        width: 800,
        height: 600
      }, (win) => {
        if (chrome.runtime.lastError || !win) {
          console.warn("[YT2PDF Background] chrome.windows.create failed, falling back to tab:", chrome.runtime.lastError?.message);
          // Fallback to active: false tab only if window creation failed
          chrome.tabs.create({ url: target.toString(), active: false }, (tab) => {
            if (chrome.runtime.lastError || !tab) {
              sendResponse({ success: false, error: chrome.runtime.lastError?.message || "Failed to create extraction process." });
              return;
            }
            registerExtraction(tab.id, null, originTabId, originUrl, sendResponse);
          });
          return;
        }

        const windowId = win.id;
        if (win.tabs && win.tabs.length > 0 && win.tabs[0].id) {
          registerExtraction(win.tabs[0].id, windowId, originTabId, originUrl, sendResponse);
        } else {
          chrome.tabs.query({ windowId: windowId }, (tabs) => {
            const tabId = (tabs && tabs[0]) ? tabs[0].id : null;
            registerExtraction(tabId, windowId, originTabId, originUrl, sendResponse);
          });
        }
      });
    } catch (e) {
      sendResponse({ success: false, error: "Invalid video URL: " + e.message });
    }

    return true; // Keep channel open for async response
  }

  function registerExtraction(tabId, windowId, originTabId, originUrl, sendResponse) {
    if (tabId) {
      try {
        chrome.tabs.update(tabId, { muted: true }).catch(() => {});
      } catch (e) {}
    }

    const key = tabId || `win_${windowId}`;
    const timeout = setTimeout(() => {
      console.warn(`[YT2PDF Background] Extraction for window ${windowId} (tab ${tabId}) timed out.`);
      cleanupExtraction(key, "Slide extraction timed out. Please check that the video is publicly playable and try again.");
    }, 75000);

    activeExtractions.set(key, { sendResponse, timeout, originTabId, windowId, originUrl });
  }

  // 1b. Headless extraction progress forwarding to website
  if (message.action === "headless_progress") {
    let tabId = sender.tab?.id;
    let pending = (tabId && activeExtractions.has(tabId)) ? activeExtractions.get(tabId) : null;
    if (!pending && activeExtractions.size > 0) {
      pending = activeExtractions.values().next().value;
    }
    if (pending && pending.originTabId) {
      chrome.tabs.sendMessage(pending.originTabId, {
        action: "extraction_progress_update",
        current: message.current,
        total: message.total
      }).catch(() => {});
    }
    return false;
  }

  // 2. Headless tab reported failure
  if (message.action === "headless_extraction_failed") {
    let tabId = sender.tab?.id;
    cleanupExtraction(tabId, message.error || "Background slide extraction failed.");
    return false;
  }

  // 2b. Headless tab reported direct upload success
  if (message.action === "headless_extraction_direct_success") {
    let tabId = sender.tab?.id;
    cleanupExtraction(tabId, null, {
      job_id: message.data?.job_id,
      backend_base: message.data?.backend_base || "https://yt2pdfs.com",
      slide_count: message.slide_count || 0
    });
    return false;
  }

  // 3. Upload frames to backend
  if (message.action === "upload_frames") {
    (async () => {
      let tabId = sender.tab?.id;
      let pending = (tabId && activeExtractions.has(tabId)) ? activeExtractions.get(tabId) : null;
      if (!pending && activeExtractions.size > 0) {
        pending = activeExtractions.values().next().value;
        tabId = activeExtractions.keys().next().value;
      }

      // Prioritize candidate base URLs (originUrl first if provided)
      const candidateBases = [];
      if (pending?.originUrl && !candidateBases.includes(pending.originUrl)) {
        candidateBases.push(pending.originUrl);
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

            // Resolve calling tab first
            sendResponse({ success: true, data: data });

            // If this upload came from silent background extraction, resolve bridge promise and destroy window!
            if (pending) {
              cleanupExtraction(tabId, null, {
                job_id: data.job_id,
                backend_base: base,
                slide_count: message.payload?.frames?.length || 0
              });
            }
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

      if (pending) {
        cleanupExtraction(tabId, lastError || "Failed to upload frames to YT2PDFS servers.");
      }
    })();

    return true; // Keep message channel open for async response
  }
});

// Watch for premature window closures
chrome.windows.onRemoved.addListener((closedWindowId) => {
  for (const [key, pending] of activeExtractions.entries()) {
    if (pending.windowId === closedWindowId) {
      cleanupExtraction(key, "Background extraction window was closed.");
      break;
    }
  }
});

// Watch for premature background tab closures
chrome.tabs.onRemoved.addListener((closedTabId) => {
  if (activeExtractions.has(closedTabId)) {
    cleanupExtraction(closedTabId, "Extraction tab was closed before completing.");
  }
});
