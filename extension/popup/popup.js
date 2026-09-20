/**
 * popup.js — Popup script for YT2PDF Slide Companion
 */

document.addEventListener("DOMContentLoaded", async () => {
  const videoCard = document.getElementById("video-card");
  const videoTitle = document.getElementById("video-title");
  const notYoutube = document.getElementById("not-youtube");
  const extractBtn = document.getElementById("extract-btn");

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

    if (!tab || !tab.url || !tab.url.includes("youtube.com/watch")) {
      videoCard.style.display = "none";
      notYoutube.style.display = "block";
      extractBtn.disabled = true;
      extractBtn.innerText = "Open a YouTube video first";
      return;
    }

    // Active YouTube video detected
    const cleanTitle = (tab.title || "YouTube Video").replace(" - YouTube", "").trim();
    videoTitle.innerText = cleanTitle;

    extractBtn.addEventListener("click", async () => {
      extractBtn.disabled = true;
      extractBtn.innerHTML = `<span>Starting extraction on tab...</span>`;

      try {
        await chrome.tabs.sendMessage(tab.id, { action: "extract_slides" });
        window.close(); // Close popup so user sees the progress on their YouTube tab
      } catch (err) {
        // Content script might need injection if tab was open before extension installed/updated
        extractBtn.innerHTML = `<span>Reconnecting extension...</span>`;
        try {
          await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            files: ["content.js"]
          });
          setTimeout(async () => {
            try {
              await chrome.tabs.sendMessage(tab.id, { action: "extract_slides" });
            } catch (e) {}
            window.close();
          }, 350);
        } catch (scriptErr) {
          console.warn("Could not inject script into tab:", scriptErr);
          extractBtn.innerHTML = `<span>Please refresh YouTube tab (F5)</span>`;
          setTimeout(() => window.close(), 2500);
        }
      }
    });

  } catch (err) {
    console.error("Popup error:", err);
    videoTitle.innerText = "YouTube Video Detected";
  }
});
