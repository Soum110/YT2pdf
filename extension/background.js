/**
 * background.js — YT2PDF Companion Service Worker
 * 
 * Core Responsibilities:
 * 1. DeclarativeNetRequest Rules: Strips x-frame-options and content-security-policy
 *    for sub_frames to allow seamless, zero-tab in-page slide extraction.
 * 2. Stale Tab Purger: Purges any abandoned background tabs from earlier sessions.
 * 3. Deck Storage & Slide Studio Routing: Stores decks in chrome.storage.local and routes to yt2pdfs.com.
 */

const DNR_RULE_ID = 2001;

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
// Purge any stale headless tabs from earlier attempts
// ─────────────────────────────────────────────
function purgeStaleExtractionTabs() {
  try {
    if (chrome.tabs && chrome.tabs.query) {
      chrome.tabs.query({}, (tabs) => {
        if (chrome.runtime.lastError || !tabs) return;
        for (const tab of tabs) {
          if (tab.url && (tab.url.includes("yt2pdf_headless=1") || tab.url.includes("yt2pdf_task="))) {
            console.log(`[YT2PDF Background] Purging abandoned extraction tab: ${tab.id}`);
            chrome.tabs.remove(tab.id).catch(() => {});
          }
        }
      });
    }
  } catch (e) {}
}

purgeStaleExtractionTabs();
if (chrome.runtime?.onStartup) chrome.runtime.onStartup.addListener(purgeStaleExtractionTabs);
if (chrome.runtime?.onInstalled) chrome.runtime.onInstalled.addListener(purgeStaleExtractionTabs);

// ─────────────────────────────────────────────
// Dynamic DeclarativeNetRequest Rules (for sub_frame embedding)
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
// Message Router
// ─────────────────────────────────────────────
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {

  // 1. Save extracted presentation deck into chrome.storage.local
  if (message.action === "deck_ready") {
    const deck = message.deck;

    if (deck && deck.deckId) {
      chrome.storage.local.set({
        [deck.deckId]: deck,
        latest_deck_id: deck.deckId,
        last_deck_id: deck.deckId
      }, () => {
        sendResponse({ success: true, deckId: deck.deckId });
      });
      return true;
    }

    sendResponse({ success: false, error: "No deck provided" });
    return false;
  }

  // 2. Open YT2PDF Slide Studio web page for a specific deck in a clean new tab
  if (message.action === "open_deck_page") {
    const deckId = message.deckId;
    (async () => {
      let allTabs = [];
      try { allTabs = await chrome.tabs.query({}); } catch (e) {}
      const targetBase = resolveTargetBase(allTabs);
      const destUrl = `${targetBase}/?deck_id=${deckId}`;

      console.log("[YT2PDF Background] Opening Slide Studio in tab:", destUrl);

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

  // 3. Direct Client-Side PDF Download via Chrome Downloads API (fallback)
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
