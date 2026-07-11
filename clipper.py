import re
import shutil
import subprocess
import time
from pathlib import Path

import config

# Azerbaijani -> ASCII map so clip filenames survive Telegram's Markdown
# rendering and URL auto-linking (underscores and non-ASCII break links).
_AZ_MAP = str.maketrans({
    "ə": "e", "Ə": "E", "ı": "i", "I": "I", "İ": "I",
    "ö": "o", "Ö": "O", "ü": "u", "Ü": "U",
    "ş": "s", "Ş": "S", "ç": "c", "Ç": "C", "ğ": "g", "Ğ": "G",
})

MIN_CLIP_SECONDS = 10


def _slugify(text: str) -> str:
    text = text.translate(_AZ_MAP)
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()


def _widen_with_whole_segments(start: float, end: float, segments: list[dict]) -> tuple[float, float]:
    """If the story window is too short, grow it by pulling in whole
    neighboring Whisper segments (never a partial one), alternating sides,
    so the clip still never starts or ends mid-sentence."""
    if not segments:
        return start, end
    before = sorted([s for s in segments if s["start"] < start], key=lambda s: -s["start"])
    after = sorted([s for s in segments if s["end"] > end], key=lambda s: s["end"])
    bi = ai = 0
    try_before_next = True
    while (end - start) < MIN_CLIP_SECONDS and (bi < len(before) or ai < len(after)):
        if try_before_next and bi < len(before):
            start = before[bi]["start"]
            bi += 1
        elif ai < len(after):
            end = after[ai]["end"]
            ai += 1
        elif bi < len(before):
            start = before[bi]["start"]
            bi += 1
        try_before_next = not try_before_next
    return start, end


def save_clip(
    segment_path: Path,
    channel: str,
    keyword: str,
    start: float | None = None,
    end: float | None = None,
    segments: list[dict] | None = None,
) -> Path:
    """Cuts just the relevant story out of the 3-minute segment using the
    AI-detected, sentence-boundary-aligned start/end (seconds). Falls back to
    copying the whole segment if no valid window was provided."""
    config.CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    out_name = f"{_slugify(channel)}-{_slugify(keyword)}-{int(time.time())}.mp4"
    out_path = config.CLIPS_DIR / out_name

    if start is None or end is None or end - start < 1:
        shutil.copy2(segment_path, out_path)
        return out_path

    window_start, window_end = _widen_with_whole_segments(start, end, segments or [])
    window_start = max(0.0, window_start)
    window_end = min(float(config.SEGMENT_SECONDS), window_end)
    duration = window_end - window_start

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(window_start),
        "-i", str(segment_path),
        "-t", str(duration),
        "-c", "copy",
        str(out_path),
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        shutil.copy2(segment_path, out_path)
    return out_path
