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
  // Perceptual slide difference with instructor movement separation & visibility scoring
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
    let identicalPixels = 0;
    const numPixels = w * h;
    const totalBytes = numPixels * 4;

    for (let i = 0; i < totalBytes; i += 4) {
      const lumA = 0.299 * dataA[i] + 0.587 * dataA[i + 1] + 0.114 * dataA[i + 2];
      const lumB = 0.299 * dataB[i] + 0.587 * dataB[i + 1] + 0.114 * dataB[i + 2];
      const d = Math.abs(lumA - lumB);
      totalDiff += d;
      if (d < 18) {
        identicalPixels++;
      }
    }

    const meanDiff = totalDiff / (numPixels * 255);
    const identicalRatio = identicalPixels / numPixels;

    const clarityA = computeClarity(canvasA);
    const clarityB = computeClarity(canvasB);

    // If >= 78% of the slide pixels match, it's the SAME slide (instructor moved/gestured)
    const isSameSlide = identicalRatio >= 0.78 && meanDiff < 0.11;
    // New slide if substantial change across the canvas
    const isNewSlide = !isSameSlide && (meanDiff >= 0.045 || identicalRatio < 0.72);

    return {
      meanDiff,
      identicalRatio,
      isSameSlide,
      isNewSlide,
      isDistinct: isNewSlide,
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

      // Strategy 3: If inside an embed or script tags had no captionTracks, fetch watch page directly
      if (!captionTracks || captionTracks.length === 0) {
        let vId = videoId;
        if (!vId) {
          try {
            const u = new URL(window.location.href);
            vId = u.searchParams.get("v") || (u.pathname.includes("/embed/") ? u.pathname.split("/embed/")[1]?.split("?")[0] : "");
          } catch(e) {}
        }
        if (vId) {
          try {
            console.log(`[YT2PDF Companion] Fetching watch page HTML directly for video ${vId}...`);
            const watchRes = await fetch(`https://www.youtube.com/watch?v=${vId}`);
            if (watchRes.ok) {
              const html = await watchRes.text();
              const match = html.match(/"captionTracks":\s*(\[.+?\])/);
              if (match) {
                const parsed = JSON.parse(match[1]);
                if (Array.isArray(parsed) && parsed.length > 0) {
                  captionTracks = parsed;
                  console.log(`[YT2PDF Companion] Strategy 3 successfully recovered ${captionTracks.length} caption tracks from watch page.`);
                }
              }
            }
          } catch (watchErr) {
            console.warn("[YT2PDF Companion] Watch page fetch error:", watchErr);
          }
        }
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
  // Client-Side YouTube Audio Extraction
  // ─────────────────────────────────────────────
  async function extractAudioTrackFromPage() {
    try {
      console.log("[YT2PDF Companion] Detecting audio stream from YouTube session...");
      const audioResult = await new Promise((resolve) => {
        const handler = (e) => {
          window.removeEventListener("yt2pdf_audio_reply", handler);
          resolve(e.detail || null);
        };
        window.addEventListener("yt2pdf_audio_reply", handler);
        setTimeout(() => {
          window.removeEventListener("yt2pdf_audio_reply", handler);
          resolve(null);
        }, 1000);

        const s = document.createElement("script");
        s.textContent = `
          (() => {
            try {
              let audioUrl = null;
              let mimeType = "audio/mp4";
              let bitrate = 0;
              const player = document.getElementById("movie_player");
              let sData = player?.getStreamingData ? player.getStreamingData() : null;
              if (!sData && window.ytInitialPlayerResponse?.streamingData) {
                sData = window.ytInitialPlayerResponse.streamingData;
              }
              if (sData?.adaptiveFormats) {
                const audioFormats = sData.adaptiveFormats.filter(f => (f.mimeType || "").startsWith("audio/"));
                if (audioFormats.length > 0) {
                  const withUrl = audioFormats.filter(f => f.url);
                  if (withUrl.length > 0) {
                    withUrl.sort((a, b) => (a.bitrate || 0) - (b.bitrate || 0));
                    audioUrl = withUrl[0].url;
                    mimeType = withUrl[0].mimeType ? withUrl[0].mimeType.split(";")[0] : "audio/mp4";
                    bitrate = withUrl[0].bitrate || 0;
                  }
                }
              }
              window.dispatchEvent(new CustomEvent("yt2pdf_audio_reply", {
                detail: audioUrl ? { audio_url: audioUrl, mime_type: mimeType, bitrate } : null
              }));
            } catch(e) {
              window.dispatchEvent(new CustomEvent("yt2pdf_audio_reply", { detail: null }));
            }
          })();
        `;
        (document.head || document.documentElement).appendChild(s);
        s.remove();
      });

      if (audioResult && audioResult.audio_url) {
        console.log(`[YT2PDF Companion] Audio stream found (${audioResult.mime_type}, ${audioResult.bitrate} bps).`);
        return audioResult;
      }
    } catch (err) {
      console.warn("[YT2PDF Companion] Audio track extraction notice:", err);
    }
    return null;
  }

  async function uploadAudioInBackground(jobId, audioUrl, backendBase) {
    if (!jobId || !audioUrl) return;
    try {
      console.log(`[YT2PDF Companion] Starting background audio stream upload for job ${jobId}...`);
      const audioResp = await fetch(audioUrl);
      if (!audioResp.ok) {
        console.warn(`[YT2PDF Companion] Could not fetch audio stream: ${audioResp.statusText}`);
        return;
      }
      const blob = await audioResp.blob();
      console.log(`[YT2PDF Companion] Audio stream fetched (${Math.round(blob.size / 1024)} KB). Uploading to backend...`);
      const formData = new FormData();
      formData.append("file", blob, "lecture_audio.mp4");

      const candidateBases = [
        backendBase || "https://yt2pdfs.com",
        "https://yt2pdfs.com",
        "https://yt2pdf-214301889618.europe-west1.run.app",
        "http://localhost:8080"
      ];
      for (const base of candidateBases) {
        try {
          const res = await fetch(`${base}/api/companion/upload-audio?job_id=${jobId}`, {
            method: "POST",
            body: formData,
          });
          if (res.ok) {
            console.log(`[YT2PDF Companion] Audio uploaded successfully to ${base} for job ${jobId}`);
            break;
          }
        } catch (postErr) {
          console.debug(`[YT2PDF Companion] Audio upload failed on ${base}:`, postErr.message);
        }
      }
    } catch (e) {
      console.warn("[YT2PDF Companion] Background audio upload notice:", e);
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
    const candidateBases = [];
    if (window.location.origin && (window.location.origin.includes("localhost") || window.location.origin.includes("127.0.0.1") || window.location.origin.includes("yt2pdfs.com"))) {
      candidateBases.push(window.location.origin);
    }
    const defaultBases = [
      "https://yt2pdfs.com",
      "https://yt2pdf-214301889618.europe-west1.run.app",
      "http://localhost:8080",
      "http://localhost:8000"
    ];
    for (const b of defaultBases) {
      if (!candidateBases.includes(b)) candidateBases.push(b);
    }

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
    const frameCount = payload.frames?.length || 0;
    // For large payloads (> 10 frames), bypass Chrome MV3 message port limits and upload directly
    if (frameCount > 10) {
      console.log(`[YT2PDF Companion] Uploading ${frameCount} slides directly to server...`);
      return await directUploadFrames(payload);
    }

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
    }

    // 2. Direct web upload fallback
    return await directUploadFrames(payload);
  }

  // ─────────────────────────────────────────────
  // High-Speed Silent Background Extraction Mode
  // ─────────────────────────────────────────────
  const isHeadless = new URLSearchParams(window.location.search).get("yt2pdf_headless") === "1";

  function parseISO8601(durationStr) {
    if (!durationStr) return 0;
    const match = durationStr.match(/PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/);
    if (!match) return 0;
    const hours = parseInt(match[1] || 0, 10);
    const minutes = parseInt(match[2] || 0, 10);
    const seconds = parseInt(match[3] || 0, 10);
    return hours * 3600 + minutes * 60 + seconds;
  }

  async function runHeadlessExtraction() {
    console.log("[YT2PDF Companion] Initializing silent background extraction...");

    // 0. Spoof document visibility to keep YouTube player actively decoding
    try {
      Object.defineProperty(document, "hidden", { get: () => false, configurable: true });
      Object.defineProperty(document, "visibilityState", { get: () => "visible", configurable: true });
      Object.defineProperty(document, "webkitVisibilityState", { get: () => "visible", configurable: true });
      window.dispatchEvent(new Event("visibilitychange"));
    } catch (e) {}

    // 1. Dedicated inline Web Worker with guaranteed timeout fallback
    let unthrottledSleep = (ms) => new Promise(r => setTimeout(r, ms));
    try {
      let workerActive = false;
      const workerBlob = new Blob([
        "self.onmessage = function(e) { setTimeout(function() { self.postMessage(e.data); }, e.data.ms); };"
      ], { type: "application/javascript" });
      const timerWorker = new Worker(URL.createObjectURL(workerBlob));
      timerWorker.onerror = () => {
        workerActive = false;
      };
      workerActive = true;
      unthrottledSleep = function(ms) {
        return new Promise((resolve) => {
          let settled = false;
          const finish = () => {
            if (!settled) {
              settled = true;
              resolve();
            }
          };
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
            try {
              timerWorker.postMessage({ id, ms });
            } catch (e) {
              finish();
            }
          }
        });
      };
    } catch(e) {
      console.warn("[YT2PDF Companion] Web Worker timer fallback:", e);
    }

    // 3. Enforce 100% complete silence: permanently mute and zero-volume all audio/video elements
    const silenceMediaElement = (el) => {
      try {
        el.muted = true;
        el.volume = 0;
        el.defaultMuted = true;
      } catch (e) {}
    };

    try {
      document.querySelectorAll("video, audio").forEach(silenceMediaElement);
      const silenceObserver = new MutationObserver(() => {
        document.querySelectorAll("video, audio").forEach(silenceMediaElement);
      });
      silenceObserver.observe(document.documentElement || document.body, { childList: true, subtree: true });
    } catch (e) {}

    // Low-Data Mode: Enforce 720p HD maximum playback quality
    // This slashes video streaming bandwidth by 65-80% compared to 1080p/4K,
    // while keeping slides 100% sharp and readable at 1280x720.
    function enforceEfficientStreaming() {
      try {
        const player = document.getElementById("movie_player") || document.querySelector(".html5-video-player");
        if (player) {
          if (typeof player.setPlaybackQualityRange === "function") {
            player.setPlaybackQualityRange("small", "hd720");
          }
          if (typeof player.setPlaybackQuality === "function") {
            player.setPlaybackQuality("hd720");
          }
        }
      } catch (e) {}
    }

    try {
      localStorage.setItem("yt-player-quality", JSON.stringify({
        data: "hd720",
        expiration: Date.now() + 86400000,
        creation: Date.now()
      }));
    } catch (e) {}

    function dismissOverlaysAndSkipAds(videoEl) {
      try {
        enforceEfficientStreaming();

        // 1. Fast-forward ad video
        const adShowing = document.querySelector(".ad-showing, .ad-interrupting, .ytp-ad-player-overlay");
        if (adShowing && videoEl) {
          if (videoEl.duration && !isNaN(videoEl.duration) && isFinite(videoEl.duration)) {
            videoEl.currentTime = videoEl.duration;
          }
        }
        // 2. Click skip buttons
        const skipBtns = document.querySelectorAll(
          ".ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button-container button"
        );
        skipBtns.forEach(btn => { try { btn.click(); } catch(e) {} });

        // 3. Close banner overlays
        const bannerBtns = document.querySelectorAll(".ytp-ad-overlay-close-button, .ytp-ad-overlay-close-container");
        bannerBtns.forEach(btn => { try { btn.click(); } catch(e) {} });

        // 4. Confirm / "still watching" dialogs
        const consentBtn = document.querySelector(
          "ytd-button-renderer#confirm-button button, .yt-confirm-dialog-renderer #confirm-button button, button[aria-label*='Accept all'], button[aria-label*='Agree']"
        );
        if (consentBtn) {
          try { consentBtn.click(); } catch(e) {}
        }

        // 5. Large play button if paused
        const playBtn = document.querySelector(".ytp-large-play-button, .ytp-play-button");
        if (playBtn && videoEl?.paused) {
          try { playBtn.click(); } catch(e) {}
        }
      } catch (e) {}
    }

    let video = null;
    let resolvedDuration = 0;
    const waitStart = Date.now();

    // 3. Actively wake up YouTube player & resolve duration
    while (Date.now() - waitStart < 15000) {
      video = document.querySelector("video.html5-main-video, video");
      dismissOverlaysAndSkipAds(video);

      // Check for YouTube fatal error (e.g. video private/deleted/embedding disabled)
      const errorScreen = document.querySelector(".ytp-error");
      if (errorScreen && errorScreen.offsetParent !== null) {
        const errReason = errorScreen.querySelector(".ytp-error-content-reason")?.textContent?.trim() || "Video unavailable";
        safeSendRuntimeMessage({
          action: "headless_extraction_failed",
          error: `YouTube error: ${errReason}`
        });
        if (window.parent && window.parent !== window) {
          try {
            window.parent.postMessage({
              type: "YT2PDF_HEADLESS_ERROR",
              error: `YouTube error: ${errReason}`
            }, "*");
          } catch(e) {}
        }
        return;
      }

      // 0. Explicit duration passed via URL parameters
      if (!resolvedDuration) {
        const pDur = new URLSearchParams(window.location.search).get("yt2pdf_duration");
        if (pDur && !isNaN(parseInt(pDur, 10)) && parseInt(pDur, 10) > 0) {
          resolvedDuration = parseInt(pDur, 10);
        }
      }

      // 1. Video element duration & kickstart muted playback
      if (video) {
        video.muted = true;
        video.volume = 0;
        video.play().catch(() => {});
        if (!resolvedDuration && video.duration && !isNaN(video.duration) && video.duration > 0) {
          resolvedDuration = Math.floor(video.duration);
        }
      }

      // 2. Player UI time duration (.ytp-time-duration e.g. "12:34")
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

      // 3. Progress bar aria-valuemax
      if (!resolvedDuration) {
        const pBar = document.querySelector(".ytp-progress-bar");
        if (pBar) {
          const maxVal = parseFloat(pBar.getAttribute("aria-valuemax") || "0");
          if (maxVal > 0) resolvedDuration = Math.floor(maxVal);
        }
      }

      // 4. og:video:duration (exact integer seconds)
      if (!resolvedDuration) {
        const ogDur = document.querySelector('meta[property="og:video:duration"]')?.getAttribute("content");
        if (ogDur && !isNaN(parseInt(ogDur, 10)) && parseInt(ogDur, 10) > 0) {
          resolvedDuration = parseInt(ogDur, 10);
        }
      }

      // 5. meta itemprop="duration" (ISO 8601 string)
      if (!resolvedDuration) {
        const metaDur = document.querySelector('meta[itemprop="duration"]')?.getAttribute("content");
        if (metaDur) resolvedDuration = parseISO8601(metaDur);
      }

      // 6. HTML script tags with lengthSeconds
      if (!resolvedDuration) {
        for (const s of document.querySelectorAll("script")) {
          if (s.textContent && s.textContent.includes("lengthSeconds")) {
            const m = s.textContent.match(/["']lengthSeconds["']\s*:\s*["']?(\d+)["']?/);
            if (m && m[1]) {
              const sec = parseInt(m[1], 10);
              if (sec > 0) { resolvedDuration = sec; break; }
            }
          }
        }
      }

      if (video && resolvedDuration > 0 && (video.readyState >= 1 || video.duration > 0)) {
        break;
      }
      await unthrottledSleep(200);
    }

    if (!video || !resolvedDuration) {
      console.warn("[YT2PDF Companion] Video element or duration not ready after 15s.");
      safeSendRuntimeMessage({
        action: "headless_extraction_failed",
        error: "Unable to load YouTube video stream in silent background tab."
      });
      if (window.parent && window.parent !== window) {
        try {
          window.parent.postMessage({
            type: "YT2PDF_HEADLESS_ERROR",
            error: "Unable to load YouTube video stream in background."
          }, "*");
        } catch(e) {}
      }
      return;
    }

    // Keep video muted and playing so Chromium continues decoding frames on seek
    video.muted = true;
    try {
      await video.play().catch(() => {});
    } catch (e) {}

    // Wait briefly for video stream to be ready for frame extraction
    const readyStart = Date.now();
    while (Date.now() - readyStart < 4000) {
      if (video && (video.readyState >= 2 || video.videoWidth > 0)) {
        break;
      }
      await unthrottledSleep(150);
    }

    try {
      const duration = resolvedDuration;
      const titleEl = document.querySelector("h1.ytd-watch-metadata yt-formatted-string") ||
                      document.querySelector("h1.title yt-formatted-string") ||
                      document.querySelector("h1.title") ||
                      document.querySelector(".ytp-title-link") ||
                      document.querySelector(".ytp-title") ||
                      document.querySelector('meta[name="title"]');
      let videoTitle = titleEl ? (titleEl.innerText || titleEl.getAttribute("content") || "").trim() : document.title.replace(" - YouTube", "").trim();
      if (!videoTitle) {
        const urlTitle = new URLSearchParams(window.location.search).get("yt2pdf_title");
        if (urlTitle) videoTitle = decodeURIComponent(urlTitle);
      }
      if (!videoTitle) videoTitle = "Presentation Slides";

      let cleanUrl = window.location.href;
      if (window.location.pathname.includes("/embed/")) {
        const vid = window.location.pathname.split("/embed/")[1]?.split("?")[0];
        if (vid) {
          cleanUrl = `https://www.youtube.com/watch?v=${vid}`;
        }
      } else {
        cleanUrl = cleanUrl.replace(/([&?])yt2pdf_headless=1&?/, "$1").replace(/[?&]$/, "");
      }

      // Smart adaptive sampling: high accuracy, zero slide misses, minimal data usage
      let targetSamples = 65;
      if (duration < 300) targetSamples = Math.max(16, Math.floor(duration / 12));
      else if (duration < 900) targetSamples = 38;
      else if (duration < 2400) targetSamples = 58;
      else if (duration < 5400) targetSamples = 72;
      else targetSamples = 80;

      let step = Math.max(8, Math.floor(duration / targetSamples));

      const samplePoints = [];
      const startT = Math.max(2, Math.floor(duration * 0.005));
      for (let t = startT; t < duration - 2; t += step) {
        samplePoints.push(t);
      }
      // Always guarantee sampling the concluding minute of the lecture!
      if (duration > 20 && (!samplePoints.length || samplePoints[samplePoints.length - 1] < duration - 15)) {
        samplePoints.push(Math.max(2, duration - 10));
      }

      let finalPoints = samplePoints;
      if (finalPoints.length > 95) {
        const stride = finalPoints.length / 95;
        finalPoints = Array.from({ length: 95 }, (_, idx) => finalPoints[Math.min(finalPoints.length - 1, Math.floor(idx * stride))]);
      }

      const captureCanvas = document.createElement("canvas");
      captureCanvas.width = 1280;
      captureCanvas.height = 720;
      const captureCtx = captureCanvas.getContext("2d");

      // Candidate thumbnail canvas (64x36 for crisp detail detection)
      const thumbCanvas = document.createElement("canvas");
      thumbCanvas.width = 64;
      thumbCanvas.height = 36;
      const thumbCtx = thumbCanvas.getContext("2d");

      // Dedicated last-captured thumbnail canvas
      const lastCapturedCanvas = document.createElement("canvas");
      lastCapturedCanvas.width = 64;
      lastCapturedCanvas.height = 36;
      const lastCapturedCtx = lastCapturedCanvas.getContext("2d");

      const capturedSlides = [];

      async function seekToTime(targetTime) {
        const activeVideo = document.querySelector("video.html5-main-video, video") || video;
        if (!activeVideo) return;

        dismissOverlaysAndSkipAds(activeVideo);

        let onSeeked = null;
        await Promise.race([
          new Promise((resolve) => {
            onSeeked = () => resolve();
            try {
              activeVideo.addEventListener("seeked", onSeeked, { once: true });
              activeVideo.currentTime = targetTime;
            } catch(e) {
              resolve();
            }
          }),
          unthrottledSleep(280)
        ]).finally(() => {
          if (onSeeked && activeVideo) {
            try { activeVideo.removeEventListener("seeked", onSeeked); } catch(e) {}
          }
        });

        await unthrottledSleep(30);
      }

      for (let i = 0; i < finalPoints.length; i++) {
        const timeTarget = finalPoints[i];

        // Send real-time extraction progress to web page
        safeSendRuntimeMessage({
          action: "headless_progress",
          current: i + 1,
          total: finalPoints.length
        });
        if (window.parent && window.parent !== window) {
          try {
            window.parent.postMessage({
              type: "YT2PDF_HEADLESS_PROGRESS",
              current: i + 1,
              total: finalPoints.length
            }, "*");
          } catch(e) {}
        }

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
            data: captureCanvas.toDataURL("image/jpeg", 0.70)
          });
        } else {
          const diffResult = calculateDifference(thumbCanvas, lastCapturedCanvas);
          if (diffResult.isSameSlide) {
            // Instructor movement detected! Check if this frame is clearer (less obstructed)
            if (diffResult.clarityA > diffResult.clarityB * 1.05) {
              lastCapturedCtx.drawImage(thumbCanvas, 0, 0, 64, 36);
              captureCtx.drawImage(currentVideo, 0, 0, 1280, 720);
              capturedSlides[capturedSlides.length - 1] = {
                timestamp: timeTarget,
                time_formatted: formatTimestamp(timeTarget),
                data: captureCanvas.toDataURL("image/jpeg", 0.70)
              };
            }
          } else if (diffResult.isNewSlide || diffResult.isDistinct) {
            lastCapturedCtx.drawImage(thumbCanvas, 0, 0, 64, 36);
            captureCtx.drawImage(currentVideo, 0, 0, 1280, 720);
            capturedSlides.push({
              timestamp: timeTarget,
              time_formatted: formatTimestamp(timeTarget),
              data: captureCanvas.toDataURL("image/jpeg", 0.70)
            });
          }
        }
      }

      // Timeline Gap & End-of-Lecture Safety Net:
      // Ensure no slides are skipped in long gaps (> 90s) or missed at the end of the video
      const gaps = [];
      const gapThreshold = Math.max(90, Math.floor(step * 2.5));
      if (capturedSlides.length > 0 && duration > 60) {
        for (let idx = 0; idx < capturedSlides.length - 1; idx++) {
          const tA = capturedSlides[idx].timestamp;
          const tB = capturedSlides[idx + 1].timestamp;
          if (tB - tA > gapThreshold) {
            gaps.push(Math.floor((tA + tB) / 2));
          }
        }
        const lastTs = capturedSlides[capturedSlides.length - 1].timestamp;
        if (lastTs < duration - 35) {
          console.log(`[YT2PDF Companion] End-of-lecture gap detected (last slide: ${lastTs}s, duration: ${duration}s). Sampling closing slide...`);
          gaps.push(Math.max(2, duration - 10));
        }
      }

      if (gaps.length > 0 || (capturedSlides.length < 6 && duration > 60)) {
        console.log(`[YT2PDF Companion] Filling ${gaps.length} timeline gaps across lecture...`);
        const existingTs = new Set(capturedSlides.map(s => Math.floor(s.timestamp)));
        const chkPoints = gaps.length > 0 ? gaps.slice(0, 10) : finalPoints.filter((_, idx) => idx % Math.max(1, Math.floor(finalPoints.length / 8)) === 0);
        for (const tPoint of chkPoints) {
          if (![...existingTs].some(ts => Math.abs(ts - tPoint) < 14)) {
            try {
              await seekToTime(tPoint);
              const currentVideo = document.querySelector("video.html5-main-video, video") || video;
              if (currentVideo) {
                captureCtx.drawImage(currentVideo, 0, 0, 1280, 720);
                capturedSlides.push({
                  timestamp: tPoint,
                  time_formatted: formatTimestamp(tPoint),
                  data: captureCanvas.toDataURL("image/jpeg", 0.70)
                });
                existingTs.add(Math.floor(tPoint));
              }
            } catch (e) {}
          }
        }
        capturedSlides.sort((a, b) => a.timestamp - b.timestamp);
        console.log(`[YT2PDF Companion] Total slides after timeline gap coverage: ${capturedSlides.length}`);
      }

      if (capturedSlides.length === 0) {
        const currentVideo = document.querySelector("video.html5-main-video, video") || video;
        if (currentVideo) {
          captureCtx.drawImage(currentVideo, 0, 0, 1280, 720);
        }
        capturedSlides.push({
          timestamp: 0,
          time_formatted: "0:00",
          data: captureCanvas.toDataURL("image/jpeg", 0.70)
        });
      }

      if (audioWakeup) {
        try { audioWakeup.close(); } catch(e) {}
      }

      console.log(`[YT2PDF Companion] Silent background extraction complete (${capturedSlides.length} slides). Transmitting...`);

      let transcriptSegments = [];
      try {
        transcriptSegments = await Promise.race([
          extractTranscriptFromPage(),
          unthrottledSleep(6000).then(() => [])
        ]);
      } catch (trErr) {
        console.warn("[YT2PDF Companion] Transcript extraction error:", trErr);
      }

      let audioInfo = null;
      // High-Yield Data Saver:
      // If we already have the complete spoken transcript (~30 KB), skip downloading/uploading
      // 50-100 MB of raw audio track, saving massive amounts of internet bandwidth!
      if (!transcriptSegments || transcriptSegments.length === 0) {
        try {
          console.log("[YT2PDF Companion] No transcript found. Fetching audio track fallback for AI study guide...");
          audioInfo = await extractAudioTrackFromPage();
        } catch (aErr) {
          console.warn("[YT2PDF Companion] Audio extraction notice:", aErr);
        }
      } else {
        console.log(`[YT2PDF Companion] Spoken transcript available (${transcriptSegments.length} segments). Skipping heavy audio stream download to save user data!`);
      }

      const payload = {
        video_url: cleanUrl,
        title: videoTitle,
        duration: duration,
        frames: capturedSlides,
        transcript: transcriptSegments,
        audio_url: audioInfo ? audioInfo.audio_url : null,
        audio_mime: audioInfo ? audioInfo.mime_type : "audio/mp4",
      };

      const notifyParentComplete = (jobId, base) => {
        if (window.parent && window.parent !== window) {
          try {
            window.parent.postMessage({
              type: "YT2PDF_HEADLESS_COMPLETE",
              success: true,
              job_id: jobId,
              backend_base: base,
              slide_count: capturedSlides.length
            }, "*");
          } catch(e) {}
        }
      };

      const notifyParentError = (errMsg) => {
        if (window.parent && window.parent !== window) {
          try {
            window.parent.postMessage({
              type: "YT2PDF_HEADLESS_ERROR",
              error: errMsg
            }, "*");
          } catch(e) {}
        }
      };

      const isRedirectRequested = new URLSearchParams(window.location.search).get("yt2pdf_redirect") === "1";
      payload.redirect_on_success = isRedirectRequested;

      // Broadcast progress that frame scanning is done and upload is beginning
      safeSendRuntimeMessage({
        action: "headless_progress",
        current: finalPoints.length,
        total: finalPoints.length,
        stage: "uploading"
      });

      const handleUploadSuccess = (jobId, base) => {
        if (audioInfo && audioInfo.audio_url && jobId) {
          uploadAudioInBackground(jobId, audioInfo.audio_url, base);
        }

        // 1. Notify background worker immediately so it closes the window and redirects origin tab!
        safeSendRuntimeMessage({
          action: "headless_extraction_direct_success",
          data: {
            job_id: jobId,
            backend_base: base,
            slide_count: capturedSlides.length
          }
        });

        // 2. Notify parent iframe if embedded
        notifyParentComplete(jobId, base);
      };

      const handleUploadFailure = (errMsg) => {
        console.error("[YT2PDF Companion] Upload failed:", errMsg);
        safeSendRuntimeMessage({
          action: "headless_extraction_failed",
          error: errMsg
        });
        notifyParentError(errMsg);
      };

      // Upload frames safely using high-speed direct upload
      uploadFramesSafely(payload)
        .then((resData) => {
          if (resData && (resData.job_id || resData.data?.job_id)) {
            const finalJobId = resData.job_id || resData.data?.job_id;
            const finalBase = resData.backend_base || resData.data?.backend_base || "https://yt2pdfs.com";
            handleUploadSuccess(finalJobId, finalBase);
          } else {
            handleUploadFailure("Invalid response from server after uploading slides.");
          }
        })
        .catch((uErr) => {
          handleUploadFailure(uErr.message || "Failed to upload extracted slides.");
        });

    } catch (err) {
      console.error("[YT2PDF Companion] Silent extraction error:", err);
      if (window.parent && window.parent !== window) {
        try {
          window.parent.postMessage({
            type: "YT2PDF_HEADLESS_ERROR",
            error: err.message
          }, "*");
        } catch(e) {}
      }
    }
  }

  if (isHeadless) {
    runHeadlessExtraction();
    return; // Do not inject watch-page buttons or register watch-page listeners
  }

  // ─────────────────────────────────────────────
  // Watch Page Slide Extraction (Silent Background Trigger)
  // ─────────────────────────────────────────────
  function resetButton(btn) {
    if (!btn) btn = document.getElementById("yt2pdf-action-btn");
    if (!btn) return;
    isExtracting = false;
    btn.disabled = false;
    btn.style.opacity = "1";
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
  }

  async function startSlideExtraction(buttonEl) {
    if (isExtracting) {
      showToast("Slide extraction is already running silently in the background!");
      return;
    }

    const video = document.querySelector("video.html5-main-video");
    if (!video || !video.duration || isNaN(video.duration)) {
      showToast("Please wait for video to load before extracting slides.", true);
      return;
    }

    isExtracting = true;
    if (buttonEl) {
      buttonEl.disabled = true;
      buttonEl.style.opacity = "0.9";
      buttonEl.innerHTML = `
        <svg class="yt2pdf-spinner" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
        <span>0%</span>
      `;
    }

    showToast("🚀 Extracting slides silently in background. You can keep watching your video!");

    let currentDuration = 0;
    if (video && video.duration && !isNaN(video.duration) && video.duration > 0) {
      currentDuration = Math.floor(video.duration);
    }
    const titleEl = document.querySelector("h1.ytd-watch-metadata yt-formatted-string") ||
                    document.querySelector("h1.title yt-formatted-string") ||
                    document.querySelector("h1.title");
    const videoTitle = titleEl ? (titleEl.innerText || "").trim() : document.title.replace(" - YouTube", "").trim();

    // Extract videoId from current URL
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

    if (!videoId) {
      isExtracting = false;
      showToast("Could not determine YouTube video ID.", true);
      resetButton(buttonEl);
      return;
    }

    // Trigger 100% silent background window extraction via service worker
    safeSendRuntimeMessage({
      action: "start_background_extraction",
      video_url: window.location.href,
      duration: currentDuration,
      title: videoTitle,
      redirect_on_success: true
    }, (response) => {
      if (chrome.runtime.lastError || (response && !response.success)) {
        isExtracting = false;
        const err = chrome.runtime.lastError?.message || response?.error || "Failed to start background extraction.";
        showToast("Slide extraction error: " + err, true);
        resetButton(buttonEl);
      }
    });
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

  // Message listener for popup, live background progress, and completion redirection
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

        // Live progress update from silent background extraction
        if (request.action === "extraction_progress_update") {
          const btn = document.getElementById("yt2pdf-action-btn");
          if (btn && request.total) {
            const pct = Math.min(99, Math.round((request.current / request.total) * 100));
            const label = (request.stage === "uploading" || pct >= 99) ? "Uploading..." : `${pct}%`;
            btn.innerHTML = `
              <svg class="yt2pdf-spinner" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
              <span>${label}</span>
            `;
          }
        }

        // Extraction finished: redirect to YT2PDFS!
        if (request.action === "extraction_finished") {
          const btn = document.getElementById("yt2pdf-action-btn");
          if (request.success) {
            if (btn) {
              btn.innerHTML = `
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#2BA640" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                <span>100% Done</span>
              `;
            }
            showToast("🎉 Slides extracted! Opening YT2PDFS to download your PDF...");
            if (request.data?.job_id) {
              let webBase = request.data.backend_base || "https://yt2pdfs.com";
              if (!webBase.includes("localhost") && !webBase.includes("127.0.0.1")) {
                webBase = "https://yt2pdfs.com";
              }
              const destinationUrl = `${webBase}/?job_id=${request.data.job_id}`;
              setTimeout(() => {
                window.location.href = destinationUrl;
              }, 400);
            }
          } else {
            if (btn) {
              btn.innerHTML = `<span>Failed</span>`;
            }
            showToast("Slide extraction failed: " + (request.error || "Unknown error"), true);
            setTimeout(() => resetButton(btn), 3500);
          }
        }

        return true;
      });
    } catch (e) {}
  }
})();
