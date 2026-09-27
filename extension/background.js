/**
 * background.js — YT2PDF Companion Service Worker
 * Manages silent background slide extraction windows, enforces 100% audio muting,
 * ensures fail-safe self-destruction of background windows, and handles safe
 * redirection to the YT2PDF Slide Studio web application.
 */

const DNR_RULE_ID = 2001;

// ─────────────────────────────────────────────
// Target Website Base Resolution (Strict Security: Never target arbitrary ports like 3000)
// ─────────────────────────────────────────────
function resolveTargetBase(tabs) {
  const DEFAULT_BASE = "https://yt2pdfs.com";
  if (!tabs || !Array.isArray(tabs)) return DEFAULT_BASE;

  for (const t of tabs) {
    if (!t.url) continue;
    try {
      const u = new URL(t.url);
      // Only match official YT2PDF domains or local development server on port 8000/8080
      if (u.hostname === "yt2pdfs.com" || u.hostname === "www.yt2pdfs.com") {
        return `${u.protocol}//${u.host}`;
      }
      if ((u.hostname === "localhost" || u.hostname === "127.0.0.1") && (u.port === "8000" || u.port === "8080")) {
        return `${u.protocol}//${u.host}`;
      }
    } catch (e) {}
  }

  return DEFAULT_BASE;
}

// ─────────────────────────────────────────────
// Orphaned Headless Window/Tab Purge (Self-Cleaning on startup/wake)
// ─────────────────────────────────────────────
function purgeOrphanedHeadlessWindows() {
  try {
    if (chrome.windows && chrome.windows.getAll) {
      chrome.windows.getAll({ populate: true }, (windows) => {
        if (chrome.runtime.lastError || !windows) return;
        for (const win of windows) {
          for (const tab of (win.tabs || [])) {
            if (tab.url && tab.url.includes("yt2pdf_headless=1")) {
              console.log(`[YT2PDF Background] Purging orphaned headless window ${win.id} / tab ${tab.id}`);
              try { chrome.windows.remove(win.id); } catch (e) {}
              try { chrome.tabs.remove(tab.id); } catch (e) {}
            }
          }
        }
      });
    }
  } catch (e) {}
}

purgeOrphanedHeadlessWindows();
if (chrome.runtime?.onStartup) chrome.runtime.onStartup.addListener(purgeOrphanedHeadlessWindows);
if (chrome.runtime?.onInstalled) chrome.runtime.onInstalled.addListener(purgeOrphanedHeadlessWindows);

// ─────────────────────────────────────────────
// Dynamic declarativeNetRequest Rules (for embedded headers)
// ─────────────────────────────────────────────
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
    }
  } catch (err) {
    console.warn("[YT2PDF Background] DNR notice:", err.message);
  }
}

setupDNRRules();
if (chrome.runtime?.onInstalled) chrome.runtime.onInstalled.addListener(setupDNRRules);

// ─────────────────────────────────────────────
// Active Extractions Lifecycle & Watchdogs
// ─────────────────────────────────────────────
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

  // Clear all mappings pointing to this record
  for (const [k, v] of Array.from(activeExtractions.entries())) {
    if (v === item) activeExtractions.delete(k);
  }

  if (item.watchdog) {
    clearTimeout(item.watchdog);
    item.watchdog = null;
  }

  // Self-Destruction: Forcefully remove the background extraction window and tab immediately
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

// ─────────────────────────────────────────────
// Message Router
// ─────────────────────────────────────────────
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {

  // 1. Launch Silent Background Extraction (Does NOT disrupt user's active tab)
  if (message.action === "start_background_extraction") {
    const originTabId = sender.tab?.id;
    const videoId = message.videoId;
    const duration = message.duration || 0;
    const videoTitle = message.videoTitle || "Presentation Slides";

    // Clean up any stale extraction for this origin tab
    if (originTabId) {
      cleanupExtraction(originTabId);
    }

    const extractionRecord = {
      originTabId: originTabId,
      videoId: videoId,
      duration: duration,
      videoTitle: videoTitle,
      attempts: 1,
      windowId: null,
      tabId: null,
      watchdog: null
    };

    const targetUrl = `https://www.youtube.com/watch?v=${videoId}&yt2pdf_headless=1&yt2pdf_duration=${duration}&yt2pdf_title=${encodeURIComponent(videoTitle)}`;

    // Fail-safe watchdog: forceful cleanup after 45 seconds
    extractionRecord.watchdog = setTimeout(() => {
      console.warn(`[YT2PDF Background] Watchdog fired for extraction of ${videoId}. Self-destructing.`);
      cleanupExtraction(extractionRecord.tabId || extractionRecord.windowId, "Slide extraction timed out after multiple attempts.");
    }, 45000);

    const winOptions = {
      url: targetUrl,
      focused: false,
      state: "minimized",
      type: "popup",
      width: 400,
      height: 300,
      left: 25000,
      top: 25000
    };

    chrome.windows.create(winOptions, (newWin) => {
      if (chrome.runtime.lastError || !newWin) {
        console.warn("[YT2PDF Background] windows.create fallback to background tab:", chrome.runtime.lastError?.message);
        chrome.tabs.create({ url: targetUrl, active: false }, (newTab) => {
          if (chrome.runtime.lastError || !newTab) {
            cleanupExtraction(originTabId, "Failed to launch background extraction tab.");
            return;
          }
          chrome.tabs.update(newTab.id, { muted: true }).catch(() => {});
          extractionRecord.tabId = newTab.id;
          activeExtractions.set(newTab.id, extractionRecord);
        });
        return;
      }

      extractionRecord.windowId = newWin.id;
      activeExtractions.set(`win_${newWin.id}`, extractionRecord);

      const tabId = (newWin.tabs && newWin.tabs.length > 0) ? newWin.tabs[0].id : null;
      if (tabId) {
        chrome.tabs.update(tabId, { muted: true }).catch(() => {});
        extractionRecord.tabId = tabId;
        activeExtractions.set(tabId, extractionRecord);
      } else {
        chrome.tabs.query({ windowId: newWin.id }, (tabs) => {
          const tId = tabs?.[0]?.id;
          if (tId) {
            chrome.tabs.update(tId, { muted: true }).catch(() => {});
            extractionRecord.tabId = tId;
            activeExtractions.set(tId, extractionRecord);
          }
        });
      }
    });

    sendResponse({ success: true, mode: "background_window" });
    return true;
  }

  // 2. Real-time Progress Forwarding from Headless Window to Active Tab
  if (message.action === "headless_progress") {
    const pending = findExtraction(sender);
    if (pending) {
      if (pending.watchdog) {
        clearTimeout(pending.watchdog);
        pending.watchdog = setTimeout(() => {
          console.warn("[YT2PDF Background] Extraction stalled for 25s with no new frames.");
          cleanupExtraction(pending.tabId || pending.windowId, "Slide extraction stalled.");
        }, 25000);
      }
      if (pending.originTabId) {
        chrome.tabs.sendMessage(pending.originTabId, {
          action: "extraction_progress_update",
          current: message.current,
          total: message.total
        }).catch(() => {});
      }
    }
    return false;
  }

  // 3. Extraction Success: Save Deck, Destroy Background Window, Route to Web Studio
  if (message.action === "headless_extraction_complete") {
    const pending = findExtraction(sender);
    const deck = message.deck;
    const deckId = deck?.deckId || `deck_${Date.now()}`;
    const slideCount = message.slideCount || deck?.slideCount || 0;

    // Save presentation deck in extension local storage
    chrome.storage.local.set({
      [deckId]: deck,
      latest_deck_id: deckId
    }, async () => {
      // Find open tabs and resolve correct target base (strictly avoids unrelated dev ports like 3000)
      let allTabs = [];
      try { allTabs = await chrome.tabs.query({}); } catch (e) {}
      const targetBase = resolveTargetBase(allTabs);
      const destUrl = `${targetBase}/?deck_id=${deckId}`;

      console.log(`[YT2PDF Background] Slides extracted (${slideCount} slides). Opening web studio: ${destUrl}`);

      // Open the Slide Studio on the web in a new tab
      chrome.tabs.create({ url: destUrl, active: true }, (openedTab) => {
        if (chrome.runtime.lastError) {
          console.warn("[YT2PDF Background] Notice creating web studio tab:", chrome.runtime.lastError.message);
        }
      });

      // Fail-Safe: Forcefully remove and destroy the background extraction window/tab immediately!
      if (pending) {
        cleanupExtraction(pending.tabId || pending.windowId, null, {
          deckId: deckId,
          slideCount: slideCount,
          url: destUrl
        });
      } else {
        if (sender.tab?.windowId) chrome.windows.remove(sender.tab.windowId).catch(() => {});
        if (sender.tab?.id) chrome.tabs.remove(sender.tab.id).catch(() => {});
      }
    });

    sendResponse({ success: true, deckId: deckId });
    return true;
  }

  // 4. Extraction Failed (Retry mechanism + Fail-Safe Self-Destruction)
  if (message.action === "headless_extraction_failed") {
    const pending = findExtraction(sender);
    if (pending && pending.attempts < 2) {
      // Retry once automatically
      pending.attempts++;
      console.log(`[YT2PDF Background] Extraction attempt 1 failed (${message.error}). Retrying attempt 2...`);

      // Destroy the failed window first
      if (pending.windowId) {
        chrome.windows.remove(pending.windowId).catch(() => {});
        activeExtractions.delete(`win_${pending.windowId}`);
      }
      if (pending.tabId) {
        chrome.tabs.remove(pending.tabId).catch(() => {});
        activeExtractions.delete(pending.tabId);
      }

      // Re-create silent window for attempt 2
      const targetUrl = `https://www.youtube.com/watch?v=${pending.videoId}&yt2pdf_headless=1&yt2pdf_duration=${pending.duration}&yt2pdf_title=${encodeURIComponent(pending.videoTitle)}`;
      chrome.windows.create({
        url: targetUrl,
        focused: false,
        state: "minimized",
        type: "popup",
        width: 400,
        height: 300,
        left: 25000,
        top: 25000
      }, (newWin) => {
        if (newWin?.id) {
          pending.windowId = newWin.id;
          activeExtractions.set(`win_${newWin.id}`, pending);
          const tId = newWin.tabs?.[0]?.id;
          if (tId) {
            chrome.tabs.update(tId, { muted: true }).catch(() => {});
            pending.tabId = tId;
            activeExtractions.set(tId, pending);
          }
        }
      });
      return false;
    }

    // Several tries failed: completely remove window and notify
    console.warn(`[YT2PDF Background] Extraction failed after ${pending?.attempts || 1} attempts: ${message.error}`);
    cleanupExtraction(pending?.tabId || pending?.windowId || sender.tab?.id, message.error || "Background slide extraction failed.");
    return false;
  }

  // 5. Open Web Studio from active tab button click
  if (message.action === "open_deck_page") {
    const deckId = message.deckId;
    (async () => {
      let allTabs = [];
      try { allTabs = await chrome.tabs.query({}); } catch (e) {}
      const targetBase = resolveTargetBase(allTabs);
      const destUrl = `${targetBase}/?deck_id=${deckId}`;

      console.log("[YT2PDF Background] Opening Slide Studio:", destUrl);

      // Check if an existing YT2PDF tab is open
      let existingTab = null;
      for (const t of allTabs) {
        if (t.url) {
          try {
            const u = new URL(t.url);
            if (u.hostname === "yt2pdfs.com" || u.hostname === "www.yt2pdfs.com" ||
                ((u.hostname === "localhost" || u.hostname === "127.0.0.1") && (u.port === "8000" || u.port === "8080"))) {
              existingTab = t;
              break;
            }
          } catch (e) {}
        }
      }

      if (existingTab && existingTab.id) {
        chrome.tabs.update(existingTab.id, { url: destUrl, active: true }, () => {
          sendResponse({ success: true, url: destUrl });
        });
      } else {
        chrome.tabs.create({ url: destUrl, active: true }, () => {
          sendResponse({ success: true, url: destUrl });
        });
      }
    })();
    return true;
  }

  // 6. Direct Client-Side PDF Download via Chrome Downloads API
  if (message.action === "download_pdf") {
    const filename = (message.filename || "Lecture_Slides.pdf").replace(/[/\\?%*:|"<>]/g, '_');
    const safeName = filename.endsWith(".pdf") ? filename : `${filename}.pdf`;
    if (chrome.downloads && chrome.downloads.download) {
      chrome.downloads.download({
        url: message.url,
        filename: safeName,
        saveAs: false
      }, (downloadId) => {
        if (chrome.runtime.lastError) {
          sendResponse({ success: false, error: chrome.runtime.lastError.message });
        } else {
          sendResponse({ success: true, downloadId: downloadId });
        }
      });
      return true;
    } else {
      sendResponse({ success: false, error: "Downloads API not available" });
      return false;
    }
  }
});
