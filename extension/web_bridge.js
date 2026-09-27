/**
 * web_bridge.js — YT2PDF Companion Web Bridge
 * Injected on yt2pdfs.com and localhost.
 * 
 * Architecture:
 * - 100% Zero-Tab Extraction: Runs slide extraction inside a hidden, offscreen iframe.
 *   NO new tabs or windows are ever opened in the user's browser.
 * - Relays real-time progress to index.html.
 * - Automatically destroys the iframe upon completion or error.
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

      window.dispatchEvent(new CustomEvent("YT2PDF_COMPANION_READY", {
        detail: { version: "1.3.0", active: true }
      }));
      window.postMessage({ type: "YT2PDF_COMPANION_READY", version: "1.3.0", active: true }, "*");
    } catch (e) {}
  }

  signalActive();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", signalActive);
  }

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
  let activeExtractorIframe = null;
  let extractorWatchdog = null;

  function cleanupExtraction() {
    if (extractorWatchdog) {
      clearTimeout(extractorWatchdog);
      extractorWatchdog = null;
    }
    if (activeExtractorIframe) {
      try {
        activeExtractorIframe.src = "about:blank";
        activeExtractorIframe.remove();
      } catch (e) {}
      activeExtractorIframe = null;
    }
    const existing = document.getElementById("yt2pdf-silent-extractor");
    if (existing) {
      try {
        existing.src = "about:blank";
        existing.remove();
      } catch (e) {}
    }
  }

  cleanupExtraction();

  function dispatchResult(detail) {
    cleanupExtraction();
    isExtracting = false;
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

    cleanupExtraction();
    isExtracting = true;
    console.log("[YT2PDF Bridge] Starting 100% silent in-page iframe extraction for:", videoUrl);

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
      dispatchResult({ success: false, error: "Invalid YouTube video URL." });
      return;
    }

    // Safety watchdog timeout (90s)
    extractorWatchdog = setTimeout(() => {
      console.warn("[YT2PDF Bridge] Extraction watchdog timed out.");
      dispatchResult({
        success: false,
        error: "Slide extraction timed out. Please check your internet connection and try again."
      });
    }, 90000);

    let hasTriedFallback = false;

    try {
      const frame = document.createElement("iframe");
      frame.id = "yt2pdf-silent-extractor";
      frame.src = `https://www.youtube.com/embed/${videoId}?autoplay=1&mute=1&enablejsapi=1&yt2pdf_headless=1`;
      frame.style.cssText = "position:fixed;top:-10000px;left:-10000px;width:640px;height:360px;border:none;pointer-events:none;opacity:0;z-index:-9999;";
      frame.allow = "autoplay *; encrypted-media *;";
      activeExtractorIframe = frame;
      document.body.appendChild(frame);
    } catch (e) {
      dispatchResult({ success: false, error: "Failed to initialize extraction frame: " + e.message });
    }

    // Listen for progress & completion messages from silent iframe
    const onFrameMessage = (event) => {
      if (event.data?.type === "YT2PDF_HEADLESS_PROGRESS") {
        window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_PROGRESS", {
          detail: { current: event.data.current, total: event.data.total }
        }));
        window.postMessage({
          type: "YT2PDF_EXTRACTION_PROGRESS",
          current: event.data.current,
          total: event.data.total
        }, "*");
      } else if (event.data?.type === "YT2PDF_HEADLESS_COMPLETE") {
        window.removeEventListener("message", onFrameMessage);
        dispatchResult({
          success: true,
          job_id: event.data.deck?.deckId,
          slide_count: event.data.slide_count,
          deck: event.data.deck
        });
      } else if (event.data?.type === "YT2PDF_HEADLESS_ERROR") {
        if (!hasTriedFallback && activeExtractorIframe) {
          hasTriedFallback = true;
          console.warn("[YT2PDF Bridge] Embed restricted, falling back to native watch stream in silent frame...");
          activeExtractorIframe.src = `https://www.youtube.com/watch?v=${videoId}&yt2pdf_headless=1`;
          return;
        }
        window.removeEventListener("message", onFrameMessage);
        cleanupExtraction();
        dispatchResult({
          success: false,
          error: event.data.error || "Slide extraction failed."
        });
      }
    };

    window.addEventListener("message", onFrameMessage);
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

  // ─────────────────────────────────────────────
  // Local Deck Retrieval from Extension Storage
  // ─────────────────────────────────────────────
  function loadDeckFromStorage(deckId) {
    if (!isExtensionContextValid() || !chrome?.storage?.local) return;
    try {
      chrome.storage.local.get(null, (res) => {
        if (!res) return;
        let deck = null;
        let targetId = deckId;

        if (targetId && res[targetId] && res[targetId].slides && res[targetId].slides.length > 0) {
          deck = res[targetId];
        } else if (res.latest_deck_id && res[res.latest_deck_id] && res[res.latest_deck_id].slides) {
          targetId = res.latest_deck_id;
          deck = res[res.latest_deck_id];
        } else if (res.last_deck_id && res[res.last_deck_id] && res[res.last_deck_id].slides) {
          targetId = res.last_deck_id;
          deck = res[res.last_deck_id];
        } else {
          // Find any key starting with deck_ with slides
          const deckKeys = Object.keys(res).filter((k) => k.startsWith("deck_") && res[k]?.slides?.length > 0);
          if (deckKeys.length > 0) {
            deckKeys.sort((a, b) => (res[b].createdAt || 0) - (res[a].createdAt || 0));
            targetId = deckKeys[0];
            deck = res[targetId];
          }
        }

        if (deck) {
          console.log(`[YT2PDF Bridge] Retrieved presentation deck ${targetId} (${deck.slideCount || deck.slides.length || 0} slides)`);
          window.dispatchEvent(new CustomEvent("YT2PDF_CLIENT_DECK_LOADED", { detail: { deck } }));
          window.postMessage({ type: "YT2PDF_CLIENT_DECK_LOADED", deck }, "*");
        }
      });
    } catch (e) {
      console.warn("[YT2PDF Bridge] Deck retrieval notice:", e);
    }
  }

  window.addEventListener("YT2PDF_LOAD_DECK", (evt) => {
    loadDeckFromStorage(evt?.detail?.deck_id);
  });
  window.addEventListener("message", (evt) => {
    if (evt.data?.type === "YT2PDF_LOAD_DECK") {
      loadDeckFromStorage(evt.data?.deck_id);
    }
  });

  try {
    const params = new URLSearchParams(window.location.search);
    if (params.has("deck_id")) {
      const dId = params.get("deck_id");
      [50, 200, 500, 1000, 2000, 3500].forEach((ms) => {
        setTimeout(() => loadDeckFromStorage(dId), ms);
      });
    }
  } catch (e) {}

  window.addEventListener("YT2PDF_PING", () => {
    signalActive();
    window.dispatchEvent(new CustomEvent("YT2PDF_PONG", { detail: { version: "1.3.0", active: true } }));
    window.postMessage({ type: "YT2PDF_PONG", version: "1.3.0", active: true }, "*");
  });
})();
