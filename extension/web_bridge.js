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

  function cleanupExtractorIframe() {
    if (extractorWatchdog) {
      clearTimeout(extractorWatchdog);
      extractorWatchdog = null;
    }
    if (activeExtractorIframe) {
      try {
        activeExtractorIframe.remove();
      } catch (e) {}
      activeExtractorIframe = null;
    }
  }

  function executeExtraction(videoUrl) {
    const now = Date.now();
    if (isExtracting || (now - lastExtractionTime < 4000)) {
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

    cleanupExtractorIframe();

    let rawUrl = videoUrl;
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

    isExtracting = true;
    lastExtractionTime = now;
    console.log("[YT2PDF Bridge] Starting completely silent in-page hidden extraction for:", target.toString());

    // Create 100% INVISIBLE in-page extractor iframe.
    // Zero new tabs. Zero new windows. 100% hidden from the user.
    const iframe = document.createElement("iframe");
    iframe.id = "yt2pdf-headless-extractor";
    iframe.src = target.toString();
    iframe.style.cssText = "position:fixed; left:-9999px; top:-9999px; width:800px; height:600px; opacity:0.001; pointer-events:none; border:none; z-index:-999999;";
    activeExtractorIframe = iframe;

    extractorWatchdog = setTimeout(() => {
      console.warn("[YT2PDF Bridge] Extraction iframe timed out after 65s.");
      dispatchResult({
        success: false,
        error: "Slide extraction timed out. Please check that the YouTube video is publicly accessible and try again."
      });
    }, 65000);

    (document.body || document.documentElement).appendChild(iframe);
  }

  function dispatchResult(detail) {
    cleanupExtractorIframe();
    isExtracting = false;
    window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_RESULT", { detail }));
    window.postMessage({ type: "YT2PDF_EXTRACTION_RESULT", detail }, "*");
  }

  // Listen for extraction requests dispatched by webpage
  window.addEventListener("YT2PDF_START_EXTRACTION", (event) => {
    executeExtraction(event?.detail?.video_url);
  });

  // Listen for messages from the embedded extractor iframe and webpage
  window.addEventListener("message", (event) => {
    // 1. Progress updates from iframe
    if (event.data?.type === "YT2PDF_HEADLESS_PROGRESS") {
      window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_PROGRESS", {
        detail: { current: event.data.current, total: event.data.total }
      }));
      window.postMessage({ type: "YT2PDF_EXTRACTION_PROGRESS", current: event.data.current, total: event.data.total }, "*");
    }

    // 2. Extraction completion from iframe
    if (event.data?.type === "YT2PDF_HEADLESS_COMPLETE") {
      console.log("[YT2PDF Bridge] Silent in-page extraction succeeded! Job ID:", event.data.job_id);
      dispatchResult({
        success: true,
        job_id: event.data.job_id,
        backend_base: event.data.backend_base,
        slide_count: event.data.slide_count
      });
    }

    // 3. Extraction failure from iframe
    if (event.data?.type === "YT2PDF_HEADLESS_ERROR") {
      console.warn("[YT2PDF Bridge] In-page extraction error:", event.data.error);
      dispatchResult({
        success: false,
        error: event.data.error || "Slide extraction failed."
      });
    }

    // 4. Ping/pong handshakes
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
