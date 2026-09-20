/**
 * web_bridge.js — YT2PDF Companion Web Bridge
 * Injected on yt2pdfs.com and *.run.app to allow seamless silent background
 * extraction when the user pastes a YouTube URL into the web application.
 */

(function () {
  // Signal to the webpage that the extension is active and available
  document.documentElement.dataset.yt2pdfCompanion = "active";
  window.__YT2PDF_COMPANION_ACTIVE__ = true;

  // Dispatch custom event in case page DOM is already listening
  window.dispatchEvent(new CustomEvent("YT2PDF_COMPANION_READY", {
    detail: { version: "1.0.0", active: true }
  }));

  function isExtensionContextValid() {
    try {
      return Boolean(typeof chrome !== "undefined" && chrome?.runtime && chrome.runtime.id);
    } catch (e) {
      return false;
    }
  }

  // Re-broadcast periodically for late-initializing SPAs
  let announceCount = 0;
  const announcer = setInterval(() => {
    if (!isExtensionContextValid()) {
      clearInterval(announcer);
      delete document.documentElement.dataset.yt2pdfCompanion;
      window.__YT2PDF_COMPANION_ACTIVE__ = false;
      return;
    }
    document.documentElement.dataset.yt2pdfCompanion = "active";
    window.__YT2PDF_COMPANION_ACTIVE__ = true;
    window.dispatchEvent(new CustomEvent("YT2PDF_COMPANION_READY", {
      detail: { version: "1.0.0", active: true }
    }));
    announceCount++;
    if (announceCount > 10) clearInterval(announcer);
  }, 500);

  // Listen for extraction requests dispatched by the webpage
  window.addEventListener("YT2PDF_START_EXTRACTION", (event) => {
    const videoUrl = event?.detail?.video_url;
    if (!videoUrl) {
      window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_RESULT", {
        detail: { success: false, error: "No video URL provided." }
      }));
      return;
    }

    if (!isExtensionContextValid()) {
      window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_RESULT", {
        detail: { success: false, error: "Extension was reloaded or updated. Please refresh the page (F5)." }
      }));
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
          window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_RESULT", {
            detail: { success: false, error: lastErr.message }
          }));
        } else if (response && response.success) {
          console.log("[YT2PDF Bridge] Silent background extraction succeeded! Job ID:", response.job_id);
          window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_RESULT", {
            detail: {
              success: true,
              job_id: response.job_id,
              backend_base: response.backend_base,
              slide_count: response.slide_count
            }
          }));
        } else {
          console.warn("[YT2PDF Bridge] Extraction failed:", response?.error);
          window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_RESULT", {
            detail: { success: false, error: response?.error || "Silent background extraction failed." }
          }));
        }
      });
    } catch (err) {
      console.warn("[YT2PDF Bridge] Error sending message:", err.message);
      window.dispatchEvent(new CustomEvent("YT2PDF_EXTRACTION_RESULT", {
        detail: { success: false, error: err.message }
      }));
    }
  });

  // Also listen for ping requests from webpage
  window.addEventListener("YT2PDF_PING", () => {
    document.documentElement.dataset.yt2pdfCompanion = "active";
    window.__YT2PDF_COMPANION_ACTIVE__ = true;
    window.dispatchEvent(new CustomEvent("YT2PDF_PONG", {
      detail: { version: "1.0.0", active: true }
    }));
  });
})();
