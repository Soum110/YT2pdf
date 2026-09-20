# Chrome Web Store Listing & Compliance: YT2PDF Slide Companion

This document contains the official metadata, permission justifications, privacy disclosures, and store copy for submitting the **YT2PDF Slide Companion** to the Chrome Web Store.

---

## 1. Store Listing Metadata

| Field | Value | Notes |
| :--- | :--- | :--- |
| **Extension Name** | `YT2PDF Slide Companion` | Under 45 characters |
| **Version** | `1.0.0` | Initial Manifest V3 release |
| **Category** | `Productivity` / `Education` | Primary target: students, educators, researchers |
| **Short Description** | `Capture presentation slides from YouTube videos and generate clean, high-resolution study PDFs on YT2PDFS.com.` | Max 132 characters (115 chars) |
| **Homepage URL** | `https://yt2pdfs.com` | Official website |
| **Support URL** | `https://yt2pdfs.com/contact` | Contact form & help |
| **Privacy Policy URL**| `https://yt2pdfs.com/privacy` | Web & extension privacy declaration |

---

## 2. Detailed Description (Store Copy)

```markdown
Turn YouTube lectures, conference talks, and slide presentations into clean, high-resolution PDFs and study notes in one click.

YT2PDF Slide Companion adds an elegant, native-styled "[📄 Generate PDF (YT2PDFS)]" button right beneath any YouTube video player. When clicked, it captures presentation slides directly from your active browser playback session, sends them securely to our AI curation engine, and opens your custom Slide Deck Studio on YT2PDFS.com.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌟 KEY FEATURES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 1-Click Native Button: Seamlessly embedded directly inside YouTube's action bar next to Like and Share.
• Client-Side Frame Sampling: Eliminates cloud server bot blocks by sampling frames directly in your browser.
• Automatic Slide Detection: Detects slide transitions, skips duplicate frames, and removes presenter webcam overlays.
• Interactive Slide Deck Studio on YT2PDFS.com: Review every extracted slide, exclude unwanted pages, and download your final PDF.
• AI Academic Study Guides: Generates university-grade notes with LaTeX formulas, derivations, diagrams, and summaries.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🛡️ 100% TRANSPARENCY & PRIVACY GUARANTEE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 100% Open Source: All extension code is publicly viewable and auditable on GitHub (https://github.com/soumensen-ai/YT2pdf).
• Zero Personal Data: We NEVER touch, access, or store your Google account, browsing history, or passwords.
• Zero Extension Telemetry: No third-party trackers, no analytics bloatware, and single-purpose slide capture.
• Single-Purpose Execution: Runs strictly on youtube.com watch pages when you explicitly trigger slide capture.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚀 HOW TO USE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Navigate to any educational video or lecture presentation on YouTube.
2. Click the "[📄 Generate PDF (YT2PDFS)]" button directly below the video player (or click the extension icon in your browser toolbar).
3. The companion scans keyframes in the background without interrupting your audio.
4. Once captured, it opens YT2PDFS.com with your custom slides ready for preview, curation, and instant PDF download!
```

---

## 3. Permissions Justification (For Chrome Review Team)

### `activeTab`
> **Justification:** Enables the extension to access the current YouTube tab when the user explicitly clicks the extension action button in the toolbar, allowing the popup to detect the active video title and status.

### `tabs`
> **Justification:** Used to query the current tab URL and title to verify whether the active tab is a valid YouTube video watch page (`youtube.com/watch`) before displaying the extraction interface.

### `storage`
> **Justification:** Stores non-sensitive user preferences locally (such as dark/light theme preference and last-used conversion settings). No personal data or credentials are ever stored.

### `scripting`
> **Justification:** Acts as a graceful fallback to inject `content.js` into active YouTube watch tabs if the user installs the extension while YouTube tabs are already open, preventing the need for a full page refresh.

### Host Permissions:
- `*://*.youtube.com/*`
  > **Justification:** Necessary to detect the HTML5 `<video>` player, sample presentation slide frames, and inject the native `[📄 PDF Slides]` button into the YouTube watch interface.
- `https://yt2pdfs.com/*`
  > **Justification:** Required to bridge silent extraction requests when the user pastes a link on YT2PDFS.com and transmit candidate slide frames to the conversion pipeline.
- `https://*.run.app/*`
  > **Justification:** Direct backend endpoint on Google Cloud Run to ensure reliable frame processing and failover during traffic spikes.

---

## 4. Chrome Web Store Single Purpose Policy

> **Single Purpose:** The YT2PDF Slide Companion has a single, narrow purpose: capturing presentation slides from YouTube videos and transferring candidate frames to YT2PDFS.com to compile a study PDF. It contains no secondary functionalities, advertising networks, or extraneous features.

---

## 5. Privacy Policy Disclosures

| Question | Answer | Details |
| :--- | :--- | :--- |
| Does the extension collect personally identifiable information (PII)? | **No** | No names, emails, IP logs, or passwords are requested or stored. |
| Does the extension collect financial or payment information? | **No** | The tool and extension are completely free. |
| Does the extension sell data to third parties? | **No** | Zero user data is sold, rented, or transferred. |
| Does the extension use data for creditworthiness or lending? | **No** | Not applicable. |
| Does the extension collect browsing activity outside YouTube? | **No** | Manifest permissions and scripts are strictly restricted to `youtube.com/watch` and `yt2pdfs.com`. |

---

## 6. Step-by-Step Publishing Guide (Google Chrome Developer Dashboard)

Follow these exact steps to publish the extension to the official Google Chrome Web Store:

### Step 1: Open Chrome Developer Dashboard
1. Open [chrome.google.com/webstore/devconsole](https://chrome.google.com/webstore/devconsole).
2. Sign in with your Google Account.
3. (If this is your first extension) Pay the one-time $5 developer registration fee required by Google.

### Step 2: Upload the Extension ZIP
1. In the top-right corner, click **"New Item"** (or **"Add new item"**).
2. Drag and drop the ready-made package file:
   📁 **`yt2pdf-slide-companion-v1.0.0.zip`** (located in the root of this project: `/Users/soumensen/Documents/YT2pdf/yt2pdf-slide-companion-v1.0.0.zip`).
3. The dashboard will validate the manifest and open the store listing editor.

### Step 3: Fill Out Store Listing
Copy and paste the exact text from **Section 1 & 2** of this document:
- **Product Name**: `YT2PDF Slide Companion`
- **Summary**: `Capture presentation slides from YouTube videos and generate clean, high-resolution study PDFs on YT2PDFS.com.`
- **Detailed Description**: Copy the entire text from **Section 2**.
- **Category**: Select `Productivity` or `Education`.
- **Language**: Select `English`.
- **Homepage URL**: `https://yt2pdfs.com`
- **Support URL**: `https://yt2pdfs.com/contact`

### Step 4: Complete the Privacy Tab
Click the **"Privacy"** tab on the left sidebar:
1. **Single Purpose**: Paste the statement from **Section 4**.
2. **Permission Justifications**:
   - For `activeTab`: Paste from **Section 3**.
   - For `tabs`: Paste from **Section 3**.
   - For `storage`: Paste from **Section 3**.
   - For `scripting`: Paste from **Section 3**.
   - For `*://*.youtube.com/*`: Paste from **Section 3**.
   - For `https://yt2pdfs.com/*`: Paste from **Section 3**.
   - For `https://*.run.app/*`: Paste from **Section 3**.
3. **Data Usage**:
   - Check the box: *"I do not collect or transmit any user data"*.
   - Check the certification: *"I certify that this extension complies with the Limited Use policy"*.
4. **Privacy Policy**: Enter `https://yt2pdfs.com/privacy`.

### Step 5: Distribution & Publish
1. Click the **"Distribution"** tab on the left sidebar.
2. Select **Visibility**: `Public`.
3. Select **Regions**: `All regions`.
4. Click **"Save draft"** (top right), then click **"Submit for Review"**!

> **Review Time:** Google typically completes review and approval within 12–48 hours. Once approved, your extension is live worldwide on the Chrome Web Store at `https://chromewebstore.google.com/detail/yt2pdf-slide-companion/<your-extension-id>`.
