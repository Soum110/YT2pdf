/**
 * web_bridge.js — YT2PDF Companion Web Bridge
 * Injected on yt2pdfs.com and *.run.app to allow seamless silent in-page
 * extraction without opening ANY new tabs or windows in the user's browser.
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

      // Broadcast companion availability
      window.dispatchEvent(new CustomEvent("YT2PDF_COMPANION_READY", {
        detail: { version: "1.0.0", active: true }
      }));
      window.postMessage({ type: "YT2PDF_COMPANION_READY", version: "1.0.0", active: true }, "*");
    } catch (e) {}
  }

  // Initial announcement
  signalActive();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", signalActive);
  }

  // Periodic announcement for late-initializing SPAs
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
  }, 400);

  let isExtracting = false;
  let lastExtractionTime = 0;
  let activeExtractorIframe = null;
  let extractorWatchdog = null;

  function cleanupExtraction() {
    if (extractorWatchdog) {
      clearTimeout(extractorWatchdog);
      extractorWatchdog = null;
    }
  }

  function dispatchResult(detail) {
    cleanupExtraction();
    isExtracting = false;
    window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_RESULT", { detail }));
    window.postMessage({ type: "YT2PDF_EXTRACTION_RESULT", detail }, "*");
  }

  function executeExtraction(videoUrl) {
    const now = Date.now();
    if (isExtracting || (now - lastExtractionTime < 3000)) {
      console.log("[YT2PDF Bridge] Extraction already in progress; ignoring duplicate trigger.");
      return;
    }

    if (!videoUrl) {
      dispatchResult({ success: false, error: "No video URL provided." });
      return;
    }

    if (!isExtensionContextValid()) {
      dispatchResult({ success: false, error: "Extension context was invalidated. Please reload the extension and refresh page." });
      return;
    }

    cleanupExtraction();
    isExtracting = true;
    lastExtractionTime = now;
    console.log("[YT2PDF Bridge] Requesting 100% silent background extraction for:", videoUrl);

    // Generous 180s (3-minute) timeout watchdog so large videos never time out prematurely
    extractorWatchdog = setTimeout(() => {
      console.warn("[YT2PDF Bridge] Extraction watchdog timed out after 180s.");
      dispatchResult({
        success: false,
        error: "Slide extraction timed out after 3 minutes. Please check that the YouTube video is publicly accessible and try again."
      });
    }, 180000);

    try {
      chrome.runtime.sendMessage({
        action: "start_background_extraction",
        video_url: videoUrl,
        origin_url: window.location.origin
      }, (response) => {
        if (chrome.runtime.lastError) {
          console.warn("[YT2PDF Bridge] Runtime message error:", chrome.runtime.lastError.message);
          dispatchResult({
            success: false,
            error: chrome.runtime.lastError.message || "Failed to communicate with companion extension."
          });
          return;
        }

        if (response) {
          if (response.success) {
            dispatchResult({
              success: true,
              job_id: response.job_id,
              backend_base: response.backend_base,
              slide_count: response.slide_count
            });
          } else {
            dispatchResult({
              success: false,
              error: response.error || "Background slide extraction failed."
            });
          }
        }
      });
    } catch (e) {
      dispatchResult({ success: false, error: "Extension messaging error: " + e.message });
    }
  }

  // Listen for extraction requests dispatched by webpage
  window.addEventListener("YT2PDF_START_EXTRACTION", (event) => {
    executeExtraction(event?.detail?.video_url);
  });

  // Listen for progress updates & completion messages from background service worker
  if (typeof chrome !== "undefined" && chrome?.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg.action === "extraction_progress_update") {
        window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_PROGRESS", {
          detail: { current: msg.current, total: msg.total }
        }));
        window.postMessage({ type: "YT2PDF_EXTRACTION_PROGRESS", current: msg.current, total: msg.total }, "*");
      } else if (msg.action === "extraction_finished") {
        if (msg.success && msg.data) {
          dispatchResult({
            success: true,
            job_id: msg.data.job_id,
            backend_base: msg.data.backend_base,
            slide_count: msg.data.slide_count
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

  // Also listen for window messages from webpage
  window.addEventListener("message", (event) => {
    // Ping/pong handshakes
    if (event.data?.type === "YT2PDF_PING") {
      signalActive();
      window.dispatchEvent(new CustomEvent("YT2PDF_PONG", { detail: { version: "1.0.0", active: true } }));
      window.postMessage({ type: "YT2PDF_PONG", version: "1.0.0", active: true }, "*");
    }
  });


  // Also listen for ping requests from webpage via CustomEvent
  window.addEventListener("YT2PDF_PING", () => {
    signalActive();
    window.dispatchEvent(new CustomEvent("YT2PDF_PONG", { detail: { version: "1.0.0", active: true } }));
    window.postMessage({ type: "YT2PDF_PONG", version: "1.0.0", active: true }, "*");
  });
})();
