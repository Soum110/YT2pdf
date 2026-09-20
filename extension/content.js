/**
 * content.js — YT2PDF Browser Companion
 * Injects a native-styled "Generate PDF via YT2PDFS" button into YouTube
 * and captures clean slide frames directly from playback with zero server bot blocks.
 */

(function () {
  const BACKEND_URLS = [
    "https://yt2pdfs.com",
    "https://yt2pdf-214301889618.europe-west1.run.app"
  ];

  let isExtracting = false;

  // ─────────────────────────────────────────────
  // Helper: Get best available backend
  // ─────────────────────────────────────────────
  async function getWorkingBackend() {
    for (const base of BACKEND_URLS) {
      try {
        const res = await fetch(`${base}/api/health`, { method: "GET", signal: AbortSignal.timeout(3000) });
        if (res.ok) return base;
      } catch (e) {}
    }
    return BACKEND_URLS[0];
  }

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
      bottom: 32px;
      right: 32px;
      z-index: 999999;
      background: ${isError ? "#C5221F" : "#0F0F0F"};
      color: #FFFFFF;
      border: 1px solid ${isError ? "#EA4335" : "#303030"};
      border-radius: 12px;
      padding: 14px 20px;
      font-family: Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      font-weight: 500;
      box-shadow: 0 10px 30px rgba(0,0,0,0.4);
      display: flex;
      align-items: center;
      gap: 12px;
      animation: yt2pdf-fade-in 0.25s ease;
      max-width: 420px;
    `;

    toast.innerHTML = `
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#FF4D4D" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
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
        toast.style.transition = "opacity 0.4s ease, transform 0.4s ease";
        toast.style.opacity = "0";
        toast.style.transform = "translateY(10px)";
        setTimeout(() => toast.remove(), 400);
      }
    }, 5000);
  }

  // ─────────────────────────────────────────────
  // Perceptual frame difference
  // ─────────────────────────────────────────────
  function calculateDifference(canvasA, canvasB) {
    // Compares two 32x18 downscaled canvases
    const ctxA = canvasA.getContext("2d");
    const ctxB = canvasB.getContext("2d");
    const dataA = ctxA.getImageData(0, 0, 32, 18).data;
    const dataB = ctxB.getImageData(0, 0, 32, 18).data;

    let diff = 0;
    const total = 32 * 18 * 4;
    for (let i = 0; i < total; i += 4) {
      // Grayscale luminance difference
      const lumA = 0.299 * dataA[i] + 0.587 * dataA[i + 1] + 0.114 * dataA[i + 2];
      const lumB = 0.299 * dataB[i] + 0.587 * dataB[i + 1] + 0.114 * dataB[i + 2];
      diff += Math.abs(lumA - lumB);
    }
    return diff / (32 * 18 * 255);
  }

  // ─────────────────────────────────────────────
  // Core: Background Slide Extraction
  // ─────────────────────────────────────────────
  async function startSlideExtraction(buttonEl) {
    if (isExtracting) return;
    const video = document.querySelector("video.html5-main-video");
    if (!video || !video.duration || isNaN(video.duration)) {
      showToast("Please wait for the video to load before extracting slides.", true);
      return;
    }

    isExtracting = true;
    const originalText = buttonEl.innerHTML;
    const originalTime = video.currentTime;
    const wasPaused = video.paused;

    // Temporarily mute and pause so seeking doesn't blast audio
    const originalMuted = video.muted;
    video.muted = true;
    if (!wasPaused) video.pause();

    try {
      const duration = Math.floor(video.duration);
      const titleEl = document.querySelector("h1.ytd-watch-metadata yt-formatted-string") || document.querySelector("h1.title");
      const videoTitle = titleEl ? titleEl.innerText.trim() : document.title.replace(" - YouTube", "").trim();
      const videoUrl = window.location.href;

      // Determine smart step intervals
      let step = 10;
      if (duration > 3600) step = 45;       // > 1 hour: sample every 45s
      else if (duration > 1800) step = 30;  // 30-60 mins: every 30s
      else if (duration > 600) step = 15;   // 10-30 mins: every 15s
      else step = 8;                        // < 10 mins: every 8s

      const samplePoints = [];
      for (let t = 2; t < duration - 2; t += step) {
        samplePoints.push(t);
      }

      // Max 60 sample frames to keep payload lightweight and fast
      const finalPoints = samplePoints.length > 60
        ? samplePoints.filter((_, idx) => idx % Math.ceil(samplePoints.length / 60) === 0)
        : samplePoints;

      // Setup capture canvases
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
          // Fallback timeout in case seeked event drops
          setTimeout(resolve, 350);
        });

        // Update button progress
        const pct = Math.round(((i + 1) / finalPoints.length) * 100);
        buttonEl.innerHTML = `
          <svg class="yt2pdf-spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
          <span>Scanning ${pct}% (${capturedSlides.length} slides)</span>
        `;

        // Render to thumbnail for fast diff
        const activeThumbCanvas = (capturedSlides.length % 2 === 0) ? thumbCanvasA : thumbCanvasB;
        const prevThumbCanvas = (capturedSlides.length % 2 === 0) ? thumbCanvasB : thumbCanvasA;
        const activeThumbCtx = activeThumbCanvas.getContext("2d");
        activeThumbCtx.drawImage(video, 0, 0, 32, 18);

        let isDistinct = true;
        if (hasPreviousThumb) {
          const diff = calculateDifference(activeThumbCanvas, prevThumbCanvas);
          if (diff < 0.08) {
            isDistinct = false; // Duplicate / minor motion; skip
          }
        }

        if (isDistinct) {
          hasPreviousThumb = true;
          // Render full resolution frame
          captureCtx.drawImage(video, 0, 0, 1280, 720);
          const base64Data = captureCanvas.toDataURL("image/jpeg", 0.85);

          capturedSlides.push({
            timestamp: timeTarget,
            time_formatted: `${Math.floor(timeTarget / 60)}:${String(timeTarget % 60).padStart(2, "0")}`,
            data: base64Data
          });
        }
      }

      if (capturedSlides.length === 0) {
        // Fallback: grab current frame
        captureCtx.drawImage(video, 0, 0, 1280, 720);
        capturedSlides.push({
          timestamp: originalTime,
          time_formatted: "0:00",
          data: captureCanvas.toDataURL("image/jpeg", 0.85)
        });
      }

      // Update button state for transmission
      buttonEl.innerHTML = `
        <svg class="yt2pdf-spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
        <span>Sending to YT2PDFS...</span>
      `;

      // Upload frames to YT2PDFS backend
      const backendBase = await getWorkingBackend();
      const payload = {
        video_url: videoUrl,
        title: videoTitle,
        duration: duration,
        frames: capturedSlides
      };

      const response = await fetch(`${backendBase}/api/companion/upload-frames`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        throw new Error(`Server returned HTTP ${response.status}`);
      }

      const result = await response.json();
      const jobId = result.job_id;
      const destinationUrl = `https://yt2pdfs.com/?job_id=${jobId}`;

      buttonEl.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#2BA640" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
        <span>Ready! Opening...</span>
      `;

      showToast(`🎉 ${capturedSlides.length} slides captured! Opening YT2PDFS to download your PDF...`);

      // Open YT2PDFS in a new tab so user reviews and downloads their PDF
      window.open(destinationUrl, "_blank");

      setTimeout(() => {
        buttonEl.innerHTML = originalText;
        buttonEl.style.opacity = "1";
        isExtracting = false;
      }, 3000);

    } catch (err) {
      console.error("[YT2PDF Companion Error]:", err);
      showToast(`Extraction failed: ${err.message}`, true);
      buttonEl.innerHTML = originalText;
      buttonEl.style.opacity = "1";
      isExtracting = false;
    } finally {
      // Restore playback state
      video.currentTime = originalTime;
      video.muted = originalMuted;
      if (!wasPaused) {
        video.play().catch(() => {});
      }
    }
  }

  // ─────────────────────────────────────────────
  // Inject "Generate PDF" Button into YouTube UI
  // ─────────────────────────────────────────────
  function injectButton() {
    if (document.getElementById("yt2pdf-action-btn")) return;

    // Target YouTube's action bar containers
    const actionsBar = 
      document.querySelector("#actions.ytd-watch-metadata #top-row") ||
      document.querySelector("ytd-watch-metadata #actions-inner") ||
      document.querySelector("#top-level-buttons-computed") ||
      document.querySelector("#actions.ytd-watch-metadata") ||
      document.querySelector(".yt-flexible-actions-view-model");

    if (!actionsBar) return;

    // Create native-styled YouTube pill button
    const btn = document.createElement("button");
    btn.id = "yt2pdf-action-btn";
    btn.className = "yt-spec-button-shape-next yt-spec-button-shape-next--tonal yt-spec-button-shape-next--mono yt-spec-button-shape-next--size-m";
    btn.style.cssText = `
      display: inline-flex;
      align-items: center;
      gap: 7px;
      margin-left: 8px;
      padding: 0 16px;
      height: 36px;
      border-radius: 18px;
      border: 1px solid rgba(255, 255, 255, 0.15);
      background: rgba(255, 0, 0, 0.12);
      color: #FFFFFF;
      font-family: Roboto, Arial, sans-serif;
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.2s, transform 0.15s;
      vertical-align: middle;
      z-index: 10;
    `;

    btn.innerHTML = `
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#FF4D4D" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
        <polyline points="14 2 14 8 20 8"></polyline>
        <line x1="16" y1="13" x2="8" y2="13"></line>
        <line x1="16" y1="17" x2="8" y2="17"></line>
        <polyline points="10 9 9 9 8 9"></polyline>
      </svg>
      <span style="letter-spacing: 0.1px;">Generate PDF (YT2PDFS)</span>
    `;

    btn.title = "Capture presentation slides & generate PDF notes on YT2PDFS.com";

    btn.addEventListener("mouseenter", () => {
      btn.style.background = "rgba(255, 0, 0, 0.22)";
      btn.style.transform = "scale(1.02)";
    });

    btn.addEventListener("mouseleave", () => {
      btn.style.background = "rgba(255, 0, 0, 0.12)";
      btn.style.transform = "scale(1)";
    });

    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      startSlideExtraction(btn);
    });

    // Insert button near the actions
    actionsBar.appendChild(btn);
  }

  // Inject spinner animation styles
  const style = document.createElement("style");
  style.textContent = `
    @keyframes yt2pdf-spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
    @keyframes yt2pdf-fade-in {
      from { opacity: 0; transform: translateY(12px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .yt2pdf-spinner {
      animation: yt2pdf-spin 0.8s linear infinite;
    }
  `;
  document.head.appendChild(style);

  // ─────────────────────────────────────────────
  // Navigation & SPA Watcher
  // ─────────────────────────────────────────────
  setInterval(injectButton, 1200);
  window.addEventListener("yt-navigate-finish", injectButton);
  window.addEventListener("spfdone", injectButton);
  window.addEventListener("popstate", injectButton);

  // Listen for trigger messages from popup
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
      if (request.action === "extract_slides") {
        const btn = document.getElementById("yt2pdf-action-btn") || document.createElement("button");
        startSlideExtraction(btn);
        sendResponse({ started: true });
      }
      return true;
    });
  }
})();
