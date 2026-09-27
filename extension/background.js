/**
 * background.js — YT2PDF Companion Service Worker
 * 
 * Core Responsibilities:
 * 1. ExtractionTabManager: Spawns completely silent, muted background tabs (active: false, muted: true)
 *    to extract presentation slides without disrupting the user's active watch page or web session.
 * 2. Fail-Safe Auto-Termination: Guarantees that extraction tabs are immediately closed and removed
 *    upon completion, error, or watchdog timeout. Zero ghost tabs left behind.
 * 3. Bidirectional Progress Relaying: Relays live slide capture progress to the originating tab or website.
 * 4. Deck Storage & Slide Studio Routing: Stores decks in chrome.storage.local and routes to yt2pdfs.com.
 */

// ─────────────────────────────────────────────
// Target Website Base Resolution (Strict Security)
// ─────────────────────────────────────────────
function resolveTargetBase(tabs) {
  const DEFAULT_BASE = "https://yt2pdfs.com";
  if (!tabs || !Array.isArray(tabs)) return DEFAULT_BASE;

  for (const t of tabs) {
    if (!t.url) continue;
    try {
      const u = new URL(t.url);
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
// Extraction Task Lifecycle Manager
// ─────────────────────────────────────────────
const activeExtractions = new Map(); // taskId -> task metadata

function purgeStaleExtractionTabs() {
  try {
    if (chrome.tabs && chrome.tabs.query) {
      chrome.tabs.query({}, (tabs) => {
        if (chrome.runtime.lastError || !tabs) return;
        for (const tab of tabs) {
          if (tab.url && (tab.url.includes("yt2pdf_headless=1") || tab.url.includes("yt2pdf_task="))) {
            console.log(`[YT2PDF Background] Purging stale extraction tab: ${tab.id}`);
            chrome.tabs.remove(tab.id).catch(() => {});
          }
        }
      });
    }
  } catch (e) {}
}

// Purge any abandoned tabs on browser startup / extension install
purgeStaleExtractionTabs();
if (chrome.runtime?.onStartup) chrome.runtime.onStartup.addListener(purgeStaleExtractionTabs);
if (chrome.runtime?.onInstalled) chrome.runtime.onInstalled.addListener(purgeStaleExtractionTabs);

// Clean up task if the background tab is closed prematurely
chrome.tabs.onRemoved.addListener((tabId) => {
  for (const [taskId, task] of activeExtractions.entries()) {
    if (task.tabId === tabId) {
      console.log(`[YT2PDF Background] Extraction tab ${tabId} closed externally.`);
      terminateExtraction(taskId, "Extraction tab was closed before completion.");
      break;
    }
  }
});

/**
 * Terminate an extraction task, immediately remove the background tab, and notify origin.
 */
function terminateExtraction(taskId, errorMessage = null, deck = null) {
  const task = activeExtractions.get(taskId);
  if (!task) return;

  if (task.timeoutTimer) {
    clearTimeout(task.timeoutTimer);
    task.timeoutTimer = null;
  }

  // Immediately close and destroy the background tab
  if (task.tabId) {
    try {
      chrome.tabs.get(task.tabId, (t) => {
        if (!chrome.runtime.lastError && t) {
          chrome.tabs.remove(task.tabId).catch(() => {});
        }
      });
    } catch (e) {}
  }

  activeExtractions.delete(taskId);

  // Notify originating YouTube tab or Web Bridge
  if (errorMessage) {
    console.warn(`[YT2PDF Background] Task ${taskId} finished with error:`, errorMessage);
    if (task.originTabId) {
      chrome.tabs.sendMessage(task.originTabId, {
        action: "extraction_finished",
        taskId: taskId,
        success: false,
        error: errorMessage
      }).catch(() => {});
    }
    // Also broadcast to web tabs if originated from web
    if (task.isWebOrigin) {
      broadcastToWebTabs({
        action: "extraction_finished",
        taskId: taskId,
        success: false,
        error: errorMessage
      });
    }
  } else if (deck) {
    console.log(`[YT2PDF Background] Task ${taskId} succeeded with ${deck.slideCount} slides.`);
    if (task.originTabId) {
      chrome.tabs.sendMessage(task.originTabId, {
        action: "extraction_finished",
        taskId: taskId,
        success: true,
        deck: deck
      }).catch(() => {});
    }
    if (task.isWebOrigin) {
      broadcastToWebTabs({
        action: "extraction_finished",
        taskId: taskId,
        success: true,
        deck: deck
      });
    }
  }
}

/**
 * Broadcast an event to all open YT2PDF web tabs.
 */
function broadcastToWebTabs(message) {
  try {
    chrome.tabs.query({}, (tabs) => {
      if (chrome.runtime.lastError || !tabs) return;
      for (const tab of tabs) {
        if (!tab.url) continue;
        try {
          const u = new URL(tab.url);
          if (u.hostname === "yt2pdfs.com" || u.hostname === "www.yt2pdfs.com" ||
              ((u.hostname === "localhost" || u.hostname === "127.0.0.1") && (u.port === "8000" || u.port === "8080"))) {
            chrome.tabs.sendMessage(tab.id, message).catch(() => {});
          }
        } catch (e) {}
      }
    });
  } catch (e) {}
}

/**
 * Start a silent background extraction task.
 */
function startSilentExtraction({ videoUrl, videoTitle, originTabId, isWebOrigin }, sendResponse) {
  let videoId = "";
  try {
    const u = new URL(videoUrl);
    if (u.hostname.includes("youtu.be")) {
      videoId = u.pathname.replace(/^\//, "").split("?")[0];
    } else if (u.pathname.includes("/shorts/")) {
      videoId = u.pathname.split("/shorts/")[1]?.split("/")[0];
    } else if (u.pathname.includes("/embed/")) {
      videoId = u.pathname.split("/embed/")[1]?.split("?")[0];
    } else if (u.searchParams.has("v")) {
      videoId = u.searchParams.get("v");
    }
  } catch (e) {}

  if (!videoId) {
    if (sendResponse) sendResponse({ success: false, error: "Invalid YouTube video URL." });
    return;
  }

  const taskId = `task_${Date.now()}_${Math.random().toString(36).substr(2, 6)}`;
  const titleParam = encodeURIComponent(videoTitle || "Presentation Slides");
  const targetUrl = `https://www.youtube.com/watch?v=${videoId}&yt2pdf_headless=1&yt2pdf_task=${taskId}&yt2pdf_title=${titleParam}`;

  console.log(`[YT2PDF Background] Starting silent extraction task: ${taskId} for video ${videoId}`);

  // Create silent, inactive background tab
  chrome.tabs.create({ url: targetUrl, active: false }, (tab) => {
    if (chrome.runtime.lastError || !tab) {
      const err = chrome.runtime.lastError?.message || "Failed to create silent extraction tab";
      if (sendResponse) sendResponse({ success: false, error: err });
      return;
    }

    // Immediately enforce mute at browser tab level
    chrome.tabs.update(tab.id, { muted: true }).catch(() => {});

    // Set initial 3-minute watchdog timeout (refreshed on progress updates)
    const timeoutTimer = setTimeout(() => {
      console.warn(`[YT2PDF Background] Task ${taskId} timed out after 3 minutes.`);
      terminateExtraction(taskId, "Slide extraction timed out after 3 minutes of inactivity.");
    }, 180000);

    activeExtractions.set(taskId, {
      taskId,
      tabId: tab.id,
      originTabId: originTabId || null,
      isWebOrigin: Boolean(isWebOrigin),
      videoUrl,
      videoId,
      timeoutTimer,
      retryCount: 0,
      startTime: Date.now()
    });

    if (sendResponse) {
      sendResponse({ success: true, taskId: taskId });
    }
  });
}

// ─────────────────────────────────────────────
// Message Router
// ─────────────────────────────────────────────
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {

  // 1. Request to launch silent background extraction
  if (message.action === "start_silent_extraction") {
    const originTabId = sender?.tab?.id || message.originTabId;
    startSilentExtraction({
      videoUrl: message.videoUrl,
      videoTitle: message.videoTitle,
      originTabId: originTabId,
      isWebOrigin: message.isWebOrigin
    }, sendResponse);
    return true; // Keep sendResponse open
  }

  // 2. Real-time progress update from headless extractor tab
  if (message.action === "extraction_progress") {
    const taskId = message.taskId;
    const task = activeExtractions.get(taskId);

    if (task) {
      // Refresh watchdog timeout on each progress step
      if (task.timeoutTimer) clearTimeout(task.timeoutTimer);
      task.timeoutTimer = setTimeout(() => {
        console.warn(`[YT2PDF Background] Task ${taskId} stalled with no progress for 2 minutes.`);
        terminateExtraction(taskId, "Slide extraction stalled with no progress for 2 minutes.");
      }, 120000);

      // Relay progress to originating YouTube tab
      if (task.originTabId) {
        chrome.tabs.sendMessage(task.originTabId, {
          action: "extraction_progress_update",
          taskId: taskId,
          current: message.current,
          total: message.total,
          statusMsg: message.statusMsg || ""
        }).catch(() => {});
      }

      // Relay progress to web origin tabs
      if (task.isWebOrigin) {
        broadcastToWebTabs({
          action: "extraction_progress_update",
          taskId: taskId,
          current: message.current,
          total: message.total,
          statusMsg: message.statusMsg || ""
        });
      }
    }
    sendResponse({ received: true });
    return true;
  }

  // 3. Extraction Error reported from headless tab
  if (message.action === "extraction_error") {
    const taskId = message.taskId;
    const task = activeExtractions.get(taskId);
    const errorMsg = message.error || "Slide extraction failed.";

    if (task && task.retryCount < 1) {
      // Retry once by reloading the tab if it wasn't a permanent error
      task.retryCount++;
      console.log(`[YT2PDF Background] Retrying extraction task ${taskId} (attempt ${task.retryCount + 1})...`);
      try {
        chrome.tabs.reload(task.tabId, { bypassCache: true });
        sendResponse({ retrying: true });
        return true;
      } catch (e) {}
    }

    terminateExtraction(taskId, errorMsg);
    sendResponse({ terminated: true });
    return true;
  }

  // 4. Save extracted presentation deck and terminate the silent tab immediately
  if (message.action === "deck_ready") {
    const deck = message.deck;
    const taskId = message.taskId;

    if (deck && deck.deckId) {
      chrome.storage.local.set({
        [deck.deckId]: deck,
        latest_deck_id: deck.deckId,
        last_deck_id: deck.deckId
      }, () => {
        // Immediately terminate and destroy the silent background tab
        if (taskId) {
          terminateExtraction(taskId, null, deck);
        } else {
          // If taskId wasn't provided, close sender tab if it was headless
          if (sender?.tab?.id && sender.tab.url && sender.tab.url.includes("yt2pdf_headless=1")) {
            chrome.tabs.remove(sender.tab.id).catch(() => {});
          }
        }
        sendResponse({ success: true, deckId: deck.deckId });
      });
      return true;
    }

    sendResponse({ success: false, error: "No deck provided" });
    return false;
  }

  // 5. Open YT2PDF Slide Studio web page for a specific deck
  if (message.action === "open_deck_page") {
    const deckId = message.deckId;
    (async () => {
      let allTabs = [];
      try { allTabs = await chrome.tabs.query({}); } catch (e) {}
      const targetBase = resolveTargetBase(allTabs);
      const destUrl = `${targetBase}/?deck_id=${deckId}`;

      console.log("[YT2PDF Background] Directing to Slide Studio:", destUrl);

      // Check if a YT2PDF tab is already open
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

  // 6. Direct Client-Side PDF Download via Chrome Downloads API (fallback)
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
