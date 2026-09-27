/**
 * web_bridge.js — YT2PDF Companion Web Bridge
 * Injected on yt2pdfs.com and localhost to allow seamless communication
 * between the web page and the YT2PDF Companion extension.
 * 
 * Architecture:
 * - Signals extension presence to index.html (sets data-yt2pdf-companion="active").
 * - Delegates extraction requests to background.js (which spawns a muted background tab).
 * - Relays real-time progress and completed presentation decks back to index.html.
 * - Does NOT embed iframes in the present tab.
 */

(function () {
  function isExtensionContextValid() {
    try {
      return Boolean(typeof chrome !== "undefined" && chrome?.runtime && chrome.runtime.id);
    } catch (e) {
      return false;
    }
  }

  function signalActive() {
    if (!isExtensionContextValid()) return;
    try {
      if (document.documentElement) {
        document.documentElement.dataset.yt2pdfCompanion = "active";
        document.documentElement.setAttribute("data-yt2pdf-companion", "active");
      }
      window.__YT2PDF_COMPANION_ACTIVE__ = true;

      // Broadcast companion availability to page scripts
      window.dispatchEvent(new CustomEvent("YT2PDF_COMPANION_READY", {
        detail: { version: "1.2.0", active: true }
      }));
      window.postMessage({ type: "YT2PDF_COMPANION_READY", version: "1.2.0", active: true }, "*");
    } catch (e) {}
  }

  // Initial announcement
  signalActive();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", signalActive);
  }

  // Periodic announcement for late-initializing DOM
  let announceCount = 0;
  const announcer = setInterval(() => {
    if (!isExtensionContextValid()) {
      clearInterval(announcer);
      if (document.documentElement) {
        delete document.documentElement.dataset.yt2pdfCompanion;
        document.documentElement.removeAttribute("data-yt2pdf-companion");
      }
      window.__YT2PDF_COMPANION_ACTIVE__ = false;
      return;
    }
    signalActive();
    announceCount++;
    if (announceCount > 15) clearInterval(announcer);
  }, 350);

  let isExtracting = false;
  let currentTaskId = null;

  function dispatchResult(detail) {
    isExtracting = false;
    currentTaskId = null;
    window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_RESULT", { detail }));
    window.postMessage({ type: "YT2PDF_EXTRACTION_RESULT", detail }, "*");
  }

  function executeExtraction(videoUrl) {
    if (isExtracting) {
      console.log("[YT2PDF Bridge] Extraction already in progress; ignoring duplicate trigger.");
      return;
    }

    if (!videoUrl) {
      dispatchResult({ success: false, error: "No video URL provided." });
      return;
    }

    if (!isExtensionContextValid()) {
      dispatchResult({ success: false, error: "Extension context was invalidated. Please reload the extension and refresh the page." });
      return;
    }

    isExtracting = true;
    console.log("[YT2PDF Bridge] Delegating extraction to background service worker for:", videoUrl);

    try {
      chrome.runtime.sendMessage({
        action: "start_silent_extraction",
        videoUrl: videoUrl,
        videoTitle: "Presentation Slides",
        isWebOrigin: true
      }, (res) => {
        if (chrome.runtime.lastError) {
          dispatchResult({ success: false, error: chrome.runtime.lastError.message });
          return;
        }
        if (res && res.success) {
          currentTaskId = res.taskId;
        } else {
          dispatchResult({ success: false, error: res?.error || "Failed to start background extraction." });
        }
      });
    } catch (e) {
      dispatchResult({ success: false, error: e.message });
    }
  }

  // Listen for extraction requests dispatched by index.html
  window.addEventListener("YT2PDF_START_EXTRACTION", (event) => {
    executeExtraction(event?.detail?.video_url);
  });
  window.addEventListener("message", (event) => {
    if (event.data?.type === "YT2PDF_START_EXTRACTION") {
      executeExtraction(event.data.video_url);
    }
  });

  // Listen for progress updates & completion messages from background service worker
  if (isExtensionContextValid() && chrome?.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg.action === "extraction_progress_update") {
        window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_PROGRESS", {
          detail: {
            current: msg.current,
            total: msg.total,
            statusMsg: msg.statusMsg || ""
          }
        }));
        window.postMessage({
          type: "YT2PDF_EXTRACTION_PROGRESS",
          current: msg.current,
          total: msg.total,
          statusMsg: msg.statusMsg || ""
        }, "*");
      } else if (msg.action === "extraction_finished") {
        if (msg.success && msg.deck) {
          dispatchResult({
            success: true,
            job_id: msg.deck.deckId,
            slide_count: msg.deck.slideCount,
            deck: msg.deck
          });
        } else {
          dispatchResult({
            success: false,
            error: msg.error || "Background slide extraction failed."
          });
        }
      }
    });
  }

  // ─────────────────────────────────────────────
  // Local Deck Retrieval from Extension Storage
  // ─────────────────────────────────────────────
  function loadDeckFromStorage(deckId) {
    if (!isExtensionContextValid() || !chrome?.storage?.local) return;
    try {
      const keys = deckId ? [deckId, "latest_deck_id"] : ["latest_deck_id"];
      chrome.storage.local.get(keys, (res) => {
        const targetId = deckId || res?.latest_deck_id;
        const deck = res ? res[targetId] : null;
        if (deck) {
          console.log(`[YT2PDF Bridge] Retrieved presentation deck ${targetId} (${deck.slideCount || 0} slides)`);
          window.dispatchEvent(new CustomEvent("YT2PDF_CLIENT_DECK_LOADED", { detail: { deck } }));
          window.postMessage({ type: "YT2PDF_CLIENT_DECK_LOADED", deck }, "*");
        }
      });
    } catch (e) {
      console.warn("[YT2PDF Bridge] Deck retrieval notice:", e);
    }
  }

  // Listen for deck load requests from webpage
  window.addEventListener("YT2PDF_LOAD_DECK", (evt) => {
    loadDeckFromStorage(evt?.detail?.deck_id);
  });
  window.addEventListener("message", (evt) => {
    if (evt.data?.type === "YT2PDF_LOAD_DECK") {
      loadDeckFromStorage(evt.data?.deck_id);
    }
  });

  // Check URL on initialization for ?deck_id=...
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.has("deck_id")) {
      const dId = params.get("deck_id");
      setTimeout(() => loadDeckFromStorage(dId), 150);
      setTimeout(() => loadDeckFromStorage(dId), 500);
      setTimeout(() => loadDeckFromStorage(dId), 1200);
    }
  } catch (e) {}

  // Respond to ping requests from webpage
  window.addEventListener("YT2PDF_PING", () => {
    signalActive();
    window.dispatchEvent(new CustomEvent("YT2PDF_PONG", { detail: { version: "1.2.0", active: true } }));
    window.postMessage({ type: "YT2PDF_PONG", version: "1.2.0", active: true }, "*");
  });
})();
