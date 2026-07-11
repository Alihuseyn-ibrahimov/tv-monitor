import datetime
import subprocess
import threading
import time

import config

# Prefix marking a channel URL as a YouTube channel page (rather than a
# direct HLS stream) whose live status varies — e.g. İdman TV only goes
# live on YouTube during actual sports broadcasts, not 24/7. The real
# stream URL is resolved fresh via yt-dlp before every recording attempt.
YOUTUBE_LIVE_PREFIX = "youtube-live:"


def _in_news_hours() -> bool:
    """True if the current local time falls in one of config.NEWS_HOURS.
    Start/end are hour-of-day numbers and may carry a fractional half-hour
    (e.g. 22.5 == 22:30) so a window's tail can extend past the top of the
    hour. If NEWS_HOURS is empty, recording runs around the clock."""
    if not config.NEWS_HOURS:
        return True
    now = datetime.datetime.now()
    t = now.hour + now.minute / 60
    return any(start <= t < end for start, end in config.NEWS_HOURS)


def _resolve_youtube_live_url(channel_page_url: str) -> str | None:
    """Returns the direct playable stream URL if the YouTube channel is
    currently live, or None if it isn't broadcasting right now."""
    try:
        result = subprocess.run(
            ["yt-dlp", "-g", channel_page_url],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    lines = [line for line in result.stdout.strip().splitlines() if line]
    return lines[-1] if lines else None


def start_recording(channel_name: str, stream_url: str) -> subprocess.Popen | None:
    """Launch a persistent ffmpeg process that segments the live stream
    into fixed-length chunks. Uses stream copy (no re-encode) for low CPU
    usage; if a channel's stream isn't segment/mp4 compatible, ffmpeg will
    exit quickly and the caller's restart loop will retry.
    Returns None (without starting anything) if this is a YouTube-live
    channel that isn't currently broadcasting."""
    if stream_url.startswith(YOUTUBE_LIVE_PREFIX):
        resolved = _resolve_youtube_live_url(stream_url[len(YOUTUBE_LIVE_PREFIX):])
        if resolved is None:
            return None
        stream_url = resolved

    channel_dir = config.SEGMENTS_DIR / channel_name
    channel_dir.mkdir(parents=True, exist_ok=True)

    log_path = config.LOGS_DIR / f"{channel_name}.log"
    log_file = open(log_path, "a", encoding="utf-8")

    cmd = [
        "ffmpeg", "-y",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", stream_url,
        "-c", "copy",
        "-map", "0",
        "-f", "segment",
        "-segment_time", str(config.SEGMENT_SECONDS),
        "-reset_timestamps", "1",
        "-strftime", "1",
        str(channel_dir / "%Y%m%d_%H%M%S.mp4"),
    ]
    return subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)


def _stop(proc: subprocess.Popen):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _take_sample(channel_name: str, stream_url: str, stop_event: threading.Event):
    """Records one SEGMENT_SECONDS chunk outside news hours, then stops.
    Runs a little past the segment boundary (SAMPLE_MARGIN_SECONDS) so
    ffmpeg's segment muxer closes the first file and starts a second one —
    that's what makes watcher.py's "last file may still be growing" check
    treat the sample as a normal, ready segment instead of ignoring it."""
    proc = start_recording(channel_name, stream_url)
    if proc is None:
        return
    print(f"[{channel_name}] Saatlıq nümunə yazılışı başladı (pid={proc.pid})")
    stop_event.wait(config.SEGMENT_SECONDS + config.SAMPLE_MARGIN_SECONDS)
    _stop(proc)
    print(f"[{channel_name}] Saatlıq nümunə tamamlandı")


def supervise(channel_name: str, stream_url: str, stop_event: threading.Event):
    """Keeps ffmpeg alive for a channel while inside the configured news
    hours. Outside news hours, takes one short sample per channel every
    config.SAMPLE_INTERVAL_SECONDS instead of recording nothing (set that
    to 0 to fall back to the old "record nothing off-hours" behaviour)."""
    proc = None
    next_sample_at = 0.0  # due immediately the first time we go off-hours
    while not stop_event.is_set():
        if _in_news_hours():
            if proc is None or proc.poll() is not None:
                proc = start_recording(channel_name, stream_url)
                if proc is not None:
                    print(f"[{channel_name}] ffmpeg started (pid={proc.pid})")
            time.sleep(10)
            next_sample_at = 0.0  # sample right away once news hours end
        else:
            if proc is not None and proc.poll() is None:
                print(f"[{channel_name}] Xəbər saatlarından kənar, qeyd dayandırılır")
                _stop(proc)
                proc = None

            if config.SAMPLE_INTERVAL_SECONDS <= 0:
                time.sleep(60)
                continue

            now = time.time()
            if now >= next_sample_at:
                _take_sample(channel_name, stream_url, stop_event)
                next_sample_at = time.time() + config.SAMPLE_INTERVAL_SECONDS
            else:
                time.sleep(min(60, next_sample_at - now))

    if proc is not None and proc.poll() is None:
        _stop(proc)
