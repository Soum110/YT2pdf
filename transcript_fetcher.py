"""
transcript_fetcher.py — Download and parse YouTube captions into timestamped segments.

Uses yt-dlp to fetch auto-generated or manual subtitles, then parses
the VTT format into a list of (start_sec, end_sec, text) tuples.
"""

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("transcript")


# ─────────────────────────────────────────────
# VTT Parser
# ─────────────────────────────────────────────

def _vtt_time_to_sec(ts: str) -> float:
    """Convert VTT timestamp like '00:01:23.456' or '01:23.456' to seconds."""
    ts = ts.strip()
    parts = ts.replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        elif len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        else:
            return float(parts[0])
    except Exception:
        return 0.0


def _parse_vtt(vtt_text: str) -> list[tuple[float, float, str]]:
    """
    Parse WebVTT subtitle text into [(start_sec, end_sec, text), ...].
    Merges consecutive cues with the same text (de-duplicates rolling captions).
    """
    segments = []
    lines = vtt_text.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        # Look for timestamp lines: "00:00:01.000 --> 00:00:04.000"
        if "-->" in line:
            parts = line.split("-->")
            if len(parts) == 2:
                start = _vtt_time_to_sec(parts[0].split()[-1] if parts[0].strip() else parts[0])
                end = _vtt_time_to_sec(parts[1].split()[0])
                # Collect text lines below the timestamp
                text_lines = []
                i += 1
                while i < len(lines) and lines[i].strip() != "":
                    raw = lines[i].strip()
                    # Strip VTT tags like <00:00:01.000><c>text</c>
                    cleaned = re.sub(r"<[^>]+>", "", raw).strip()
                    if cleaned:
                        text_lines.append(cleaned)
                    i += 1
                text = " ".join(text_lines).strip()
                if text:
                    segments.append((start, end, text))
                continue
        i += 1

    # Merge near-duplicate consecutive segments (rolling caption dedup)
    if not segments:
        return []

    merged = [segments[0]]
    for start, end, text in segments[1:]:
        prev_start, prev_end, prev_text = merged[-1]
        # If text is a superset of previous (rolling caption), replace
        if prev_text in text or text.endswith(prev_text):
            merged[-1] = (prev_start, end, text)
        elif text == prev_text:
            merged[-1] = (prev_start, end, prev_text)
        else:
            merged.append((start, end, text))

    return merged


# ─────────────────────────────────────────────
# yt-dlp subtitle downloader
# ─────────────────────────────────────────────

class _SilentLogger:
    """Routes all yt-dlp output through Python logging. Never touches stdout/stderr."""
    def debug(self, msg):
        if not msg.startswith("[debug]"):
            log.debug("yt-dlp: %s", msg)
    def info(self, msg):   log.debug("yt-dlp: %s", msg)
    def warning(self, msg): log.warning("yt-dlp(caption): %s", msg)
    def error(self, msg):   log.error("yt-dlp(caption): %s", msg)


def fetch_transcript(
    url: str,
    tmp_dir: Optional[str] = None,
    lang: str = "en",
) -> list[tuple[float, float, str]]:
    """
    Download YouTube captions for `url` and return parsed segments.

    Returns:
        List of (start_sec, end_sec, text) tuples, sorted by time.
        Empty list if no captions are available.

    Args:
        url:     YouTube (or other) video URL.
        tmp_dir: Where to save subtitle files. Uses tempfile if None.
        lang:    Preferred caption language code (default 'en').
    """
    import yt_dlp

    own_tmp = tmp_dir is None
    if own_tmp:
        tmp_dir = tempfile.mkdtemp(prefix="yt2pdf_transcript_")

    out_template = os.path.join(tmp_dir, "caption")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "logger": _SilentLogger(),
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [lang, f"{lang}-*", "en", "en-*"],
        "subtitlesformat": "vtt",
        "outtmpl": out_template,
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }

    vtt_text = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find any .vtt file that was written
        vtt_files = list(Path(tmp_dir).glob("caption*.vtt"))
        if not vtt_files:
            # Also check for .en.vtt, .en-US.vtt etc.
            vtt_files = list(Path(tmp_dir).glob("*.vtt"))

        if vtt_files:
            # Prefer manual subtitles over auto-generated ones
            manual = [f for f in vtt_files if ".auto." not in f.name and "-orig" not in f.name]
            chosen = manual[0] if manual else vtt_files[0]
            log.info("Using captions file: %s", chosen.name)
            vtt_text = chosen.read_text(encoding="utf-8", errors="replace")

    except Exception as e:
        log.warning("Caption download failed: %s", e)

    if vtt_text is None:
        log.warning("No captions found for this video. Study guide will use slide analysis only.")
        return []

    segments = _parse_vtt(vtt_text)
    log.info("Parsed %d caption segments", len(segments))
    return segments


# ─────────────────────────────────────────────
# Transcript windowing helpers
# ─────────────────────────────────────────────

def get_transcript_for_window(
    segments: list[tuple[float, float, str]],
    t_start: float,
    t_end: float,
    padding_sec: float = 2.0,
) -> str:
    """
    Return the transcript text spoken between t_start and t_end seconds.
    Adds `padding_sec` before t_start to catch lead-in speech.
    """
    effective_start = max(0, t_start - padding_sec)
    window_texts = []
    for seg_start, seg_end, text in segments:
        # Include segment if it overlaps the window
        if seg_end >= effective_start and seg_start <= t_end:
            window_texts.append(text)

    if not window_texts:
        return ""

    # Deduplicate consecutive identical sentences
    deduped = [window_texts[0]]
    for t in window_texts[1:]:
        if t != deduped[-1]:
            deduped.append(t)

    return " ".join(deduped).strip()


def assign_transcripts_to_slides(
    slide_timestamps: list[float],
    segments: list[tuple[float, float, str]],
    total_duration: float,
) -> list[str]:
    """
    For each slide (given its start timestamp), get the transcript
    text covering from that slide until the next slide appears.

    Returns a list of transcript strings aligned with slide_timestamps.
    """
    texts = []
    for i, t_start in enumerate(slide_timestamps):
        t_end = slide_timestamps[i + 1] if i + 1 < len(slide_timestamps) else total_duration
        text = get_transcript_for_window(segments, t_start, t_end)
        texts.append(text)
    return texts
