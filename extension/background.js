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

async function uploadAudioStream(jobId, audioUrl, backendBase) {
  if (!jobId || !audioUrl) return;
  try {
    console.log(`[YT2PDF Background] Fetching audio stream for job ${jobId}...`);
    const audioResp = await fetch(audioUrl);
    if (!audioResp.ok) {
      console.warn(`[YT2PDF Background] Audio fetch notice: ${audioResp.statusText}`);
      return;
    }
    const blob = await audioResp.blob();
    console.log(`[YT2PDF Background] Audio fetched (${Math.round(blob.size / 1024)} KB). Uploading to backend...`);
    const formData = new FormData();
    formData.append("file", blob, "lecture_audio.mp4");
    const targetUrl = `${backendBase || "https://yt2pdfs.com"}/api/companion/upload-audio?job_id=${jobId}`;
    const postRes = await fetch(targetUrl, {
      method: "POST",
      body: formData,
    });
    if (postRes.ok) {
      console.log(`[YT2PDF Background] Audio track successfully saved for job ${jobId}`);
    } else {
      console.warn(`[YT2PDF Background] Audio upload notice: HTTP ${postRes.status}`);
    }
  } catch (e) {
    console.warn("[YT2PDF Background] Audio stream upload notice:", e.message);
  }
}

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

function findExtraction(sender) {
  const tabId = sender?.tab?.id;
  const winId = sender?.tab?.windowId;
  if (tabId && activeExtractions.has(tabId)) return activeExtractions.get(tabId);
  if (winId && activeExtractions.has(`win_${winId}`)) return activeExtractions.get(`win_${winId}`);
  for (const item of activeExtractions.values()) {
    if ((tabId && item.tabId === tabId) || (winId && item.windowId === winId)) {
      return item;
    }
  }
  return activeExtractions.size > 0 ? activeExtractions.values().next().value : null;
}

function cleanupExtraction(identifier, error = null, resultData = null) {
  let item = null;
  if (identifier && activeExtractions.has(identifier)) {
    item = activeExtractions.get(identifier);
  } else {
    for (const [k, v] of activeExtractions.entries()) {
      if (k === identifier || v.tabId === identifier || v.windowId === identifier || `win_${v.windowId}` === identifier) {
        item = v;
        break;
      }
    }
  }
  if (!item && activeExtractions.size > 0) {
    item = activeExtractions.values().next().value;
  }
  if (!item) return;

  // Clean all keys pointing to this record
  for (const [k, v] of Array.from(activeExtractions.entries())) {
    if (v === item) activeExtractions.delete(k);
  }

  if (item.timeout) {
    clearTimeout(item.timeout);
    item.timeout = null;
  }

  // Close the background extraction window or tab immediately
  try {
    if (item.windowId) {
      chrome.windows.remove(item.windowId).catch(() => {});
    }
  } catch (e) {}
  try {
    if (item.tabId) {
      chrome.tabs.remove(item.tabId).catch(() => {});
    }
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

  // Direct foreground redirection if requested (e.g. from YouTube watch page)
  if (!error && resultData?.job_id && item.redirectOnSuccess && item.originTabId) {
    let webBase = resultData.backend_base || "https://yt2pdfs.com";
    if (!webBase.includes("localhost") && !webBase.includes("127.0.0.1")) {
      webBase = "https://yt2pdfs.com";
    }
    const destUrl = `${webBase}/?job_id=${resultData.job_id}`;
    console.log("[YT2PDF Background] Direct foreground redirection for origin tab to:", destUrl);
    chrome.tabs.update(item.originTabId, { url: destUrl }).catch(() => {});
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

chrome.windows.onRemoved.addListener((closedWinId) => {
  if (activeExtractions.has(`win_${closedWinId}`)) {
    cleanupExtraction(`win_${closedWinId}`, "Slide extraction window was closed before completing.");
  }
});

chrome.tabs.onRemoved.addListener((closedTabId) => {
  if (activeExtractions.has(closedTabId)) {
    cleanupExtraction(closedTabId, "Slide extraction tab was closed before completing.");
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // 1. Silent Background Extraction
  if (message.action === "start_background_extraction") {
    // Extraction now runs 100% in-page via silent invisible iframe.
    // Zero browser tabs and zero OS windows are ever created!
    console.log("[YT2PDF Background] In-page silent iframe extraction active.");
    sendResponse({ success: true, mode: "in_page_frame" });
    return false;
  }

  // 2. Real-time progress update forwarding & watchdog keepalive
  if (message.action === "headless_progress") {
    const pending = findExtraction(sender);
    if (pending) {
      // Keep extraction alive on active progress: reset watchdog
      if (pending.timeout) {
        clearTimeout(pending.timeout);
        const targetId = pending.tabId || pending.windowId;
        pending.timeout = setTimeout(() => {
          console.warn(`[YT2PDF Background] Extraction stalled with no progress for 3 minutes.`);
          cleanupExtraction(targetId, "Slide extraction stalled. Please try again.");
        }, 180000);
      }
      if (pending.originTabId) {
        chrome.tabs.sendMessage(pending.originTabId, {
          action: "extraction_progress_update",
          current: message.current,
          total: message.total,
          stage: message.stage
        }).catch(() => {});
      }
    }
    return false;
  }

  // 3. Extraction failed
  if (message.action === "headless_extraction_failed") {
    const item = findExtraction(sender);
    console.warn("[YT2PDF Background] Extraction reported failure:", message.error);
    cleanupExtraction(item?.tabId || item?.windowId || sender.tab?.id, message.error || "Background slide extraction failed.");
    return false;
  }

  // 4. Extraction direct success
  if (message.action === "headless_extraction_direct_success") {
    const item = findExtraction(sender);
    console.log("[YT2PDF Background] Headless extraction direct success reported! Job ID:", message.data?.job_id);
    cleanupExtraction(item?.tabId || item?.windowId || sender.tab?.id, null, {
      job_id: message.data?.job_id,
      backend_base: message.data?.backend_base || "https://yt2pdfs.com",
      slide_count: message.slide_count || message.data?.slide_count || 0
    });
    return false;
  }

  // 5. Upload frames to backend
  if (message.action === "upload_frames") {
    (async () => {
      const pending = findExtraction(sender);
      let tabId = pending?.tabId || sender.tab?.id;

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

            // Stream audio track in the background service worker if audio_url was provided
            if (message.payload?.audio_url && data.job_id) {
              uploadAudioStream(data.job_id, message.payload.audio_url, base).catch((aErr) => {
                console.warn("[YT2PDF Background] Audio upload stream notice:", aErr.message);
              });
            }

            // Direct Redirection Guarantee: Redirect the origin tab or open foreground tab
            const shouldRedirect = Boolean(message.redirect_on_success || message.payload?.redirect_on_success || pending?.redirectOnSuccess);
            if (shouldRedirect) {
              let webBase = "https://yt2pdfs.com";
              if (base.includes("localhost") || base.includes("127.0.0.1")) {
                webBase = base;
              }
              const destUrl = `${webBase}/?job_id=${data.job_id}`;
              console.log("[YT2PDF Background] Direct foreground redirection for:", destUrl);
              if (pending && pending.originTabId) {
                chrome.tabs.update(pending.originTabId, { url: destUrl }).catch(() => {});
              }
            }

            if (pending) {
              cleanupExtraction(pending.tabId || pending.windowId || tabId, null, {
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
        cleanupExtraction(pending.tabId || pending.windowId || tabId, lastError || "Failed to upload frames to processing servers.");
      }
    })();

    return true; // Keep message channel open for async response
  }

  // 6. Reliable Website Redirection (bypasses browser popup blockers)
  if (message.action === "open_website_tab" && message.url) {
    console.log("[YT2PDF Background] Opening website tab for:", message.url);
    try {
      chrome.tabs.create({ url: message.url, active: true }, (tab) => {
        sendResponse({ success: true, tab_id: tab?.id });
      });
    } catch (tabErr) {
      console.warn("[YT2PDF Background] Failed to open tab:", tabErr);
      sendResponse({ success: false, error: tabErr.message });
    }
    return true;
  }
});
