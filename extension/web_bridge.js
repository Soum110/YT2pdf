/**
 * web_bridge.js — YT2PDF Companion Web Bridge
 * Injected on yt2pdfs.com and *.run.app to allow seamless silent background
 * extraction when the user pastes a YouTube URL into the web application.
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

      // Dual-channel broadcast (CustomEvent + postMessage)
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

  // Re-broadcast periodically for late-initializing SPAs
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

  function executeExtraction(videoUrl) {
    if (!videoUrl) {
      dispatchResult({ success: false, error: "No video URL provided." });
      return;
    }

    if (!isExtensionContextValid()) {
      dispatchResult({ success: false, error: "Extension context was invalidated. Please reload the extension and refresh page." });
      return;
    }

    console.log("[YT2PDF Bridge] Forwarding silent extraction request to background worker for:", videoUrl);

    try {
      chrome.runtime.sendMessage({
        action: "start_background_extraction",
        video_url: videoUrl
      }, (response) => {
        const lastErr = chrome?.runtime?.lastError;
        if (lastErr) {
          console.warn("[YT2PDF Bridge] Runtime error:", lastErr.message);
          dispatchResult({ success: false, error: lastErr.message });
        } else if (response && response.success) {
          console.log("[YT2PDF Bridge] Silent background extraction succeeded! Job ID:", response.job_id);
          dispatchResult({
            success: true,
            job_id: response.job_id,
            backend_base: response.backend_base,
            slide_count: response.slide_count
          });
        } else {
          console.warn("[YT2PDF Bridge] Extraction failed:", response?.error);
          dispatchResult({ success: false, error: response?.error || "Silent background extraction failed." });
        }
      });
    } catch (err) {
      console.warn("[YT2PDF Bridge] Error sending message:", err.message);
      dispatchResult({ success: false, error: err.message });
    }
  }

  function dispatchResult(detail) {
    window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_RESULT", { detail }));
    window.postMessage({ type: "YT2PDF_EXTRACTION_RESULT", detail }, "*");
  }

  // Listen for extraction requests dispatched by webpage (CustomEvent & postMessage)
  window.addEventListener("YT2PDF_START_EXTRACTION", (event) => {
    executeExtraction(event?.detail?.video_url);
  });

  window.addEventListener("message", (event) => {
    if (event.data?.type === "YT2PDF_START_EXTRACTION" && event.data?.video_url) {
      executeExtraction(event.data.video_url);
    }
    if (event.data?.type === "YT2PDF_PING") {
      signalActive();
      window.dispatchEvent(new CustomEvent("YT2PDF_PONG", { detail: { version: "1.0.0", active: true } }));
      window.postMessage({ type: "YT2PDF_PONG", version: "1.0.0", active: true }, "*");
    }
  });

  // Forward background extraction progress to webpage
  try {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg && msg.action === "extraction_progress_update") {
        window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_PROGRESS", {
          detail: { current: msg.current, total: msg.total }
        }));
        window.postMessage({ type: "YT2PDF_EXTRACTION_PROGRESS", current: msg.current, total: msg.total }, "*");
      }
    });
  } catch (e) {}

  // Also listen for ping requests from webpage via CustomEvent
  window.addEventListener("YT2PDF_PING", () => {
    signalActive();
    window.dispatchEvent(new CustomEvent("YT2PDF_PONG", { detail: { version: "1.0.0", active: true } }));
    window.postMessage({ type: "YT2PDF_PONG", version: "1.0.0", active: true }, "*");
  });
})();
