/**
 * content.js — YT2PDF Slide Companion
 * Injects a native-styled "PDF Slides" button directly into YouTube's action bar
 * and captures clean, high-resolution presentation slides directly from the browser's active player.
 * 
 * 100% In-Page Execution:
 * - ZERO hidden iframes / background streams
 * - ZERO audio leakage
 * - ZERO 0% freezing / offscreen throttling
 * - Direct redirection to YT2PDF Slide Studio for curation and PDF download
 */

(function () {
  let isExtracting = false;

  // ─────────────────────────────────────────────
  // Immediate Ghost Frame & Zombie Stream Killer
  // ─────────────────────────────────────────────
  function killGhostFrames() {
    try {
      const ghostFrames = document.querySelectorAll(
        "iframe#yt2pdf-headless-frame, iframe#yt2pdf-silent-extractor, iframe[src*='yt2pdf_headless']"
      );
      ghostFrames.forEach(f => {
        try {
          f.src = "about:blank";
          f.remove();
        } catch (e) {}
      });

      // Mute and pause any media elements that are not YouTube's main video player
      const mainVid = document.querySelector("video.html5-main-video");
      document.querySelectorAll("video, audio").forEach(el => {
        if (el !== mainVid) {
          try {
            el.muted = true;
            el.volume = 0;
            el.pause();
          } catch(e) {}
        }
      });
    } catch (e) {}
  }

  // Terminate any lingering ghost streams immediately on load
  killGhostFrames();

  // Never run extraction inside sub-frames or iframes
  if (window.self !== window.top) {
    return;
  }

  // ─────────────────────────────────────────────
  // Toast Notification
  // ─────────────────────────────────────────────
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
      toast.title = "Click to open YT2PDF Slide Studio in a new tab";
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
    }, linkUrl ? 12000 : 5500);
  }

  // ─────────────────────────────────────────────
  // Perceptual Slide Difference & Clarity Scoring
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

    // Minor jitter or identical background
    const isSameSlide = meanDiff < 0.020 && changedRatio < 0.035;
    // New distinct slide if mean difference >= 0.024 OR >= 4.0% of canvas changed
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

  // ─────────────────────────────────────────────
  // Extension Messaging Helpers
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
  // Native Action Button Reset
  // ─────────────────────────────────────────────
  function resetButton(btn) {
    if (!btn) btn = document.getElementById("yt2pdf-action-btn");
    if (!btn) return;
    isExtracting = false;
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

  // ─────────────────────────────────────────────
  // 100% In-Page Direct Slide Extraction (Zero Ghost Streams)
  // ─────────────────────────────────────────────
  async function startSlideExtraction(buttonEl) {
    if (isExtracting) {
      showToast("Slide extraction is already in progress!");
      return;
    }

    const video = document.querySelector("video.html5-main-video, video");
    if (!video || !video.duration || isNaN(video.duration) || video.duration <= 0) {
      showToast("Please wait for the YouTube video to load before extracting slides.", true);
      return;
    }

    isExtracting = true;
    killGhostFrames(); // Guarantee no ghost frames exist

    // 1. Preserve original playback state
    const originalTime = video.currentTime;
    const wasPaused = video.paused;
    const wasMuted = video.muted;
    const originalPlaybackRate = video.playbackRate;

    // 2. Pause and mute immediately to prevent any audio glitch during seek loop
    try {
      video.pause();
      video.muted = true;
    } catch (e) {}

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
    } catch(e) {}
    if (!videoId) {
      const ogUrl = document.querySelector('meta[property="og:url"]')?.getAttribute("content");
      if (ogUrl && ogUrl.includes("v=")) {
        videoId = new URL(ogUrl).searchParams.get("v");
      }
    }

    if (buttonEl) {
      buttonEl.disabled = true;
      buttonEl.style.opacity = "0.9";
      buttonEl.innerHTML = `
        <svg class="yt2pdf-spinner" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
        <span>0%</span>
      `;
    }

    showToast("🚀 Scanning presentation slides directly in high resolution...");

    // 3. Smart adaptive sampling: high accuracy, zero slide misses
    let targetSamples = 40;
    if (currentDuration < 180) targetSamples = Math.max(12, Math.floor(currentDuration / 8));
    else if (currentDuration < 600) targetSamples = 25;
    else if (currentDuration < 1800) targetSamples = 35;
    else if (currentDuration < 3600) targetSamples = 45;
    else targetSamples = 55;

    let step = Math.max(6, Math.floor(currentDuration / targetSamples));
    const samplePoints = [];
    const startT = Math.max(2, Math.floor(currentDuration * 0.005));
    for (let t = startT; t < currentDuration - 2; t += step) {
      samplePoints.push(t);
    }
    if (currentDuration > 20 && (!samplePoints.length || samplePoints[samplePoints.length - 1] < currentDuration - 15)) {
      samplePoints.push(Math.max(2, currentDuration - 10));
    }

    const captureCanvas = document.createElement("canvas");
    captureCanvas.width = 1280;
    captureCanvas.height = 720;
    const captureCtx = captureCanvas.getContext("2d");

    const thumbCanvas = document.createElement("canvas");
    thumbCanvas.width = 64;
    thumbCanvas.height = 36;
    const thumbCtx = thumbCanvas.getContext("2d");

    const lastCapturedCanvas = document.createElement("canvas");
    lastCapturedCanvas.width = 64;
    lastCapturedCanvas.height = 36;
    const lastCapturedCtx = lastCapturedCanvas.getContext("2d");

    const capturedSlides = [];

    // Hardware-accelerated frame seeking with watchdog
    async function seekFrame(targetTime) {
      await new Promise((resolve) => {
        let settled = false;
        const finish = () => {
          if (!settled) {
            settled = true;
            resolve();
          }
        };
        const timer = setTimeout(finish, 320); // 320ms safety watchdog

        const onSeeked = () => {
          video.removeEventListener("seeked", onSeeked);
          clearTimeout(timer);
          finish();
        };
        video.addEventListener("seeked", onSeeked, { once: true });

        if (typeof video.requestVideoFrameCallback === "function") {
          video.requestVideoFrameCallback(() => {
            video.removeEventListener("seeked", onSeeked);
            clearTimeout(timer);
            finish();
          });
        }

        try {
          video.currentTime = targetTime;
        } catch(e) {
          finish();
        }
      });
    }

    try {
      for (let i = 0; i < samplePoints.length; i++) {
        const t = samplePoints[i];
        const pct = Math.min(99, Math.round(((i + 1) / samplePoints.length) * 100));

        if (buttonEl) {
          buttonEl.innerHTML = `
            <svg class="yt2pdf-spinner" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
            <span>${pct}%</span>
          `;
        }

        await seekFrame(t);

        thumbCtx.drawImage(video, 0, 0, 64, 36);

        if (capturedSlides.length === 0) {
          lastCapturedCtx.drawImage(thumbCanvas, 0, 0, 64, 36);
          captureCtx.drawImage(video, 0, 0, 1280, 720);
          capturedSlides.push({
            timestamp: t,
            time_formatted: formatTimestamp(t),
            data: captureCanvas.toDataURL("image/jpeg", 0.72)
          });
        } else {
          const diffResult = calculateDifference(thumbCanvas, lastCapturedCanvas);
          if (diffResult.isDistinct) {
            lastCapturedCtx.drawImage(thumbCanvas, 0, 0, 64, 36);
            captureCtx.drawImage(video, 0, 0, 1280, 720);
            capturedSlides.push({
              timestamp: t,
              time_formatted: formatTimestamp(t),
              data: captureCanvas.toDataURL("image/jpeg", 0.72)
            });
          } else if (diffResult.isSameSlide && diffResult.clarityA > diffResult.clarityB * 1.05) {
            // Instructor movement or slide uncovered: update with clearer frame
            lastCapturedCtx.drawImage(thumbCanvas, 0, 0, 64, 36);
            captureCtx.drawImage(video, 0, 0, 1280, 720);
            capturedSlides[capturedSlides.length - 1] = {
              timestamp: t,
              time_formatted: formatTimestamp(t),
              data: captureCanvas.toDataURL("image/jpeg", 0.72)
            };
          }
        }
      }

      // Guarantee at least 1 slide
      if (capturedSlides.length === 0) {
        captureCtx.drawImage(video, 0, 0, 1280, 720);
        capturedSlides.push({
          timestamp: 0,
          time_formatted: "0:00",
          data: captureCanvas.toDataURL("image/jpeg", 0.72)
        });
      }

      // 4. Restore original playback position and state immediately
      try {
        video.currentTime = originalTime;
        video.playbackRate = originalPlaybackRate;
        video.muted = wasMuted;
        if (!wasPaused) {
          video.play().catch(() => {});
        }
      } catch (e) {}

      // 5. Package presentation deck
      const deckId = `deck_${Date.now()}`;
      const deckPayload = {
        deckId: deckId,
        videoTitle: videoTitle,
        videoId: videoId,
        videoUrl: window.location.href,
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

      // 6. Save in extension local storage
      safeSendRuntimeMessage({
        action: "deck_ready",
        deck: deckPayload
      });

      // 7. Update button state
      if (buttonEl) {
        buttonEl.disabled = false;
        buttonEl.style.opacity = "1";
        buttonEl.style.cursor = "pointer";
        buttonEl.innerHTML = `
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#2BA640" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
          <span>View Slides (${capturedSlides.length}) &rarr;</span>
        `;
        buttonEl.title = `Extracted ${capturedSlides.length} slides. Click to open in YT2PDF Slide Studio!`;
        buttonEl.onclick = (e) => {
          e.preventDefault();
          e.stopPropagation();
          safeSendRuntimeMessage({ action: "open_deck_page", deckId: deckId });
        };
      }

      showToast(`🎉 <strong>${capturedSlides.length} slides extracted!</strong> Opening YT2PDF Slide Studio to view and download PDF...`);

      // 8. Direct user to website Slide Studio
      safeSendRuntimeMessage({
        action: "open_deck_page",
        deckId: deckId
      });

      setTimeout(() => resetButton(buttonEl), 25000);

    } catch (err) {
      console.error("[YT2PDF Companion] In-page extraction error:", err);
      try {
        video.currentTime = originalTime;
        video.playbackRate = originalPlaybackRate;
        video.muted = wasMuted;
        if (!wasPaused) video.play().catch(() => {});
      } catch (e) {}
      showToast("Slide extraction failed: " + (err.message || "Unknown error"), true);
      resetButton(buttonEl);
    } finally {
      isExtracting = false;
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
  document.head.appendChild(style);

  // Watch for page navigation and inject button
  setInterval(injectButton, 1000);
  window.addEventListener("yt-navigate-finish", () => {
    killGhostFrames();
    isExtracting = false;
    injectButton();
  });
  window.addEventListener("spfdone", () => {
    killGhostFrames();
    isExtracting = false;
    injectButton();
  });
  window.addEventListener("popstate", () => {
    killGhostFrames();
    isExtracting = false;
    injectButton();
  });

  // Handle messages from popup or background worker
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
