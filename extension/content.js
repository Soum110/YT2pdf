/**
 * content.js — YT2PDF Slide Companion
 * Injects a native-styled "PDF Slides" button directly into YouTube's action bar
 * and captures clean presentation slides in the background.
 */

(function () {
  let isExtracting = false;

  // ─────────────────────────────────────────────
  // Toast notification
  // ─────────────────────────────────────────────
  function showToast(message, isError = false) {
    const existing = document.getElementById("yt2pdf-toast");
    if (existing) existing.remove();

    const toast = document.createElement("div");
    toast.id = "yt2pdf-toast";
    toast.style.cssText = `
      position: fixed;
      bottom: 28px;
      right: 28px;
      z-index: 9999999;
      background: ${isError ? "#BA1A1A" : "#1A1A1A"};
      color: #FFFFFF;
      border: 1px solid ${isError ? "#FF5449" : "rgba(255, 255, 255, 0.18)"};
      border-radius: 12px;
      padding: 13px 18px;
      font-family: Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 13.5px;
      font-weight: 500;
      box-shadow: 0 12px 32px rgba(0,0,0,0.5);
      display: flex;
      align-items: center;
      gap: 10px;
      animation: yt2pdf-fade-in 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      max-width: 440px;
      line-height: 1.4;
    `;

    toast.innerHTML = `
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="${isError ? '#FFDAD6' : '#FF4D4D'}" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
        <polyline points="14 2 14 8 20 8"></polyline>
        <line x1="16" y1="13" x2="8" y2="13"></line>
        <line x1="16" y1="17" x2="8" y2="17"></line>
        <polyline points="10 9 9 9 8 9"></polyline>
      </svg>
      <span>${message}</span>
    `;

    document.body.appendChild(toast);

    setTimeout(() => {
      if (toast && toast.parentNode) {
        toast.style.transition = "opacity 0.35s ease, transform 0.35s ease";
        toast.style.opacity = "0";
        toast.style.transform = "translateY(12px)";
        setTimeout(() => toast.remove(), 380);
      }
    }, 5500);
  }

  // ─────────────────────────────────────────────
  // Perceptual frame difference (luminance diff)
  // ─────────────────────────────────────────────
  function calculateDifference(canvasA, canvasB) {
    const ctxA = canvasA.getContext("2d");
    const ctxB = canvasB.getContext("2d");
    const dataA = ctxA.getImageData(0, 0, 32, 18).data;
    const dataB = ctxB.getImageData(0, 0, 32, 18).data;

    let diff = 0;
    const total = 32 * 18 * 4;
    for (let i = 0; i < total; i += 4) {
      const lumA = 0.299 * dataA[i] + 0.587 * dataA[i + 1] + 0.114 * dataA[i + 2];
      const lumB = 0.299 * dataB[i] + 0.587 * dataB[i + 1] + 0.114 * dataB[i + 2];
      diff += Math.abs(lumA - lumB);
    }
    return diff / (32 * 18 * 255);
  }

  // ─────────────────────────────────────────────
  // Client-Side YouTube Caption / Transcript Extraction
  // ─────────────────────────────────────────────
  async function extractTranscriptFromPage() {
    try {
      console.log("[YT2PDF Companion] Detecting captions from YouTube browser session...");
      let captionTracks = null;

      // Strategy 1: Look for captionTracks in DOM <script> tags
      const scripts = document.querySelectorAll("script");
      for (const s of scripts) {
        const txt = s.textContent || "";
        if (txt.includes("captionTracks")) {
          const match = txt.match(/"captionTracks":\s*(\[.+?\])/);
          if (match) {
            try {
              const parsed = JSON.parse(match[1]);
              if (Array.isArray(parsed) && parsed.length > 0) {
                captionTracks = parsed;
                break;
              }
            } catch (e) {}
          }
        }
      }

      // Strategy 2: Query main-world player or window.ytInitialPlayerResponse via quick event
      if (!captionTracks || captionTracks.length === 0) {
        captionTracks = await new Promise((resolve) => {
          const eventHandler = (e) => {
            window.removeEventListener("yt2pdf_tracks_reply", eventHandler);
            resolve(e.detail?.tracks || null);
          };
          window.addEventListener("yt2pdf_tracks_reply", eventHandler);
          setTimeout(() => {
            window.removeEventListener("yt2pdf_tracks_reply", eventHandler);
            resolve(null);
          }, 800);

          const scriptEl = document.createElement("script");
          scriptEl.textContent = `
            (() => {
              try {
                let tracks = null;
                const player = document.getElementById("movie_player");
                if (player && typeof player.getOption === "function") {
                  tracks = player.getOption("captions", "tracklist");
                }
                if (!tracks && window.ytInitialPlayerResponse?.captions?.playerCaptionsTracklistRenderer?.captionTracks) {
                  tracks = window.ytInitialPlayerResponse.captions.playerCaptionsTracklistRenderer.captionTracks;
                }
                window.dispatchEvent(new CustomEvent("yt2pdf_tracks_reply", { detail: { tracks: tracks || null } }));
              } catch (err) {
                window.dispatchEvent(new CustomEvent("yt2pdf_tracks_reply", { detail: { tracks: null } }));
              }
            })();
          `;
          (document.head || document.documentElement).appendChild(scriptEl);
          scriptEl.remove();
        });
      }

      if (!captionTracks || captionTracks.length === 0) {
        console.log("[YT2PDF Companion] No caption tracks detected on page.");
        return [];
      }

      console.log(`[YT2PDF Companion] Found ${captionTracks.length} caption tracks. Selecting optimal language track...`);

      // Priority: English manual > English auto (asr) > non-auto track > any track
      let chosen = captionTracks.find(t => (t.languageCode === "en" || t.languageCode?.startsWith("en")) && t.kind !== "asr");
      if (!chosen) {
        chosen = captionTracks.find(t => t.languageCode === "en" || t.languageCode?.startsWith("en"));
      }
      if (!chosen) {
        chosen = captionTracks.find(t => t.kind !== "asr");
      }
      if (!chosen) {
        chosen = captionTracks[0];
      }

      if (!chosen || !chosen.baseUrl) {
        return [];
      }

      console.log(`[YT2PDF Companion] Selected caption track: ${chosen.languageCode} (${chosen.kind || "manual"}). Fetching timedtext...`);

      let segments = [];

      // Try fmt=json3 format first
      try {
        const jsonUrl = chosen.baseUrl + (chosen.baseUrl.includes("?") ? "&" : "?") + "fmt=json3";
        const res = await fetch(jsonUrl, { credentials: "include" });
        if (res.ok) {
          const raw = await res.text();
          if (raw && raw.trim().startsWith("{")) {
            const data = JSON.parse(raw);
            if (data.events && Array.isArray(data.events)) {
              for (const ev of data.events) {
                const s = (ev.tStartMs || 0) / 1000.0;
                const d = (ev.dDurationMs || 0) / 1000.0;
                const txt = (ev.segs || []).map(x => x.utf8 || "").join("").trim();
                if (txt && txt !== "\n") {
                  segments.push([s, s + d, txt]);
                }
              }
            }
          }
        }
      } catch (jsonErr) {
        console.warn("[YT2PDF Companion] json3 timedtext fetch error:", jsonErr);
      }

      // Fallback: XML timedtext if json3 yielded no segments
      if (segments.length === 0) {
        try {
          const res = await fetch(chosen.baseUrl, { credentials: "include" });
          if (res.ok) {
            const xml = await res.text();
            if (xml && xml.includes("<text")) {
              const parser = new DOMParser();
              const xmlDoc = parser.parseFromString(xml, "text/xml");
              const textNodes = xmlDoc.getElementsByTagName("text");
              for (let i = 0; i < textNodes.length; i++) {
                const node = textNodes[i];
                const s = parseFloat(node.getAttribute("start") || "0");
                const d = parseFloat(node.getAttribute("dur") || "0");
                const raw = (node.textContent || "")
                  .replace(/&#39;/g, "'")
                  .replace(/&quot;/g, '"')
                  .replace(/&amp;/g, '&')
                  .replace(/&lt;/g, '<')
                  .replace(/&gt;/g, '>')
                  .trim();
                if (raw) {
                  segments.push([s, s + d, raw]);
                }
              }
            }
          }
        } catch (xmlErr) {
          console.warn("[YT2PDF Companion] XML timedtext fetch error:", xmlErr);
        }
      }

      console.log(`[YT2PDF Companion] Successfully extracted ${segments.length} transcript segments from browser.`);
      return segments;

    } catch (err) {
      console.warn("[YT2PDF Companion] Extract transcript failed:", err);
      return [];
    }
  }

  // ─────────────────────────────────────────────
  // Extension Context & Direct Transmission Resilience
  // ─────────────────────────────────────────────
  function isExtensionContextValid() {
    try {
      return Boolean(typeof chrome !== "undefined" && chrome?.runtime && chrome.runtime.id);
    } catch (e) {
      return false;
    }
  }

  function safeSendRuntimeMessage(message, callback) {
    if (!isExtensionContextValid()) {
      if (callback) callback({ success: false, error: "Extension context invalidated" });
      return;
    }
    try {
      chrome.runtime.sendMessage(message, (res) => {
        const lastErr = chrome?.runtime?.lastError;
        if (lastErr) {
          if (callback) callback({ success: false, error: lastErr.message });
        } else {
          if (callback) callback(res || { success: true });
        }
      });
    } catch (e) {
      if (callback) callback({ success: false, error: e.message });
    }
  }

  async function directUploadFrames(payload) {
    const candidateBases = [
      "https://yt2pdfs.com",
      "https://yt2pdf-214301889618.europe-west1.run.app",
      "http://localhost:8080",
      "http://localhost:8000"
    ];

    let lastError = null;
    for (const base of candidateBases) {
      try {
        console.log(`[YT2PDF Companion] Direct upload attempt to ${base}...`);
        const response = await fetch(`${base}/api/companion/upload-frames`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Accept": "application/json"
          },
          body: JSON.stringify(payload)
        });

        if (response.ok) {
          const data = await response.json();
          data.backend_base = base;
          return data;
        } else {
          const errorText = await response.text().catch(() => "");
          lastError = new Error(`Server ${base} returned HTTP ${response.status}: ${errorText}`);
        }
      } catch (netErr) {
        console.warn(`[YT2PDF Companion] Direct upload network error on ${base}:`, netErr.message);
        lastError = netErr;
      }
    }

    throw lastError || new Error("Failed to reach YT2PDFS server.");
  }

  async function uploadFramesSafely(payload) {
    // 1. Try background service worker if extension context is alive
    if (isExtensionContextValid()) {
      try {
        const bgResult = await new Promise((resolve, reject) => {
          try {
            chrome.runtime.sendMessage({
              action: "upload_frames",
              payload: payload
            }, (res) => {
              const lastErr = chrome?.runtime?.lastError;
              if (lastErr) {
                reject(new Error(lastErr.message));
              } else if (res && res.success) {
                resolve(res.data);
              } else {
                reject(new Error(res?.error || "Background worker rejected upload"));
              }
            });
          } catch (syncErr) {
            reject(syncErr);
          }
        });
        return bgResult;
      } catch (bgErr) {
        console.warn("[YT2PDF Companion] Background worker error, falling back to direct upload:", bgErr.message);
      }
    } else {
      console.log("[YT2PDF Companion] Extension context disconnected/reloaded; proceeding with direct web upload.");
    }

    // 2. Fallback: Direct web upload (completely immune to extension context invalidation)
    return await directUploadFrames(payload);
  }

  // ─────────────────────────────────────────────
  // Silent Background Tab Mode (Website Automation)
  // ─────────────────────────────────────────────
  const isHeadless = new URLSearchParams(window.location.search).get("yt2pdf_headless") === "1";

  async function runHeadlessExtraction() {
    console.log("[YT2PDF Companion] Initializing silent background extraction...");
    let video = null;
    const waitStart = Date.now();

    // Poll for active video element with valid duration
    while (Date.now() - waitStart < 15000) {
      video = document.querySelector("video.html5-main-video");
      if (video && video.duration && !isNaN(video.duration) && video.duration > 0) {
        break;
      }
      await new Promise(r => setTimeout(r, 350));
    }

    if (!video || !video.duration || isNaN(video.duration)) {
      console.warn("[YT2PDF Companion] Video element not ready after 15s.");
      safeSendRuntimeMessage({
        action: "headless_extraction_failed",
        error: "Unable to load YouTube video stream in silent background tab."
      });
      return;
    }

    // Silence audio and pause video immediately
    video.muted = true;
    try {
      await video.play().catch(() => {});
      video.pause();
    } catch (e) {}

    // Auto-skip ad if present
    const skipBtn = document.querySelector(".ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern");
    if (skipBtn) {
      try { skipBtn.click(); } catch (e) {}
    }

    try {
      const duration = Math.floor(video.duration);
      const titleEl = document.querySelector("h1.ytd-watch-metadata yt-formatted-string") ||
                      document.querySelector("h1.title yt-formatted-string") ||
                      document.querySelector("h1.title");
      const videoTitle = titleEl ? titleEl.innerText.trim() : document.title.replace(" - YouTube", "").trim();
      const cleanUrl = window.location.href.replace(/([&?])yt2pdf_headless=1&?/, "$1").replace(/[?&]$/, "");

      let step = 10;
      if (duration > 3600) step = 45;
      else if (duration > 1800) step = 30;
      else if (duration > 600) step = 15;
      else step = 8;

      const samplePoints = [];
      for (let t = 2; t < duration - 2; t += step) {
        samplePoints.push(t);
      }
      const maxFrames = 30;
      const finalPoints = samplePoints.length > maxFrames
        ? samplePoints.filter((_, idx) => idx % Math.ceil(samplePoints.length / maxFrames) === 0)
        : samplePoints;

      const captureCanvas = document.createElement("canvas");
      captureCanvas.width = 1280;
      captureCanvas.height = 720;
      const captureCtx = captureCanvas.getContext("2d");

      const thumbCanvasA = document.createElement("canvas");
      thumbCanvasA.width = 32;
      thumbCanvasA.height = 18;
      const thumbCanvasB = document.createElement("canvas");
      thumbCanvasB.width = 32;
      thumbCanvasB.height = 18;

      const capturedSlides = [];
      let hasPreviousThumb = false;

      for (let i = 0; i < finalPoints.length; i++) {
        const timeTarget = finalPoints[i];
        await new Promise((resolve) => {
          const onSeeked = () => {
            video.removeEventListener("seeked", onSeeked);
            resolve();
          };
          video.addEventListener("seeked", onSeeked, { once: true });
          video.currentTime = timeTarget;
          setTimeout(resolve, 350);
        });

        const activeThumbCanvas = (capturedSlides.length % 2 === 0) ? thumbCanvasA : thumbCanvasB;
        const prevThumbCanvas = (capturedSlides.length % 2 === 0) ? thumbCanvasB : thumbCanvasA;
        const activeThumbCtx = activeThumbCanvas.getContext("2d");
        activeThumbCtx.drawImage(video, 0, 0, 32, 18);

        let isDistinct = true;
        if (hasPreviousThumb) {
          const diff = calculateDifference(activeThumbCanvas, prevThumbCanvas);
          if (diff < 0.07) isDistinct = false;
        }

        if (isDistinct) {
          hasPreviousThumb = true;
          captureCtx.drawImage(video, 0, 0, 1280, 720);
          capturedSlides.push({
            timestamp: timeTarget,
            time_formatted: `${Math.floor(timeTarget / 60)}:${String(timeTarget % 60).padStart(2, "0")}`,
            data: captureCanvas.toDataURL("image/jpeg", 0.78)
          });
        }
      }

      if (capturedSlides.length === 0) {
        captureCtx.drawImage(video, 0, 0, 1280, 720);
        capturedSlides.push({
          timestamp: 0,
          time_formatted: "0:00",
          data: captureCanvas.toDataURL("image/jpeg", 0.78)
        });
      }

      console.log(`[YT2PDF Companion] Silent extraction complete (${capturedSlides.length} slides). Transmitting...`);

      let transcriptSegments = [];
      try {
        transcriptSegments = await extractTranscriptFromPage();
      } catch (trErr) {
        console.warn("[YT2PDF Companion] Silent transcript extraction error:", trErr);
      }

      const payload = {
        video_url: cleanUrl,
        title: videoTitle,
        duration: duration,
        frames: capturedSlides,
        transcript: transcriptSegments
      };

      safeSendRuntimeMessage({
        action: "upload_frames",
        payload: payload,
        headless: true
      });

    } catch (err) {
      console.error("[YT2PDF Companion] Headless extraction error:", err);
      safeSendRuntimeMessage({
        action: "headless_extraction_failed",
        error: err.message
      });
    }
  }

  if (isHeadless) {
    runHeadlessExtraction();
    return; // Do not inject watch-page buttons or register watch-page listeners
  }

  // ─────────────────────────────────────────────
  // Watch Page Slide Extraction (Manual Trigger)
  // ─────────────────────────────────────────────
  async function startSlideExtraction(buttonEl) {
    if (isExtracting) return;
    const video = document.querySelector("video.html5-main-video");
    if (!video || !video.duration || isNaN(video.duration)) {
      showToast("Please wait for video to load before extracting slides.", true);
      return;
    }

    isExtracting = true;
    const originalContent = buttonEl.innerHTML;
    const originalTime = video.currentTime;
    const wasPaused = video.paused;

    // Mute and pause temporarily for quiet, background seeking
    const originalMuted = video.muted;
    video.muted = true;
    if (!wasPaused) video.pause();

    try {
      const duration = Math.floor(video.duration);
      const titleEl = document.querySelector("h1.ytd-watch-metadata yt-formatted-string") ||
                      document.querySelector("h1.title yt-formatted-string") ||
                      document.querySelector("h1.title");
      const videoTitle = titleEl ? titleEl.innerText.trim() : document.title.replace(" - YouTube", "").trim();
      const videoUrl = window.location.href;

      // Smart sample intervals based on video duration
      let step = 10;
      if (duration > 3600) step = 45;       // > 1 hr
      else if (duration > 1800) step = 30;  // 30-60 mins
      else if (duration > 600) step = 15;   // 10-30 mins
      else step = 8;                        // < 10 mins

      const samplePoints = [];
      for (let t = 2; t < duration - 2; t += step) {
        samplePoints.push(t);
      }

      // Max 32 frames to keep transmission fast (< 1.5MB)
      const maxFrames = 32;
      const finalPoints = samplePoints.length > maxFrames
        ? samplePoints.filter((_, idx) => idx % Math.ceil(samplePoints.length / maxFrames) === 0)
        : samplePoints;

      // Canvases
      const captureCanvas = document.createElement("canvas");
      captureCanvas.width = 1280;
      captureCanvas.height = 720;
      const captureCtx = captureCanvas.getContext("2d");

      const thumbCanvasA = document.createElement("canvas");
      thumbCanvasA.width = 32;
      thumbCanvasA.height = 18;
      const thumbCtxA = thumbCanvasA.getContext("2d");

      const thumbCanvasB = document.createElement("canvas");
      thumbCanvasB.width = 32;
      thumbCanvasB.height = 18;
      const thumbCtxB = thumbCanvasB.getContext("2d");

      const capturedSlides = [];
      let hasPreviousThumb = false;

      buttonEl.style.opacity = "0.9";

      for (let i = 0; i < finalPoints.length; i++) {
        const timeTarget = finalPoints[i];

        // Seek video
        await new Promise((resolve) => {
          const onSeeked = () => {
            video.removeEventListener("seeked", onSeeked);
            resolve();
          };
          video.addEventListener("seeked", onSeeked, { once: true });
          video.currentTime = timeTarget;
          setTimeout(resolve, 400); // Fallback timeout
        });

        // Update button status
        const pct = Math.round(((i + 1) / finalPoints.length) * 100);
        buttonEl.innerHTML = `
          <svg class="yt2pdf-spinner" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
          <span>${pct}% (${capturedSlides.length})</span>
        `;

        // Diff thumbnails
        const activeThumbCanvas = (capturedSlides.length % 2 === 0) ? thumbCanvasA : thumbCanvasB;
        const prevThumbCanvas = (capturedSlides.length % 2 === 0) ? thumbCanvasB : thumbCanvasA;
        const activeThumbCtx = activeThumbCanvas.getContext("2d");
        activeThumbCtx.drawImage(video, 0, 0, 32, 18);

        let isDistinct = true;
        if (hasPreviousThumb) {
          const diff = calculateDifference(activeThumbCanvas, prevThumbCanvas);
          if (diff < 0.07) {
            isDistinct = false;
          }
        }

        if (isDistinct) {
          hasPreviousThumb = true;
          captureCtx.drawImage(video, 0, 0, 1280, 720);
          const base64Data = captureCanvas.toDataURL("image/jpeg", 0.78);

          capturedSlides.push({
            timestamp: timeTarget,
            time_formatted: `${Math.floor(timeTarget / 60)}:${String(timeTarget % 60).padStart(2, "0")}`,
            data: base64Data
          });
        }
      }

      if (capturedSlides.length === 0) {
        captureCtx.drawImage(video, 0, 0, 1280, 720);
        capturedSlides.push({
          timestamp: originalTime,
          time_formatted: "0:00",
          data: captureCanvas.toDataURL("image/jpeg", 0.78)
        });
      }

      // Update button state: uploading
      buttonEl.innerHTML = `
        <svg class="yt2pdf-spinner" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
        <span>Uploading...</span>
      `;

      let transcriptSegments = [];
      try {
        transcriptSegments = await extractTranscriptFromPage();
      } catch (trErr) {
        console.warn("[YT2PDF Companion] Manual transcript extraction error:", trErr);
      }

      const payload = {
        video_url: videoUrl,
        title: videoTitle,
        duration: duration,
        frames: capturedSlides,
        transcript: transcriptSegments
      };

      // Upload frames: Safe multi-strategy upload (background worker with direct HTTP fallback)
      const uploadResult = await uploadFramesSafely(payload);

      const jobId = uploadResult.job_id;
      // Always direct users to the official domain yt2pdfs.com
      let webBase = "https://yt2pdfs.com";
      if (uploadResult.backend_base && (uploadResult.backend_base.includes("localhost") || uploadResult.backend_base.includes("127.0.0.1"))) {
        webBase = uploadResult.backend_base;
      }
      const destinationUrl = `${webBase}/?job_id=${jobId}`;

      buttonEl.innerHTML = `
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#2BA640" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
        <span>Opening...</span>
      `;

      showToast(`🎉 ${capturedSlides.length} slides captured! Opening YT2PDFS to download your PDF...`);

      // Open YT2PDFS in a new tab for curation & download
      window.open(destinationUrl, "_blank");

      setTimeout(() => {
        buttonEl.innerHTML = originalContent;
        buttonEl.style.opacity = "1";
        isExtracting = false;
      }, 3500);

    } catch (err) {
      console.error("[YT2PDF Companion Error]:", err);
      let errorMsg = err?.message || "Unknown error";
      if (errorMsg.includes("Extension context invalidated")) {
        errorMsg = "Extension was updated. Please refresh this tab (F5) to complete setup.";
      }
      showToast(`Extraction failed: ${errorMsg}`, true);
      buttonEl.innerHTML = originalContent;
      buttonEl.style.opacity = "1";
      isExtracting = false;
    }
 finally {
      video.currentTime = originalTime;
      video.muted = originalMuted;
      if (!wasPaused) video.play().catch(() => {});
    }
  }

  // ─────────────────────────────────────────────
  // Native-Styled Button Injection
  // ─────────────────────────────────────────────
  function injectButton() {
    if (document.getElementById("yt2pdf-action-btn")) return;

    // Target the actual button list container inside YouTube actions
    const topButtons = 
      document.querySelector("#top-level-buttons-computed") ||
      document.querySelector(".yt-flexible-actions-view-model") ||
      document.querySelector("ytd-menu-renderer #top-level-buttons-computed");

    const subscribeBtn = 
      document.querySelector("#owner #subscribe-button") ||
      document.querySelector("#subscribe-button ytd-subscribe-button-renderer");

    if (!topButtons && !subscribeBtn) return;

    // Create native YouTube action button
    const btn = document.createElement("button");
    btn.id = "yt2pdf-action-btn";
    btn.className = "yt-spec-button-shape-next yt-spec-button-shape-next--tonal yt-spec-button-shape-next--mono yt-spec-button-shape-next--size-m yt-spec-button-shape-next--icon-leading";
    btn.setAttribute("aria-label", "Convert slides to PDF on YT2PDFS.com");
    btn.title = "Extract presentation slides & generate PDF notes on YT2PDFS.com";

    btn.style.cssText = `
      display: inline-flex;
      align-items: center;
      gap: 6px;
      margin: 0 4px;
      padding: 0 14px;
      height: 36px;
      border-radius: 18px;
      border: none;
      background: rgba(255, 255, 255, 0.1);
      color: #F1F1F1;
      font-family: "Roboto", Arial, sans-serif;
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
      white-space: nowrap;
      transition: background 0.2s ease, transform 0.1s ease;
      vertical-align: middle;
      flex-shrink: 0;
      box-sizing: border-box;
      line-height: normal;
      user-select: none;
    `;

    btn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#FF4D4D" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
        <polyline points="14 2 14 8 20 8"></polyline>
        <line x1="16" y1="13" x2="8" y2="13"></line>
        <line x1="16" y1="17" x2="8" y2="17"></line>
        <polyline points="10 9 9 9 8 9"></polyline>
      </svg>
      <span style="font-size:14px;font-weight:500;">PDF Slides</span>
    `;

    btn.addEventListener("mouseenter", () => {
      btn.style.background = "rgba(255, 255, 255, 0.2)";
      btn.style.color = "#FFFFFF";
    });

    btn.addEventListener("mouseleave", () => {
      btn.style.background = "rgba(255, 255, 255, 0.1)";
      btn.style.color = "#F1F1F1";
    });

    btn.addEventListener("mousedown", () => {
      btn.style.transform = "scale(0.97)";
    });

    btn.addEventListener("mouseup", () => {
      btn.style.transform = "scale(1)";
    });

    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      startSlideExtraction(btn);
    });

    // Placement logic:
    // Insert into topButtons right after Like/Dislike segment, or prepend
    if (topButtons) {
      if (topButtons.children.length > 1) {
        topButtons.insertBefore(btn, topButtons.children[1]);
      } else {
        topButtons.prepend(btn);
      }
    } else if (subscribeBtn && subscribeBtn.parentNode) {
      subscribeBtn.parentNode.insertBefore(btn, subscribeBtn.nextSibling);
    }
  }

  // Animation styles
  const style = document.createElement("style");
  style.textContent = `
    @keyframes yt2pdf-spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
    @keyframes yt2pdf-fade-in {
      from { opacity: 0; transform: translateY(10px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .yt2pdf-spinner {
      animation: yt2pdf-spin 0.8s linear infinite;
    }
  `;
  document.head.appendChild(style);

  // Periodic and SPA navigation watcher
  setInterval(injectButton, 1000);
  window.addEventListener("yt-navigate-finish", injectButton);
  window.addEventListener("spfdone", injectButton);
  window.addEventListener("popstate", injectButton);

  // Message listener for popup
  if (isExtensionContextValid() && chrome?.runtime?.onMessage) {
    try {
      chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
        if (request.action === "extract_slides") {
          const btn = document.getElementById("yt2pdf-action-btn") || document.createElement("button");
          startSlideExtraction(btn);
          try {
            sendResponse({ started: true });
          } catch (e) {}
        }
        return true;
      });
    } catch (e) {}
  }
})();
