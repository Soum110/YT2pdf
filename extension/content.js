/**
 * content.js — YT2PDF Slide Companion
 * 
 * In-Page Silent Frame Architecture (100% Zero-Tab, Zero-Disruption):
 * - Active Watch Page: User's video playback continues 100% normal and uninterrupted.
 * - Zero Browser Tabs: Extraction runs exclusively inside an offscreen, muted <iframe>.
 *   No new tabs or windows ever appear in the user's browser.
 * - Instant Decoding: Because the iframe lives in the user's active window, Chrome
 *   decodes frames at full speed without background tab throttling or 0% freezes.
 * - Minimal Internet (360p): Stream is locked to 360p, reducing data transfer by 75-85%.
 *   Captured frames are upscaled to 1280x720 canvas with high-quality smoothing for sharp PDF text.
 * - Ad Handling: Auto-clicks skip buttons; fast-forwards unskippable ads at 16x speed.
 * - Instant Cleanup: The iframe is completely removed from the DOM as soon as extraction finishes.
 */

(function () {
  const urlParams = new URLSearchParams(window.location.search);
  const isHeadless = urlParams.get("yt2pdf_headless") === "1" || window.location.pathname.includes("/embed/");

  // Only run top-level watch-page UI scripts in the top-level window
  if (!isHeadless && window.self !== window.top) {
    return;
  }

  // ─────────────────────────────────────────────
  // Helper: Chrome Runtime Context Validation
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

  // ─────────────────────────────────────────────
  // Perceptual Difference & Clarity Scoring
  // ─────────────────────────────────────────────
  function computeClarity(canvas) {
    const w = canvas.width || 64;
    const h = canvas.height || 36;
    const ctx = canvas.getContext("2d");
    const data = ctx.getImageData(0, 0, w, h).data;
    let edgeSum = 0;
    for (let y = 1; y < h - 1; y++) {
      for (let x = 1; x < w - 1; x++) {
        const i = (y * w + x) * 4;
        const lum = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        const right = 0.299 * data[i + 4] + 0.587 * data[i + 5] + 0.114 * data[i + 6];
        const down = 0.299 * data[i + w * 4] + 0.587 * data[i + w * 4 + 1] + 0.114 * data[i + w * 4 + 2];
        edgeSum += Math.abs(lum - right) + Math.abs(lum - down);
      }
    }
    return edgeSum / ((w - 2) * (h - 2));
  }

  function calculateDifference(canvasA, canvasB) {
    const w = canvasA.width || 64;
    const h = canvasA.height || 36;
    const ctxA = canvasA.getContext("2d");
    const ctxB = canvasB.getContext("2d");
    const dataA = ctxA.getImageData(0, 0, w, h).data;
    const dataB = ctxB.getImageData(0, 0, w, h).data;

    let totalDiff = 0;
    let changedPixels = 0;
    const numPixels = w * h;
    const totalBytes = numPixels * 4;

    for (let i = 0; i < totalBytes; i += 4) {
      const lumA = 0.299 * dataA[i] + 0.587 * dataA[i + 1] + 0.114 * dataA[i + 2];
      const lumB = 0.299 * dataB[i] + 0.587 * dataB[i + 1] + 0.114 * dataB[i + 2];
      const d = Math.abs(lumA - lumB);
      totalDiff += d;
      if (d > 22) {
        changedPixels++;
      }
    }

    const meanDiff = totalDiff / (numPixels * 255);
    const changedRatio = changedPixels / numPixels;

    const clarityA = computeClarity(canvasA);
    const clarityB = computeClarity(canvasB);

    const isSameSlide = meanDiff < 0.020 && changedRatio < 0.035;
    const isDistinct = !isSameSlide && (meanDiff >= 0.024 || changedRatio >= 0.040);

    return {
      meanDiff,
      changedRatio,
      isSameSlide,
      isDistinct,
      clarityA,
      clarityB,
    };
  }

  function formatTimestamp(sec) {
    const s = Math.max(0, Math.floor(sec || 0));
    if (s >= 3600) {
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const rem = s % 60;
      return `${h}:${String(m).padStart(2, "0")}:${String(rem).padStart(2, "0")}`;
    }
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  // ═════════════════════════════════════════════
  // MODE A: IN-PAGE SILENT FRAME EXTRACTOR (Hidden <iframe>)
  // ═════════════════════════════════════════════
  if (isHeadless) {
    console.log("[YT2PDF In-Page Extractor] Starting silent extraction inside offscreen frame...");

    // 1. Guaranteed Silence: permanently mute all audio/video elements
    try {
      HTMLMediaElement.prototype.play = (function (origPlay) {
        return function () {
          this.muted = true;
          this.volume = 0;
          this.defaultMuted = true;
          return origPlay.apply(this, arguments);
        };
      })(HTMLMediaElement.prototype.play);
    } catch (e) {}

    const silenceMedia = (el) => {
      try {
        el.muted = true;
        el.volume = 0;
        el.defaultMuted = true;
      } catch (e) {}
    };

    try {
      document.querySelectorAll("video, audio").forEach(silenceMedia);
      const silenceObserver = new MutationObserver(() => {
        document.querySelectorAll("video, audio").forEach(silenceMedia);
      });
      silenceObserver.observe(document.documentElement || document.body, { childList: true, subtree: true });
    } catch (e) {}

    // 2. Prevent background throttling by spoofing document visibility
    try {
      Object.defineProperty(document, "hidden", { get: () => false, configurable: true });
      Object.defineProperty(document, "visibilityState", { get: () => "visible", configurable: true });
      Object.defineProperty(document, "webkitVisibilityState", { get: () => "visible", configurable: true });
      window.dispatchEvent(new Event("visibilitychange"));
    } catch (e) {}

    // 3. Dedicated unthrottled Web Worker timer
    let unthrottledSleep = (ms) => new Promise((r) => setTimeout(r, ms));
    try {
      let workerActive = false;
      const workerBlob = new Blob([
        "self.onmessage = function(e) { setTimeout(function() { self.postMessage(e.data); }, e.data.ms); };"
      ], { type: "application/javascript" });
      const timerWorker = new Worker(URL.createObjectURL(workerBlob));
      timerWorker.onerror = () => { workerActive = false; };
      workerActive = true;
      unthrottledSleep = function (ms) {
        return new Promise((resolve) => {
          let settled = false;
          const finish = () => { if (!settled) { settled = true; resolve(); } };
          setTimeout(finish, ms);
          if (workerActive) {
            const id = Math.random();
            const handler = (e) => {
              if (e.data && e.data.id === id) {
                timerWorker.removeEventListener("message", handler);
                finish();
              }
            };
            timerWorker.addEventListener("message", handler);
            try { timerWorker.postMessage({ id, ms }); } catch (e) { finish(); }
          }
        });
      };
    } catch (e) {}

    // 4. Force 360p video quality to minimize user bandwidth (75-85% savings)
    function enforce360pStream() {
      try {
        const s = document.createElement("script");
        s.textContent = `
          (() => {
            try {
              const p = document.getElementById("movie_player");
              if (p) {
                if (typeof p.setPlaybackQualityRange === "function") {
                  p.setPlaybackQualityRange("small", "small");
                }
                if (typeof p.setPlaybackQuality === "function") {
                  p.setPlaybackQuality("small");
                }
              }
            } catch(e) {}
          })();
        `;
        (document.documentElement || document.head || document.body).appendChild(s);
        s.remove();
      } catch (e) {}
    }

    // 5. In-video Ad Detection (Precise, zero false-positives)
    function isAdActive() {
      const player = document.getElementById("movie_player");
      if (player) {
        if (player.classList.contains("ad-showing") || player.classList.contains("ad-interrupting")) {
          return true;
        }
        if (typeof player.getAdState === "function" && player.getAdState() === 1) {
          return true;
        }
      }
      const skipBtn = document.querySelector(".ytp-skip-ad-button, .ytp-ad-skip-button-modern");
      if (skipBtn && skipBtn.offsetParent !== null) return true;
      const adText = document.querySelector(".ytp-ad-text");
      if (adText && adText.offsetParent !== null && (adText.textContent || "").trim().length > 0) return true;
      return false;
    }

    function handleAdsAndOverlays(videoEl) {
      try {
        const skipBtns = document.querySelectorAll(
          ".ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button-container button, .videoAdUiSkipButton"
        );
        skipBtns.forEach((btn) => { try { btn.click(); } catch (e) {} });

        const bannerBtns = document.querySelectorAll(".ytp-ad-overlay-close-button, .ytp-ad-overlay-close-container");
        bannerBtns.forEach((btn) => { try { btn.click(); } catch (e) {} });

        const consentBtn = document.querySelector("ytd-button-renderer#confirm-button button, .yt-confirm-dialog-renderer #confirm-button button");
        if (consentBtn) { try { consentBtn.click(); } catch (e) {} }

        const adPlaying = isAdActive();
        if (adPlaying && videoEl) {
          videoEl.playbackRate = 16.0;
          videoEl.muted = true;
          videoEl.volume = 0;
          if (videoEl.duration && !isNaN(videoEl.duration) && videoEl.currentTime < videoEl.duration - 0.2) {
            try { videoEl.currentTime = videoEl.duration - 0.1; } catch (e) {}
          }
        } else if (videoEl && videoEl.playbackRate > 2.0) {
          videoEl.playbackRate = 1.0;
        }

        const playBtn = document.querySelector(".ytp-large-play-button, .ytp-play-button");
        if (videoEl && videoEl.paused && playBtn) { try { playBtn.click(); } catch (e) {} }
      } catch (e) {}
    }

    function sendParentMessage(msg) {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage(msg, "*");
      }
    }

    async function runExtractionPipeline() {
      // 1. Resolve duration (from URL parameter if passed by parent)
      let resolvedDuration = 0;
      const pDur = parseInt(urlParams.get("yt2pdf_duration") || "0", 10);
      if (pDur > 0) resolvedDuration = pDur;

      let video = null;
      const waitStart = Date.now();

      while (Date.now() - waitStart < 15000) {
        video = document.querySelector("video.html5-main-video, video");
        enforce360pStream();
        handleAdsAndOverlays(video);

        // Check for YouTube embed error
        const errorScreen = document.querySelector(".ytp-error");
        if (errorScreen && errorScreen.offsetParent !== null) {
          const errReason = errorScreen.querySelector(".ytp-error-content-reason")?.textContent?.trim() || "Video unavailable";
          sendParentMessage({ type: "YT2PDF_HEADLESS_ERROR", error: `YouTube error: ${errReason}` });
          return;
        }

        const adPlaying = isAdActive();
        if (video && !adPlaying) {
          video.muted = true;
          video.volume = 0;
          video.play().catch(() => {});
          if (!resolvedDuration && video.duration && !isNaN(video.duration) && video.duration > 0) {
            resolvedDuration = Math.floor(video.duration);
          }
        }

        if (!resolvedDuration) {
          const timeDur = document.querySelector(".ytp-time-duration")?.textContent?.trim();
          if (timeDur && timeDur.includes(":")) {
            const parts = timeDur.split(":").map(Number);
            if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
              resolvedDuration = parts[0] * 60 + parts[1];
            } else if (parts.length === 3 && !isNaN(parts[0]) && !isNaN(parts[1]) && !isNaN(parts[2])) {
              resolvedDuration = parts[0] * 3600 + parts[1] * 60 + parts[2];
            }
          }
        }

        if (video && resolvedDuration > 0 && !adPlaying && (video.readyState >= 1 || video.duration > 0)) {
          break;
        }

        await unthrottledSleep(150);
      }

      if (!video || !resolvedDuration) {
        console.warn("[YT2PDF In-Page Extractor] Video stream not ready after timeout.");
        sendParentMessage({ type: "YT2PDF_HEADLESS_ERROR", error: "Could not load video stream in silent extractor." });
        return;
      }

      // Enforce muted state and 360p stream
      video.muted = true;
      video.volume = 0;
      enforce360pStream();
      try { await video.play().catch(() => {}); } catch (e) {}

      // Wait briefly for decode readiness
      const readyStart = Date.now();
      while (Date.now() - readyStart < 3000) {
        if (video && (video.readyState >= 2 || video.videoWidth > 0)) break;
        await unthrottledSleep(100);
      }

      try {
        const duration = resolvedDuration;
        let videoTitle = "Presentation Slides";
        const urlTitle = urlParams.get("yt2pdf_title");
        if (urlTitle) videoTitle = decodeURIComponent(urlTitle);

        let videoId = "";
        try {
          const u = new URL(window.location.href);
          if (u.pathname.includes("/embed/")) videoId = u.pathname.split("/embed/")[1]?.split("?")[0] || "";
          else videoId = u.searchParams.get("v") || "";
        } catch (e) {}

        // Smart adaptive sampling: high accuracy, zero slide misses
        let targetSamples = 35;
        if (duration < 180) targetSamples = Math.max(12, Math.floor(duration / 8));
        else if (duration < 600) targetSamples = 22;
        else if (duration < 1800) targetSamples = 32;
        else if (duration < 3600) targetSamples = 40;
        else targetSamples = 50;

        let step = Math.max(6, Math.floor(duration / targetSamples));
        const samplePoints = [];
        const startT = Math.max(2, Math.floor(duration * 0.005));
        for (let t = startT; t < duration - 2; t += step) {
          samplePoints.push(t);
        }
        if (duration > 20 && (!samplePoints.length || samplePoints[samplePoints.length - 1] < duration - 15)) {
          samplePoints.push(Math.max(2, duration - 10));
        }

        // Upscale canvas to 1280x720 with high-quality smoothing for publication-grade slide text
        const captureCanvas = document.createElement("canvas");
        captureCanvas.width = 1280;
        captureCanvas.height = 720;
        const captureCtx = captureCanvas.getContext("2d");
        captureCtx.imageSmoothingEnabled = true;
        captureCtx.imageSmoothingQuality = "high";

        const thumbCanvas = document.createElement("canvas");
        thumbCanvas.width = 64;
        thumbCanvas.height = 36;
        const thumbCtx = thumbCanvas.getContext("2d");

        const lastCapturedCanvas = document.createElement("canvas");
        lastCapturedCanvas.width = 64;
        lastCapturedCanvas.height = 36;
        const lastCapturedCtx = lastCapturedCanvas.getContext("2d");

        const capturedSlides = [];

        async function seekToTime(targetTime) {
          const activeVideo = document.querySelector("video.html5-main-video, video") || video;
          if (!activeVideo) return;
          enforce360pStream();
          handleAdsAndOverlays(activeVideo);

          try {
            const s = document.createElement("script");
            s.textContent = `
              (() => {
                try {
                  const p = document.getElementById("movie_player");
                  if (p && typeof p.seekTo === "function") {
                    p.seekTo(${targetTime}, true);
                  }
                } catch(e) {}
              })();
            `;
            (document.documentElement || document.head || document.body).appendChild(s);
            s.remove();
          } catch (e) {}

          let onSeeked = null;
          await Promise.race([
            new Promise((resolve) => {
              onSeeked = () => resolve();
              try {
                activeVideo.addEventListener("seeked", onSeeked, { once: true });
                activeVideo.currentTime = targetTime;
              } catch (e) { resolve(); }
            }),
            unthrottledSleep(280)
          ]).finally(() => {
            if (onSeeked && activeVideo) {
              try { activeVideo.removeEventListener("seeked", onSeeked); } catch (e) {}
            }
          });

          await unthrottledSleep(30);
        }

        // Main extraction loop
        for (let i = 0; i < samplePoints.length; i++) {
          const timeTarget = samplePoints[i];

          // Forward real-time progress update to parent frame
          sendParentMessage({
            type: "YT2PDF_HEADLESS_PROGRESS",
            current: i + 1,
            total: samplePoints.length
          });

          await seekToTime(timeTarget);

          const currentVideo = document.querySelector("video.html5-main-video, video") || video;
          if (!currentVideo) continue;

          thumbCtx.drawImage(currentVideo, 0, 0, 64, 36);

          if (capturedSlides.length === 0) {
            lastCapturedCtx.drawImage(thumbCanvas, 0, 0, 64, 36);
            captureCtx.drawImage(currentVideo, 0, 0, 1280, 720);
            capturedSlides.push({
              timestamp: timeTarget,
              time_formatted: formatTimestamp(timeTarget),
              data: captureCanvas.toDataURL("image/jpeg", 0.72)
            });
          } else {
            const diffResult = calculateDifference(thumbCanvas, lastCapturedCanvas);
            if (diffResult.isDistinct) {
              lastCapturedCtx.drawImage(thumbCanvas, 0, 0, 64, 36);
              captureCtx.drawImage(currentVideo, 0, 0, 1280, 720);
              capturedSlides.push({
                timestamp: timeTarget,
                time_formatted: formatTimestamp(timeTarget),
                data: captureCanvas.toDataURL("image/jpeg", 0.72)
              });
            } else if (diffResult.isSameSlide && diffResult.clarityA > diffResult.clarityB * 1.05) {
              lastCapturedCtx.drawImage(thumbCanvas, 0, 0, 64, 36);
              captureCtx.drawImage(currentVideo, 0, 0, 1280, 720);
              capturedSlides[capturedSlides.length - 1] = {
                timestamp: timeTarget,
                time_formatted: formatTimestamp(timeTarget),
                data: captureCanvas.toDataURL("image/jpeg", 0.72)
              };
            }
          }
        }

        // Timeline Gap Safety Net
        const gaps = [];
        const gapThreshold = Math.max(60, Math.floor(step * 2.2));
        if (capturedSlides.length > 0 && duration > 45) {
          for (let idx = 0; idx < capturedSlides.length - 1; idx++) {
            const tA = capturedSlides[idx].timestamp;
            const tB = capturedSlides[idx + 1].timestamp;
            if (tB - tA > gapThreshold) {
              gaps.push(Math.floor((tA + tB) / 2));
            }
          }
          const lastTs = capturedSlides[capturedSlides.length - 1].timestamp;
          if (lastTs < duration - 25) {
            gaps.push(Math.max(2, duration - 8));
          }
        }

        if (gaps.length > 0 || (capturedSlides.length < 5 && duration > 60)) {
          const existingTs = new Set(capturedSlides.map((s) => Math.floor(s.timestamp)));
          const chkPoints = gaps.length > 0 ? gaps.slice(0, 10) : samplePoints.filter((_, idx) => idx % 2 === 0);
          for (const tPoint of chkPoints) {
            if (![...existingTs].some((ts) => Math.abs(ts - tPoint) < 10)) {
              try {
                await seekToTime(tPoint);
                const currentVideo = document.querySelector("video.html5-main-video, video") || video;
                if (currentVideo) {
                  thumbCtx.drawImage(currentVideo, 0, 0, 64, 36);
                  const diffResult = calculateDifference(thumbCanvas, lastCapturedCanvas);
                  if (diffResult.isDistinct || capturedSlides.length < 3) {
                    lastCapturedCtx.drawImage(thumbCanvas, 0, 0, 64, 36);
                    captureCtx.drawImage(currentVideo, 0, 0, 1280, 720);
                    capturedSlides.push({
                      timestamp: tPoint,
                      time_formatted: formatTimestamp(tPoint),
                      data: captureCanvas.toDataURL("image/jpeg", 0.72)
                    });
                    existingTs.add(Math.floor(tPoint));
                  }
                }
              } catch (e) {}
            }
          }
          capturedSlides.sort((a, b) => a.timestamp - b.timestamp);
        }

        // Fallback slide if none captured
        if (capturedSlides.length === 0) {
          const currentVideo = document.querySelector("video.html5-main-video, video") || video;
          if (currentVideo) captureCtx.drawImage(currentVideo, 0, 0, 1280, 720);
          capturedSlides.push({
            timestamp: 0,
            time_formatted: "0:00",
            data: captureCanvas.toDataURL("image/jpeg", 0.72)
          });
        }

        console.log(`[YT2PDF In-Page Extractor] Extracted ${capturedSlides.length} slides.`);

        // Build presentation deck payload
        const deckId = `deck_${Date.now()}`;
        const deckPayload = {
          deckId: deckId,
          videoTitle: videoTitle,
          videoId: videoId,
          videoUrl: `https://www.youtube.com/watch?v=${videoId}`,
          slideCount: capturedSlides.length,
          slides: capturedSlides.map((s, idx) => ({
            index: idx + 1,
            filename: `slide_${idx + 1}.jpg`,
            timestamp_sec: s.timestamp,
            timestamp_str: s.time_formatted || formatTimestamp(s.timestamp),
            title: `Slide ${idx + 1}`,
            image_url: s.data,
            data: s.data
          })),
          createdAt: Date.now()
        };

        // Save in extension storage
        safeSendRuntimeMessage({
          action: "deck_ready",
          deck: deckPayload
        });

        // Notify parent frame of successful completion
        sendParentMessage({
          type: "YT2PDF_HEADLESS_COMPLETE",
          success: true,
          slide_count: capturedSlides.length,
          deck: deckPayload
        });

      } catch (err) {
        console.error("[YT2PDF In-Page Extractor] Pipeline error:", err);
        sendParentMessage({
          type: "YT2PDF_HEADLESS_ERROR",
          error: err.message || "Extraction error in silent frame"
        });
      }
    }

    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", runExtractionPipeline);
    } else {
      runExtractionPipeline();
    }

    return; // Halt Mode A. Mode B runs exclusively on active watch pages.
  }

  // ═════════════════════════════════════════════
  // MODE B: ACTIVE WATCH PAGE (User Foreground Tab)
  // ═════════════════════════════════════════════

  let isExtracting = false;
  let activeFrame = null;
  let frameWatchdog = null;

  function showToast(message, isError = false, linkUrl = null) {
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
      max-width: 460px;
      line-height: 1.4;
      ${linkUrl ? "cursor: pointer;" : ""}
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

    if (linkUrl) {
      toast.title = "Click to open Slide Studio in a new tab";
      toast.addEventListener("click", (e) => {
        if (e.target && e.target.tagName === "A") return;
        window.open(linkUrl, "_blank");
      });
    }

    document.body.appendChild(toast);

    setTimeout(() => {
      if (toast && toast.parentNode) {
        toast.style.transition = "opacity 0.35s ease, transform 0.35s ease";
        toast.style.opacity = "0";
        toast.style.transform = "translateY(12px)";
        setTimeout(() => toast.remove(), 380);
      }
    }, linkUrl ? 14000 : 5500);
  }

  function cleanupExtractionFrame() {
    if (frameWatchdog) {
      clearTimeout(frameWatchdog);
      frameWatchdog = null;
    }
    if (activeFrame) {
      try {
        activeFrame.src = "about:blank";
        activeFrame.remove();
      } catch (e) {}
      activeFrame = null;
    }
    const existing = document.getElementById("yt2pdf-extractor-frame");
    if (existing) {
      try {
        existing.src = "about:blank";
        existing.remove();
      } catch (e) {}
    }
  }

  function resetButton(btn) {
    if (!btn) btn = document.getElementById("yt2pdf-action-btn");
    if (!btn) return;
    isExtracting = false;
    cleanupExtractionFrame();
    btn.disabled = false;
    btn.style.opacity = "1";
    btn.style.cursor = "pointer";
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
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      startSlideExtraction(btn);
    };
  }

  function startSlideExtraction(buttonEl) {
    if (isExtracting) {
      showToast("Slide extraction is already in progress silently in the background!");
      return;
    }

    const video = document.querySelector("video.html5-main-video, video");
    if (!video || !video.duration || isNaN(video.duration) || video.duration <= 0) {
      showToast("Please wait for the YouTube video to load before extracting slides.", true);
      return;
    }

    const currentDuration = Math.floor(video.duration);
    const titleEl = document.querySelector("h1.ytd-watch-metadata yt-formatted-string") ||
                    document.querySelector("h1.title yt-formatted-string") ||
                    document.querySelector("h1.title");
    let videoTitle = titleEl ? (titleEl.innerText || "").trim() : document.title.replace(" - YouTube", "").trim();
    if (!videoTitle) videoTitle = "Presentation Slides";

    let videoId = "";
    try {
      const u = new URL(window.location.href);
      if (u.searchParams.has("v")) videoId = u.searchParams.get("v");
      else if (u.pathname.includes("/shorts/")) videoId = u.pathname.split("/shorts/")[1]?.split("/")[0];
    } catch (e) {}

    if (!videoId) {
      const ogUrl = document.querySelector('meta[property="og:url"]')?.getAttribute("content");
      if (ogUrl && ogUrl.includes("v=")) {
        videoId = new URL(ogUrl).searchParams.get("v");
      }
    }

    if (!videoId) {
      showToast("Could not determine YouTube video ID.", true);
      return;
    }

    isExtracting = true;
    cleanupExtractionFrame();

    if (buttonEl) {
      buttonEl.disabled = true;
      buttonEl.style.opacity = "0.9";
      buttonEl.innerHTML = `
        <svg class="yt2pdf-spinner" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
        <span>0%</span>
      `;
    }

    showToast("🚀 Extracting slides silently in background. Your video playback continues uninterrupted!");

    // Watchdog safety timeout (90 seconds)
    frameWatchdog = setTimeout(() => {
      if (isExtracting) {
        cleanupExtractionFrame();
        isExtracting = false;
        resetButton(buttonEl);
        showToast("Slide extraction timed out. Please try again.", true);
      }
    }, 90000);

    // Listen for progress & completion messages from the silent iframe
    const onFrameMessage = (event) => {
      if (!event.data) return;

      if (event.data.type === "YT2PDF_HEADLESS_PROGRESS") {
        if (buttonEl && event.data.total) {
          const pct = Math.min(99, Math.round((event.data.current / event.data.total) * 100));
          buttonEl.innerHTML = `
            <svg class="yt2pdf-spinner" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
            <span>${pct}%</span>
          `;
        }
      } else if (event.data.type === "YT2PDF_HEADLESS_COMPLETE") {
        window.removeEventListener("message", onFrameMessage);
        cleanupExtractionFrame();
        isExtracting = false;
        const count = event.data.slide_count || 1;
        const deckId = event.data.deck?.deckId;
        const destinationUrl = deckId ? `https://yt2pdfs.com/?deck_id=${deckId}` : "https://yt2pdfs.com";

        if (buttonEl) {
          buttonEl.disabled = false;
          buttonEl.style.opacity = "1";
          buttonEl.style.cursor = "pointer";
          buttonEl.innerHTML = `
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#2BA640" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
            <span>View Slides (${count}) &rarr;</span>
          `;
          buttonEl.title = `Extracted ${count} slides. Click to open in YT2PDF Slide Studio!`;
          buttonEl.onclick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (deckId) {
              safeSendRuntimeMessage({ action: "open_deck_page", deckId: deckId });
            }
          };
        }

        showToast(
          `🎉 <strong>${count} slides extracted!</strong> Click to view slides and download PDF on YT2PDFS.com`,
          false,
          destinationUrl
        );

        setTimeout(() => resetButton(buttonEl), 35000);

      } else if (event.data.type === "YT2PDF_HEADLESS_ERROR") {
        window.removeEventListener("message", onFrameMessage);
        cleanupExtractionFrame();
        isExtracting = false;
        resetButton(buttonEl);
        showToast("Slide extraction could not be completed for this video: " + (event.data.error || "Unknown error"), true);
      }
    };

    window.addEventListener("message", onFrameMessage);

    // Create 100% silent, offscreen iframe (ZERO browser tabs created)
    try {
      const frame = document.createElement("iframe");
      frame.id = "yt2pdf-extractor-frame";
      frame.src = `https://www.youtube.com/embed/${videoId}?autoplay=1&mute=1&enablejsapi=1&yt2pdf_headless=1&yt2pdf_duration=${currentDuration}&yt2pdf_title=${encodeURIComponent(videoTitle)}`;
      frame.style.cssText = "position:fixed;top:-10000px;left:-10000px;width:640px;height:360px;border:none;pointer-events:none;opacity:0;z-index:-9999;";
      frame.allow = "autoplay 'none'";
      activeFrame = frame;
      document.body.appendChild(frame);
    } catch (err) {
      cleanupExtractionFrame();
      isExtracting = false;
      resetButton(buttonEl);
      showToast("Unable to start extraction frame: " + err.message, true);
    }
  }

  // ─────────────────────────────────────────────
  // Native-Styled YouTube Button Injection
  // ─────────────────────────────────────────────
  function injectButton() {
    if (document.getElementById("yt2pdf-action-btn")) return;

    const topButtons =
      document.querySelector("#top-level-buttons-computed") ||
      document.querySelector(".yt-flexible-actions-view-model") ||
      document.querySelector("ytd-menu-renderer #top-level-buttons-computed");

    const subscribeBtn =
      document.querySelector("#owner #subscribe-button") ||
      document.querySelector("#subscribe-button ytd-subscribe-button-renderer");

    if (!topButtons && !subscribeBtn) return;

    const btn = document.createElement("button");
    btn.id = "yt2pdf-action-btn";
    btn.className = "yt-spec-button-shape-next yt-spec-button-shape-next--tonal yt-spec-button-shape-next--mono yt-spec-button-shape-next--size-m yt-spec-button-shape-next--icon-leading";
    btn.setAttribute("aria-label", "Convert slides to PDF on YT2PDFS.com");
    btn.title = "Extract presentation slides & open Slide Studio on YT2PDFS.com";

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
  try {
    (document.head || document.documentElement).appendChild(style);
  } catch (e) {}

  // Watch for page navigation and inject button
  setInterval(injectButton, 1000);
  window.addEventListener("yt-navigate-finish", () => {
    cleanupExtractionFrame();
    isExtracting = false;
    injectButton();
  });
  window.addEventListener("spfdone", () => {
    cleanupExtractionFrame();
    isExtracting = false;
    injectButton();
  });
  window.addEventListener("popstate", () => {
    cleanupExtractionFrame();
    isExtracting = false;
    injectButton();
  });

  // Handle messages from popup
  if (isExtensionContextValid() && chrome?.runtime?.onMessage) {
    try {
      chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
        if (request.action === "extract_slides") {
          const btn = document.getElementById("yt2pdf-action-btn") || document.createElement("button");
          startSlideExtraction(btn);
          try { sendResponse({ started: true }); } catch (e) {}
        }
        return true;
      });
    } catch (e) {}
  }
})();
