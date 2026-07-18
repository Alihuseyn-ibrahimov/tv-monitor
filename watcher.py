import json
import shutil
import threading
import time
from pathlib import Path

import requests

import classify
import clipper
import config
import groq_retry
import story
import transcribe


def _list_ready_segments(channel_dir: Path) -> list[Path]:
    """All segments except the most recently modified one (still being written by ffmpeg)."""
    files = sorted(channel_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    return files[:-1] if len(files) > 1 else []


def _cache_path(segment_path: Path) -> Path:
    # Uğurlu transkript diskə yazılır ki, classify addımı kvota xətası ilə
    # uğursuz olanda seqment İTMƏSİN: növbəti dövrədə bahalı transkripsiya
    # təkrarlanmadan yalnız classify yenidən cəhd edilir.
    return segment_path.with_suffix(".transcript.json")


def _purge_stale_segments(channel_dir: Path, max_age_seconds: int) -> None:
    """Safety valve: normally every segment is deleted right after
    process_segment() handles it (see watch_channel). If processing ever
    falls behind or a segment slips through unprocessed (an unforeseen
    failure mode, not just the one already fixed in process_segment), this
    caps how long unprocessed segments can accumulate so the disk never
    reaches the crisis this was added after (2026-07-16: segments piled up
    for ~2 days until the disk hit 100% and the whole pipeline seized up).
    2026-07-18: yaş limiti ilə yanaşı disk-əsaslı ikinci qoruyucu əlavə
    olundu — boş yer DISK_FREE_FLOOR_BYTES-dan aşağı düşsə, yaşından asılı
    olmayaraq ən köhnə seqmentlər silinir."""
    now = time.time()
    for path in channel_dir.glob("*.mp4"):
        try:
            if now - path.stat().st_mtime > max_age_seconds:
                path.unlink(missing_ok=True)
                _cache_path(path).unlink(missing_ok=True)
        except OSError:
            pass
    # Sahibi silinmiş yetim transkript keşləri (mp4 purge olunub, json qalıb)
    for cache in channel_dir.glob("*.transcript.json"):
        try:
            if not cache.with_suffix("").with_suffix(".mp4").exists():
                cache.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        if shutil.disk_usage(channel_dir).free >= config.DISK_FREE_FLOOR_BYTES:
            return
        oldest_first = sorted(channel_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
        # Ən yenisi hələ yazılır — ona toxunma
        for path in oldest_first[:-1]:
            path.unlink(missing_ok=True)
            _cache_path(path).unlink(missing_ok=True)
            if shutil.disk_usage(channel_dir).free >= config.DISK_FREE_FLOOR_BYTES:
                break
    except OSError:
        pass


def _write_heartbeat(channel: str) -> None:
    """Proof that this channel's watch_channel loop is still alive and
    iterating — independent of whether there was anything to process this
    round. healthcheck.py alerts (and, if every channel goes stale at once,
    auto-restarts the service) when a heartbeat stops updating, since a
    silently-dead thread previously left segments piling up forever with
    nothing to notice or fix it."""
    try:
        config.HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
        (config.HEARTBEAT_DIR / f"{channel}.hb").write_text(str(time.time()))
    except OSError:
        pass


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


def notify_django(channel: str, keyword: str, title: str, clip_path: Path, sentiment: str = ""):
    # mediapulse.az dashboard-unda gorunsün deye eyni klip məlumatını Django-ya da göndərir.
    # Django-nun verdiyi pk n8n-ə ötürülür ki, Telegram-dan verilən qərar da saytda əks olunsun.
    # `sentiment` (MÜSBƏT/MƏNFİ, classify.py-dən) Django tərəfində klipin PR-monitorinq
    # lentinə (NewsResult) daxil edilməsi üçün istifadə olunur.
    if not config.PUBLIC_BASE_URL:
        return None
    video_url = f"{config.PUBLIC_BASE_URL}/{clip_path.name}"
    try:
        resp = requests.post(
            config.DJANGO_INGEST_URL,
            json={"channel": channel, "keyword": keyword, "title": title,
                  "videoUrl": video_url, "sentiment": sentiment},
            headers={"X-Ingest-Secret": config.DJANGO_INGEST_SECRET},
            timeout=15,
        )
        print(f"[{channel}] django ingest -> {resp.status_code} for keyword '{keyword}'")
        return resp.json().get("id") if resp.ok else None
    except (requests.RequestException, ValueError) as e:
        print(f"[{channel}] django ingest failed: {e}")
        return None


def notify_all(channel: str, keyword: str, title: str, clip_path: Path, sentiment: str = ""):
    django_clip_id = notify_django(channel, keyword, title, clip_path, sentiment=sentiment)
    notify_n8n(channel, keyword, title, clip_path, django_clip_id=django_clip_id)


def _is_quota_error(e: Exception) -> bool:
    """Groq-un keçici (kvota/server) xətası — seqmenti silmək yox, sonra
    yenidən cəhd etmək lazımdır."""
    return (
        isinstance(e, requests.HTTPError)
        and e.response is not None
        and e.response.status_code in groq_retry.RETRYABLE_STATUS_CODES
    )


def process_segment(channel: str, segment_path: Path) -> bool:
    """True = seqmentin işi bitdi (silinə bilər); False = keçici kvota
    xətası, seqment saxlanılsın və növbəti dövrədə yenidən cəhd edilsin
    (purge yaş limiti yenə də son sərhəddir)."""
    # Purge növbədə gözləyən seqmenti silmiş ola bilər — əvvəllər bu hal
    # ffmpeg "exit status 254" kimi görünüb log-u yanıltırdı (3 gündə 177 dəfə).
    if not segment_path.exists():
        return True

    cache = _cache_path(segment_path)
    transcript = None
    if cache.exists():
        try:
            transcript = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            transcript = None
    if transcript is None:
        try:
            transcript = transcribe.transcribe(segment_path)
        except Exception as e:
            print(f"[{channel}] transcription failed for {segment_path.name}: {e}")
            return not _is_quota_error(e)

    matches = transcribe.find_keyword_matches(transcript["text"], config.KEYWORDS)
    if not matches:
        return True

    keyword = matches[0]
    if not cache.exists():
        try:
            cache.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    try:
        result = classify.classify_and_title(transcript["segments"], channel, keyword)
    except Exception as e:
        print(f"[{channel}] AI relevance check failed for {segment_path.name}: {e}")
        return not _is_quota_error(e)

    if not result["relevant"]:
        print(f"[{channel}] Keyword '{keyword}' matched but AI deemed it unrelated to ƏƏSMN, skipping {segment_path.name}")
        return True

    print(f"[{channel}] MATCH '{keyword}' (AI-confirmed) in {segment_path.name}: {result['title']} ({result['start']}-{result['end']}s)")
    # clipper/story/notify aşağıda bir dəfə uğursuz olsa belə (məs. disk dolu
    # olanda shutil.copy2-nin atdığı OSError) bu funksiya çağırıldığı yerdə
    # (watch_channel) try/except YOXDUR — ötən bir istisna bütün kanalın
    # thread-ini sükutla öldürür, seqment heç vaxt silinmir və disk get-gedə
    # dolur (2026-07-16-da bu, diskin 100% dolmasına səbəb oldu). Ona görə
    # bu blok özü qorunur ki, xəta olsa belə funksiya normal qayıdıb seqmentin
    # silinməsinə imkan versin.
    try:
        clip_path = clipper.save_clip(
            segment_path, channel, keyword,
            result.get("start"), result.get("end"), transcript["segments"],
        )
        if clip_path.exists() and clip_path.stat().st_size > 0:
            print(f"[{channel}] clip saved: {clip_path.name} ({clip_path.stat().st_size} bytes)")
        else:
            print(f"[{channel}] ERROR: clip file missing/empty right after save_clip: {clip_path}")
        notify_all(channel, keyword, result["title"], clip_path, sentiment=result.get("sentiment", ""))

        # If this segment continues an earlier one from the same broadcast
        # (back-to-back matches), keep accumulating it. If it instead closes
        # out a previous run, that run's full stitched version comes back here.
        # Sentiment ötürülmür: eyni başlıqlı NewsResult artıq ilk parçadan mövcuddur,
        # Django ingest dublikatı tanıyıb yalnız linki tam versiyaya yeniləyir.
        finished_run = story.add_piece(channel, segment_path, keyword, result["title"])
        if finished_run:
            print(f"[{channel}] full-version clip ready from {finished_run['piece_count']} pieces: {finished_run['clip_path'].name}")
            notify_all(channel, finished_run["keyword"], finished_run["title"], finished_run["clip_path"])
    except Exception as e:
        print(f"[{channel}] clip save/notify failed for {segment_path.name}: {e}")
    return True


def watch_channel(channel: str, stop_event: threading.Event, poll_seconds: int = 15):
    channel_dir = config.SEGMENTS_DIR / channel
    channel_dir.mkdir(parents=True, exist_ok=True)

    while not stop_event.is_set():
        _write_heartbeat(channel)
        _purge_stale_segments(channel_dir, config.SEGMENT_MAX_AGE_SECONDS)

        for segment_path in _list_ready_segments(channel_dir):
            done = process_segment(channel, segment_path)
            # Any matching clip was already copied out by clipper.save_clip
            # above, so the raw segment can be freed immediately — recordings
            # are large (100-200MB per 3-min chunk) and would otherwise fill
            # the disk within hours across 6 channels. Keçici kvota xətasında
            # (done=False) seqment və transkript keşi saxlanılır ki, növbəti
            # dövrədə yenidən cəhd olunsun.
            if done:
                segment_path.unlink(missing_ok=True)
                _cache_path(segment_path).unlink(missing_ok=True)
            # A single segment can block here for minutes (Groq 429 retry
            # backoff — see groq_retry.py). If the backlog is long, waiting
            # until the whole for-loop finishes to purge (as this used to)
            # meant the purge line was never reached for a very long time —
            # exactly the failure mode this was meant to prevent. Purging
            # after every segment (not just once per outer while-loop pass)
            # keeps the backlog capped even while a slow queue is draining.
            _purge_stale_segments(channel_dir, config.SEGMENT_MAX_AGE_SECONDS)

        # A broadcast run with no new matching segment for a while (it simply
        # ended) needs to be finalized here too — add_piece only closes a run
        # out when a later non-consecutive segment arrives, which may never happen.
        finished_run = story.flush_idle(channel)
        if finished_run:
            print(f"[{channel}] full-version clip ready from {finished_run['piece_count']} pieces (idle flush): {finished_run['clip_path'].name}")
            notify_all(channel, finished_run["keyword"], finished_run["title"], finished_run["clip_path"])

        time.sleep(poll_seconds)
