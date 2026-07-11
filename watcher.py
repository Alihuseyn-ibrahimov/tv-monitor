import threading
import time
from pathlib import Path

import requests

import classify
import clipper
import config
import story
import transcribe


def _list_ready_segments(channel_dir: Path) -> list[Path]:
    """All segments except the most recently modified one (still being written by ffmpeg)."""
    files = sorted(channel_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    return files[:-1] if len(files) > 1 else []


def notify_n8n(channel: str, keyword: str, title: str, clip_path: Path, django_clip_id=None):
    if not config.PUBLIC_BASE_URL:
        print(f"[{channel}] PUBLIC_BASE_URL not set yet, skipping webhook for {clip_path.name}")
        return
    video_url = f"{config.PUBLIC_BASE_URL}/{clip_path.name}"
    body = {"channel": channel, "keyword": keyword, "title": title, "videoUrl": video_url}
    if django_clip_id is not None:
        body["djangoClipId"] = django_clip_id
    try:
        resp = requests.post(config.N8N_WEBHOOK_URL, json=body, timeout=30)
        print(f"[{channel}] webhook -> {resp.status_code} for keyword '{keyword}'")
    except requests.RequestException as e:
        print(f"[{channel}] webhook failed: {e}")


def notify_django(channel: str, keyword: str, title: str, clip_path: Path):
    # mediapulse.az dashboard-unda gorunsün deye eyni klip məlumatını Django-ya da göndərir.
    # Django-nun verdiyi pk n8n-ə ötürülür ki, Telegram-dan verilən qərar da saytda əks olunsun.
    if not config.PUBLIC_BASE_URL:
        return None
    video_url = f"{config.PUBLIC_BASE_URL}/{clip_path.name}"
    try:
        resp = requests.post(
            config.DJANGO_INGEST_URL,
            json={"channel": channel, "keyword": keyword, "title": title, "videoUrl": video_url},
            headers={"X-Ingest-Secret": config.DJANGO_INGEST_SECRET},
            timeout=15,
        )
        print(f"[{channel}] django ingest -> {resp.status_code} for keyword '{keyword}'")
        return resp.json().get("id") if resp.ok else None
    except (requests.RequestException, ValueError) as e:
        print(f"[{channel}] django ingest failed: {e}")
        return None


def notify_all(channel: str, keyword: str, title: str, clip_path: Path):
    django_clip_id = notify_django(channel, keyword, title, clip_path)
    notify_n8n(channel, keyword, title, clip_path, django_clip_id=django_clip_id)


def process_segment(channel: str, segment_path: Path):
    try:
        transcript = transcribe.transcribe(segment_path)
    except Exception as e:
        print(f"[{channel}] transcription failed for {segment_path.name}: {e}")
        return

    matches = transcribe.find_keyword_matches(transcript["text"], config.KEYWORDS)
    if not matches:
        return

    keyword = matches[0]
    try:
        result = classify.classify_and_title(transcript["segments"], channel, keyword)
    except Exception as e:
        print(f"[{channel}] AI relevance check failed for {segment_path.name}: {e}")
        return

    if not result["relevant"]:
        print(f"[{channel}] Keyword '{keyword}' matched but AI deemed it unrelated to ƏƏSMN, skipping {segment_path.name}")
        return

    print(f"[{channel}] MATCH '{keyword}' (AI-confirmed) in {segment_path.name}: {result['title']} ({result['start']}-{result['end']}s)")
    clip_path = clipper.save_clip(
        segment_path, channel, keyword,
        result.get("start"), result.get("end"), transcript["segments"],
    )
    if clip_path.exists() and clip_path.stat().st_size > 0:
        print(f"[{channel}] clip saved: {clip_path.name} ({clip_path.stat().st_size} bytes)")
    else:
        print(f"[{channel}] ERROR: clip file missing/empty right after save_clip: {clip_path}")
    notify_all(channel, keyword, result["title"], clip_path)

    # If this segment continues an earlier one from the same broadcast
    # (back-to-back matches), keep accumulating it. If it instead closes
    # out a previous run, that run's full stitched version comes back here.
    finished_run = story.add_piece(channel, segment_path, keyword, result["title"])
    if finished_run:
        print(f"[{channel}] full-version clip ready from {finished_run['piece_count']} pieces: {finished_run['clip_path'].name}")
        notify_all(channel, finished_run["keyword"], finished_run["title"], finished_run["clip_path"])


def watch_channel(channel: str, stop_event: threading.Event, poll_seconds: int = 15):
    channel_dir = config.SEGMENTS_DIR / channel
    channel_dir.mkdir(parents=True, exist_ok=True)

    while not stop_event.is_set():
        for segment_path in _list_ready_segments(channel_dir):
            process_segment(channel, segment_path)
            # Any matching clip was already copied out by clipper.save_clip
            # above, so the raw segment can be freed immediately — recordings
            # are large (100-200MB per 3-min chunk) and would otherwise fill
            # the disk within hours across 6 channels.
            segment_path.unlink(missing_ok=True)

        # A broadcast run with no new matching segment for a while (it simply
        # ended) needs to be finalized here too — add_piece only closes a run
        # out when a later non-consecutive segment arrives, which may never happen.
        finished_run = story.flush_idle(channel)
        if finished_run:
            print(f"[{channel}] full-version clip ready from {finished_run['piece_count']} pieces (idle flush): {finished_run['clip_path'].name}")
            notify_all(channel, finished_run["keyword"], finished_run["title"], finished_run["clip_path"])

        time.sleep(poll_seconds)
