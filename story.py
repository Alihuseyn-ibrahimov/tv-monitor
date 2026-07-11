import datetime
import shutil
import subprocess
import time
from pathlib import Path

import config

# If a channel keeps matching on back-to-back segments, it's almost
# certainly one continuous broadcast (e.g. a talk show) that our fixed
# 3-minute segmentation chopped into pieces. This module accumulates the
# raw segments of such a run and, once it ends, stitches them into one
# gapless "full version" clip in addition to the individual per-segment
# clips already sent.
MAX_GAP_SECONDS = config.SEGMENT_SECONDS + 60  # allows some processing jitter
FLUSH_IDLE_SECONDS = 180  # finalize a run if nothing new arrives for this long

_active: dict[str, dict] = {}


def _segment_start_time(segment_path: Path) -> float:
    dt = datetime.datetime.strptime(segment_path.stem, "%Y%m%d_%H%M%S")
    return dt.timestamp()


def _finalize(channel: str) -> dict | None:
    state = _active.pop(channel, None)
    if not state:
        return None
    if len(state["pieces"]) < 2:
        for p in state["pieces"]:
            p.unlink(missing_ok=True)
        return None

    config.CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    list_path = config.STORY_DIR / f"{channel}-concat-{int(time.time())}.txt"
    list_path.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in state["pieces"]), encoding="utf-8"
    )
    out_path = config.CLIPS_DIR / f"{channel}-full-{int(time.time())}.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        str(out_path),
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    for p in state["pieces"]:
        p.unlink(missing_ok=True)
    list_path.unlink(missing_ok=True)

    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        return None

    return {
        "clip_path": out_path,
        "keyword": state["keyword"],
        "title": f"{state['title']} (Tam buraxılış)",
        "piece_count": len(state["pieces"]),
    }


def add_piece(channel: str, segment_path: Path, keyword: str, title: str) -> dict | None:
    """Registers a matched segment as part of the channel's in-progress
    broadcast run. Returns a finalized {"clip_path", "keyword", "title"}
    dict if adding this piece closed out a PREVIOUS run (because this
    segment isn't consecutive with it) — that finalized run should be
    uploaded as a "full version" clip. Otherwise returns None."""
    config.STORY_DIR.mkdir(parents=True, exist_ok=True)
    seg_start = _segment_start_time(segment_path)
    state = _active.get(channel)
    finalized = None

    if state is not None and seg_start - state["last_start"] > MAX_GAP_SECONDS:
        finalized = _finalize(channel)
        state = None

    if state is None:
        state = {"pieces": [], "keyword": keyword, "title": title}
        _active[channel] = state

    piece_copy = config.STORY_DIR / f"{channel}-{segment_path.name}"
    shutil.copy2(segment_path, piece_copy)
    state["pieces"].append(piece_copy)
    state["last_start"] = seg_start
    state["last_touch"] = time.time()

    return finalized


def flush_idle(channel: str) -> dict | None:
    """Call periodically (independent of new matches) so a run that went
    quiet — the broadcast simply ended — still gets finalized even though
    no future non-consecutive segment will ever trigger add_piece's check."""
    state = _active.get(channel)
    if state and time.time() - state["last_touch"] > FLUSH_IDLE_SECONDS:
        return _finalize(channel)
    return None
