import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import requests

import config

MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 20


def extract_audio(video_path: Path) -> Path:
    """Pulls a small mono mp3 out of the video for cheap/fast transcription.
    Uses a unique filename (not just the segment's timestamp stem) since
    different channels can produce segments with the same timestamp."""
    audio_path = Path(tempfile.gettempdir()) / f"{video_path.stem}-{uuid.uuid4().hex}.mp3"
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k",
        str(audio_path),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return audio_path


def transcribe(video_path: Path) -> dict:
    """Returns {"text": full transcript, "segments": [{"start", "end", "text"}, ...]}
    for a video segment, via Groq Whisper. Segment-level timestamps let us later
    cut out just the relevant portion of the clip instead of the whole recording.
    Retries on rate-limit (429) responses with a short wait."""
    audio_path = extract_audio(video_path)
    try:
        last_response = None
        for attempt in range(MAX_RETRIES):
            with open(audio_path, "rb") as f:
                response = requests.post(
                    config.GROQ_TRANSCRIBE_URL,
                    headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                    files={"file": (audio_path.name, f, "audio/mpeg")},
                    data={
                        "model": config.GROQ_MODEL,
                        "language": "az",
                        "response_format": "verbose_json",
                    },
                    timeout=120,
                )
            if response.status_code == 429 and attempt < MAX_RETRIES - 1:
                last_response = response
                time.sleep(RETRY_WAIT_SECONDS)
                continue
            response.raise_for_status()
            data = response.json()
            return {
                "text": data["text"],
                "segments": [
                    {"start": s["start"], "end": s["end"], "text": s["text"]}
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
