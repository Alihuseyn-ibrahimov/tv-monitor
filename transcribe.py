import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import requests

import config
import groq_retry

MAX_RETRIES = groq_retry.MAX_RETRIES

# 2026-07-18: 11 kanal × gündə 10.5 saat xəbər pəncərəsi Groq-un pulsuz
# limitinin (hər modelə saatda 7200 audio-saniyə) dəfələrlə üstündə idi —
# növbə saatlarla gerilədiyi üçün seqmentlər emala çatmamış purge olunurdu
# və günlərlə heç bir süjet tapılmırdı. İki birləşmiş tədbir:
#   1. Audio 1.5× sürətləndirilib göndərilir — billinq audio-saniyə ilə
#      olduğundan kvota istifadəsi 33% azalır. Real ARB24 seqmentində
#      yoxlanılıb: 1.5×-də transkript 1×-lə demək olar eynidir (açar söz
#      kökləri salamat qalır), 2×-də isə nəzərəçarpan pozulma başlayır.
#   2. Hər Whisper modelinin AYRICA kvota hovuzu var (API başlıqları ilə
#      təsdiqlənib) — iki modeli növbələşdirmək saatlıq tutumu ikiqat edir.
# Birlikdə: 7200 → effektiv 21600 real-audio-saniyə/saat.
SPEED_FACTOR = 1.5


def extract_audio(video_path: Path) -> Path:
    """Pulls a small mono mp3 out of the video for cheap/fast transcription.
    Uses a unique filename (not just the segment's timestamp stem) since
    different channels can produce segments with the same timestamp."""
    audio_path = Path(tempfile.gettempdir()) / f"{video_path.stem}-{uuid.uuid4().hex}.mp3"
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-af", f"atempo={SPEED_FACTOR}", "-ac", "1", "-ar", "16000", "-b:a", "64k",
        str(audio_path),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return audio_path


def transcribe(video_path: Path) -> dict:
    """Returns {"text": full transcript, "segments": [{"start", "end", "text"}, ...]}
    for a video segment, via Groq Whisper. Segment-level timestamps let us later
    cut out just the relevant portion of the clip instead of the whole recording.
    Retries on rate-limit (429) responses, switching between the two Whisper
    models' independent quota pools before backing off."""
    audio_path = extract_audio(video_path)
    try:
        # Yayım fasiləsi/zədəli stream boş-qırıq mp3 verə bilir — Groq buna
        # 400 qaytarır (XezerTV-də müşahidə olunub). Sorğunu boşuna xərcləmə.
        if audio_path.stat().st_size < 10_000:
            return {"text": "", "segments": []}
        last_response = None
        for attempt in range(MAX_RETRIES):
            model = config.GROQ_MODELS[attempt % len(config.GROQ_MODELS)]
            with open(audio_path, "rb") as f:
                response = requests.post(
                    config.GROQ_TRANSCRIBE_URL,
                    headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                    files={"file": (audio_path.name, f, "audio/mpeg")},
                    data={
                        "model": model,
                        "language": "az",
                        "response_format": "verbose_json",
                    },
                    timeout=120,
                )
            if groq_retry.should_retry(response.status_code) and attempt < MAX_RETRIES - 1:
                last_response = response
                # Əvvəlcə o biri modelin hovuzunu dərhal yoxla; yalnız hər
                # iki hovuz dalbadal rədd edəndə gözlə.
                if attempt % len(config.GROQ_MODELS) == len(config.GROQ_MODELS) - 1:
                    time.sleep(groq_retry.wait_seconds(attempt // len(config.GROQ_MODELS), response))
                continue
            response.raise_for_status()
            data = response.json()
            # Timestamplar sürətləndirilmiş audioya aiddir — klip kəsimi üçün
            # real video saniyələrinə geri miqyaslanmalıdır.
            return {
                "text": data["text"],
                "segments": [
                    {
                        "start": s["start"] * SPEED_FACTOR,
                        "end": s["end"] * SPEED_FACTOR,
                        "text": s["text"],
                    }
                    for s in data.get("segments", [])
                ],
            }
        last_response.raise_for_status()
    finally:
        audio_path.unlink(missing_ok=True)


def find_keyword_matches(transcript_text: str, keywords: list[str]) -> list[str]:
    """Returns the list of keywords (from `keywords`) found in the transcript."""
    text_lower = transcript_text.lower()
    return [kw for kw in keywords if kw.lower() in text_lower]
