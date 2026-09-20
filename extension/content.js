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
  // Background Slide Extraction
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

      const payload = {
        video_url: videoUrl,
        title: videoTitle,
        duration: duration,
        frames: capturedSlides
      };

      // Upload frames: First try background service worker (bypasses page CORS completely)
      let uploadResult = null;
      let uploadError = null;

      try {
        uploadResult = await new Promise((resolve, reject) => {
          if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
            chrome.runtime.sendMessage({
              action: "upload_frames",
              payload: payload
            }, (res) => {
              if (chrome.runtime.lastError) {
                reject(new Error(chrome.runtime.lastError.message + ". Please reload extension in chrome://extensions"));
              } else if (res && res.success) {
                resolve(res.data);
              } else {
                reject(new Error(res?.error || "Server upload failed"));
              }
            });
          } else {
            reject(new Error("Extension messaging unavailable. Please reload extension."));
          }
        });
      } catch (bgErr) {
        console.error("[YT2PDF Companion] Background worker error:", bgErr);
        throw bgErr;
      }

      const jobId = uploadResult.job_id;
      let destinationUrl = `https://yt2pdfs.com/?job_id=${jobId}`;
      if (uploadResult.backend_base && (uploadResult.backend_base.includes("localhost") || uploadResult.backend_base.includes("127.0.0.1"))) {
        destinationUrl = `${uploadResult.backend_base}/?job_id=${jobId}`;
      }

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
      showToast(`Extraction failed: ${err.message}`, true);
      buttonEl.innerHTML = originalContent;
      buttonEl.style.opacity = "1";
      isExtracting = false;
    } finally {
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
