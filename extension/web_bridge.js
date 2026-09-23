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
    if (activeExtractorIframe) {
      try { activeExtractorIframe.remove(); } catch (e) {}
      activeExtractorIframe = null;
    }
    const existing = document.getElementById("yt2pdf-silent-extractor");
    if (existing) {
      try { existing.remove(); } catch (e) {}
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

    // Generous 180s (3-minute) timeout watchdog so large videos never time out prematurely
    extractorWatchdog = setTimeout(() => {
      console.warn("[YT2PDF Bridge] Extraction watchdog timed out after 180s.");
      dispatchResult({
        success: false,
        error: "Slide extraction timed out after 3 minutes. Please check that the YouTube video is publicly accessible and try again."
      });
    }, 180000);

    try {
      const embedUrl = `https://www.youtube.com/embed/${videoId}?autoplay=1&mute=1&enablejsapi=1&yt2pdf_headless=1`;
      const iframe = document.createElement("iframe");
      iframe.id = "yt2pdf-silent-extractor";
      iframe.src = embedUrl;
      iframe.allow = "autoplay";
      iframe.style.cssText = "position:fixed;top:-9999px;left:-9999px;width:1280px;height:720px;opacity:0.001;pointer-events:none;border:none;z-index:-99999;";

      iframe.onerror = (e) => {
        console.warn("[YT2PDF Bridge] Iframe error:", e);
        dispatchResult({ success: false, error: "Failed to initialize YouTube video stream." });
      };

      document.body.appendChild(iframe);
      activeExtractorIframe = iframe;
    } catch (e) {
      dispatchResult({ success: false, error: "Iframe initialization error: " + e.message });
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

  // Listen for window postMessages from the silent iframe or webpage
  window.addEventListener("message", (event) => {
    if (event.data?.type === "YT2PDF_HEADLESS_PROGRESS") {
      window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_PROGRESS", {
        detail: { current: event.data.current, total: event.data.total }
      }));
      window.postMessage({ type: "YT2PDF_EXTRACTION_PROGRESS", current: event.data.current, total: event.data.total }, "*");
    } else if (event.data?.type === "YT2PDF_HEADLESS_COMPLETE") {
      dispatchResult({
        success: true,
        job_id: event.data.job_id,
        backend_base: event.data.backend_base,
        slide_count: event.data.slide_count
      });
    } else if (event.data?.type === "YT2PDF_HEADLESS_ERROR") {
      dispatchResult({
        success: false,
        error: event.data.error || "Background slide extraction failed."
      });
    } else if (event.data?.type === "YT2PDF_PING") {
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
