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
        removeRuleIds: [DNR_RULE_ID, 2002],
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
          },
          {
            id: 2002,
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
              urlFilter: "*://*.youtube-nocookie.com/*",
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

const activeExtractions = new Map();

function cleanupExtraction(tabId, error = null, resultData = null) {
  if (!tabId || !activeExtractions.has(tabId)) return;
  const item = activeExtractions.get(tabId);
  activeExtractions.delete(tabId);

  if (item.timeout) {
    clearTimeout(item.timeout);
    item.timeout = null;
  }

  // Close the background extraction tab immediately
  try {
    chrome.tabs.remove(tabId).catch(() => {});
  } catch (e) {}

  if (item.sendResponse) {
    try {
      if (error) {
        item.sendResponse({ success: false, error });
      } else {
        item.sendResponse({ success: true, ...(resultData || {}) });
      }
    } catch (e) {}
  }

  if (item.originTabId) {
    chrome.tabs.sendMessage(item.originTabId, {
      action: "extraction_finished",
      success: !error,
      error: error,
      data: resultData
    }).catch(() => {});
  }
}

// 100% Silence Guarantee: Permanently enforce muting on extraction tabs
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (activeExtractions.has(tabId)) {
    if (changeInfo.muted === false || changeInfo.audible) {
      chrome.tabs.update(tabId, { muted: true }).catch(() => {});
    }
  }
});

chrome.tabs.onRemoved.addListener((closedTabId) => {
  if (activeExtractions.has(closedTabId)) {
    cleanupExtraction(closedTabId, "Slide extraction tab was closed before completing.");
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // 1. Silent Background Tab Extraction
  if (message.action === "start_background_extraction") {
    const originTabId = sender.tab?.id;
    const originUrl = message.origin_url || null;

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

    console.log("[YT2PDF Background] Opening 100% silent background tab for:", target.toString());

    // Open background tab (active: false ensures user is never interrupted)
    chrome.tabs.create({
      url: target.toString(),
      active: false
    }, (newTab) => {
      if (chrome.runtime.lastError || !newTab) {
        sendResponse({
          success: false,
          error: chrome.runtime.lastError?.message || "Failed to create extraction tab."
        });
        return;
      }

      const tabId = newTab.id;

      // IMMEDIATELY mute the tab at the browser level
      chrome.tabs.update(tabId, { muted: true }).catch(() => {});

      // Generous 180s (3 min) watchdog so longer videos never time out prematurely
      const timeout = setTimeout(() => {
        console.warn(`[YT2PDF Background] Extraction tab ${tabId} timed out after 180s.`);
        cleanupExtraction(tabId, "Slide extraction timed out after 3 minutes. Please ensure the video is publicly accessible and try again.");
      }, 180000);

      activeExtractions.set(tabId, {
        sendResponse,
        timeout,
        originTabId,
        originUrl
      });
    });

    return true; // Keep channel open for async response
  }

  // 2. Real-time progress update forwarding
  if (message.action === "headless_progress") {
    const tabId = sender.tab?.id;
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

  // 3. Extraction failed
  if (message.action === "headless_extraction_failed") {
    const tabId = sender.tab?.id;
    console.warn(`[YT2PDF Background] Extraction tab ${tabId} reported failure:`, message.error);
    cleanupExtraction(tabId, message.error || "Background slide extraction failed.");
    return false;
  }

  // 4. Extraction direct success
  if (message.action === "headless_extraction_direct_success") {
    const tabId = sender.tab?.id;
    cleanupExtraction(tabId, null, {
      job_id: message.data?.job_id,
      backend_base: message.data?.backend_base || "https://yt2pdfs.com",
      slide_count: message.slide_count || 0
    });
    return false;
  }

  // 5. Upload frames to backend
  if (message.action === "upload_frames") {
    (async () => {
      let tabId = sender.tab?.id;
      let pending = (tabId && activeExtractions.has(tabId)) ? activeExtractions.get(tabId) : null;
      if (!pending && activeExtractions.size > 0) {
        pending = activeExtractions.values().next().value;
        tabId = activeExtractions.keys().next().value;
      }

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
            sendResponse({ success: true, data: data });

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
        cleanupExtraction(tabId, lastError || "Failed to upload frames to processing servers.");
      }
    })();

    return true; // Keep message channel open for async response
  }

  // 6. Reliable Website Redirection (bypasses browser popup blockers)
  if (message.action === "open_website_tab" && message.url) {
    console.log("[YT2PDF Background] Opening website tab for:", message.url);
    chrome.tabs.create({ url: message.url, active: true }, (tab) => {
      sendResponse({ success: true, tab_id: tab?.id });
    });
    return true;
  }
});
