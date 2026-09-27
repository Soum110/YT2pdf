/**
 * background.js — YT2PDF Companion Service Worker
 * 
 * Core Responsibilities:
 * 1. ExtractionTabManager: Spawns completely silent, muted background tabs (active: false, muted: true)
 *    to extract presentation slides without disrupting the user's active watch page or web session.
 * 2. Fail-Safe Auto-Termination: Guarantees that extraction tabs are immediately closed and removed
 *    upon completion, error, or watchdog timeout. Persistent storage ensures zero ghost tabs.
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
// Extraction Task Lifecycle Manager (Persisted in chrome.storage.local)
// ─────────────────────────────────────────────
const memoryWatchdogs = new Map(); // taskId -> timeout handle

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
  chrome.storage.local.get(null, (items) => {
    if (!items) return;
    for (const [key, value] of Object.entries(items)) {
      if (key.startsWith("task_") && value && value.tabId === tabId) {
        console.log(`[YT2PDF Background] Extraction tab ${tabId} closed externally.`);
        terminateExtraction(value.taskId, "Extraction tab was closed before completion.");
        break;
      }
    }
  });
});

/**
 * Terminate an extraction task, immediately remove the background tab, and notify origin.
 */
async function terminateExtraction(taskId, errorMessage = null, deck = null) {
  if (memoryWatchdogs.has(taskId)) {
    clearTimeout(memoryWatchdogs.get(taskId));
    memoryWatchdogs.delete(taskId);
  }

  const storageKey = `task_${taskId}`;
  const res = await new Promise((resolve) => chrome.storage.local.get(storageKey, resolve));
  const task = res ? res[storageKey] : null;

  // Immediately close and destroy the background tab
  if (task && task.tabId) {
    try {
      chrome.tabs.get(task.tabId, (t) => {
        if (!chrome.runtime.lastError && t) {
          chrome.tabs.remove(task.tabId).catch(() => {});
        }
      });
    } catch (e) {}
  }

  // Clean up persisted task data
  chrome.storage.local.remove(storageKey);

  // Notify originating YouTube tab or Web Bridge
  if (errorMessage) {
    console.warn(`[YT2PDF Background] Task ${taskId} terminated with error:`, errorMessage);
    if (task && task.originTabId) {
      chrome.tabs.sendMessage(task.originTabId, {
        action: "extraction_finished",
        taskId: taskId,
        success: false,
        error: errorMessage
      }).catch(() => {});
    }
    if (task && task.isWebOrigin) {
      broadcastToWebTabs({
        action: "extraction_finished",
        taskId: taskId,
        success: false,
        error: errorMessage
      });
    }
  } else if (deck) {
    console.log(`[YT2PDF Background] Task ${taskId} succeeded with ${deck.slideCount} slides.`);
    if (task && task.originTabId) {
      chrome.tabs.sendMessage(task.originTabId, {
        action: "extraction_finished",
        taskId: taskId,
        success: true,
        deck: deck
      }).catch(() => {});
    }
    if (task && task.isWebOrigin) {
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
function startSilentExtraction({ videoUrl, videoTitle, videoDuration, originTabId, isWebOrigin }, sendResponse) {
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
  let targetUrl = `https://www.youtube.com/watch?v=${videoId}&yt2pdf_headless=1&yt2pdf_task=${taskId}&yt2pdf_title=${titleParam}`;
  if (videoDuration && !isNaN(videoDuration) && videoDuration > 0) {
    targetUrl += `&yt2pdf_duration=${Math.floor(videoDuration)}`;
  }

  console.log(`[YT2PDF Background] Starting silent extraction task: ${taskId} for video ${videoId} (duration: ${videoDuration || 0}s)`);

  // Create silent, inactive background tab
  chrome.tabs.create({ url: targetUrl, active: false }, (tab) => {
    if (chrome.runtime.lastError || !tab) {
      const err = chrome.runtime.lastError?.message || "Failed to create silent extraction tab";
      if (sendResponse) sendResponse({ success: false, error: err });
      return;
    }

    // Immediately enforce mute at browser tab level
    chrome.tabs.update(tab.id, { muted: true }).catch(() => {});

    // Set watchdog timeout (180s hard timeout)
    const timeoutHandle = setTimeout(() => {
      console.warn(`[YT2PDF Background] Task ${taskId} timed out after 3 minutes.`);
      terminateExtraction(taskId, "Slide extraction timed out after 3 minutes.");
    }, 180000);
    memoryWatchdogs.set(taskId, timeoutHandle);

    // Persist task data in chrome.storage.local
    const taskData = {
      taskId,
      tabId: tab.id,
      originTabId: originTabId || null,
      isWebOrigin: Boolean(isWebOrigin),
      videoUrl,
      videoId,
      retryCount: 0,
      startTime: Date.now()
    };
    chrome.storage.local.set({ [`task_${taskId}`]: taskData });

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
      videoDuration: message.videoDuration,
      originTabId: originTabId,
      isWebOrigin: message.isWebOrigin
    }, sendResponse);
    return true; // Keep sendResponse open
  }

  // 2. Real-time progress update from headless extractor tab
  if (message.action === "extraction_progress") {
    const taskId = message.taskId;

    chrome.storage.local.get(`task_${taskId}`, (res) => {
      const task = res ? res[`task_${taskId}`] : null;
      if (task) {
        // Refresh watchdog timeout
        if (memoryWatchdogs.has(taskId)) {
          clearTimeout(memoryWatchdogs.get(taskId));
        }
        const timeoutHandle = setTimeout(() => {
          console.warn(`[YT2PDF Background] Task ${taskId} stalled with no progress for 2 minutes.`);
          terminateExtraction(taskId, "Slide extraction stalled with no progress for 2 minutes.");
        }, 120000);
        memoryWatchdogs.set(taskId, timeoutHandle);

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
    });

    sendResponse({ received: true });
    return true;
  }

  // 3. Extraction Error reported from headless tab
  if (message.action === "extraction_error") {
    const taskId = message.taskId;
    const errorMsg = message.error || "Slide extraction failed.";

    // Unconditionally remove the sender tab immediately
    if (sender?.tab?.id) {
      chrome.tabs.remove(sender.tab.id).catch(() => {});
    }

    chrome.storage.local.get(`task_${taskId}`, (res) => {
      const task = res ? res[`task_${taskId}`] : null;

      if (task && task.retryCount < 1) {
        // One retry attempt by launching fresh tab
        task.retryCount++;
        chrome.storage.local.set({ [`task_${taskId}`]: task });
        console.log(`[YT2PDF Background] Retrying extraction task ${taskId}...`);
        
        startSilentExtraction({
          videoUrl: task.videoUrl,
          videoTitle: "Presentation Slides",
          originTabId: task.originTabId,
          isWebOrigin: task.isWebOrigin
        });
        sendResponse({ retrying: true });
        return;
      }

      terminateExtraction(taskId, errorMsg);
      sendResponse({ terminated: true });
    });
    return true;
  }

  // 4. Save extracted presentation deck and terminate the silent tab immediately
  if (message.action === "deck_ready") {
    const deck = message.deck;
    const taskId = message.taskId;

    // Unconditionally remove the sender tab immediately
    if (sender?.tab?.id) {
      chrome.tabs.remove(sender.tab.id).catch(() => {});
    }

    if (deck && deck.deckId) {
      chrome.storage.local.set({
        [deck.deckId]: deck,
        latest_deck_id: deck.deckId,
        last_deck_id: deck.deckId
      }, () => {
        if (taskId) {
          terminateExtraction(taskId, null, deck);
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
