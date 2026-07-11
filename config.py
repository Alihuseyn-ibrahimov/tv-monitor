import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SEGMENTS_DIR = BASE_DIR / "segments"
CLIPS_DIR = BASE_DIR / "clips"
LOGS_DIR = BASE_DIR / "logs"
STORY_DIR = BASE_DIR / "stories"

# Live stream sources (HLS). Verify each in VLC before relying on it —
# these are community-maintained mirrors and can go offline without notice.
CHANNELS = {
    "AzTV": "https://str.yodacdn.net/azertv/index.m3u8",
    "ARB": "https://raw.githubusercontent.com/UzunMuhalefet/streams/main/myvideo-az/arb.m3u8",
    "ARB24": "https://raw.githubusercontent.com/UzunMuhalefet/streams/main/myvideo-az/arb-24.m3u8",
    "BakuTV": "https://rtmp.baku.tv/hls/bakutv.m3u8",
    "XezerTV": "https://www.xezerxeber.az/stream/index.m3u8",
    "MedeniyyetTV": "https://str.yodacdn.net/medeniyyettele/index.m3u8",
    "RealTV": "https://raw.githubusercontent.com/UzunMuhalefet/streams/main/myvideo-az/real-tv.m3u8",
    # 2026-07-09: eyni CDN-i (149.255.152.199) istifadə edən UzunMuhalefet
    # mirror repo-sunda tapıldı, VPS-dən ffmpeg ilə sınanıb işlək çıxdı.
    "SpaceTV": "https://raw.githubusercontent.com/UzunMuhalefet/streams/main/myvideo-az/space-tv.m3u8",
    "ITV": "https://raw.githubusercontent.com/UzunMuhalefet/streams/main/myvideo-az/ictimai-tv.m3u8",
    # 2026-07-09: atv.az/live səhifəsindəki Ant Media Server iframe-dən (id=atvcanli)
    # standart HLS yolu ilə tapıldı, VPS-dən ffmpeg ilə sınanıb işlək çıxdı.
    "ATV": "https://lives.atv.az:5443/ATV_TV_STREAM/streams/atvcanli.m3u8",
    # 2026-07-09: idmantv.az-da veb pleyer yoxdur, yalnız YouTube kanalında
    # (İdman TV) idman hadisələri (matçlar) zamanı canlı yayım açılır — 24/7
    # deyil. "youtube-live:" prefiksi recorder.py-ə deyir ki, hər cəhddən
    # əvvəl yt-dlp ilə kanalın hazırda canlı olub-olmadığını yoxlasın; canlı
    # deyilsə, sadəcə bu kanal üçün heç nə qeydə alınmır, digərlərinə təsir etmir.
    "IdmanTV": "youtube-live:https://www.youtube.com/channel/UCT5ztSm0LfTNkde7wzq0Faw/live",
    # LiderTV kanalı bağlanıb, artıq mövcud deyil — əlavə edilməyəcək.
}

# Keywords to watch for (case-insensitive). Positive-signal subset of the
# PRDostu/Quantum ƏƏSMN keyword set — only phrases that typically appear in
# good-news stories (increases, new services, jobs created, benefits granted).
# Negative phrases (violations, cuts, crises) and neutral entity names that
# match both good and bad coverage were intentionally left out, since matched
# clips are destined for the YouTube channel after Telegram approval.
KEYWORDS = [
    # Artımlar / ödənişlər
    "pensiya artımı",
    "pensiya təyin",
    "pensiya ödənişi",
    "əmək haqqının artımı",
    "əməkhaqqı artırıldı",
    "əməkhaqqılar artdı",
    "minimum əməkhaqqı",
    "minimum maaş",
    "minimum əmək pensiyası",
    "prezident təqaüdü",
    "sosial islahatlar",
    # Məşğulluq / yeni iş yerləri
    "iş yeri açıldı",
    "iş yeri yaradıldı",
    "iş yerləri yaradıl",
    "yeni iş yerləri",
    "iş imkan",
    "işlə təmin edildi",
    "özünüməşğulluq",
    "özünüməşğulluq proqramı",
    "əmək yarmarkası",
    "əmək bazarı",
    "iş sərgisi",
    "peşə hazırlığı kursu",
    "peşə kursu",
    "ixtisas kursu",
    "məşğulluq portalı",
    "məşğulluq mərkəzi",
    "məşğulluq xidməti",
    "vakansiya bazası",
    "DMA",
    "Dövlət Məşğulluq Agentliyi",
    # Müavinətlər / sosial dəstək
    "sosial yardım",
    "ünvanlı sosial yardım",
    "sosial müavinət",
    "uşaq müavinəti",
    "körpə müavinəti",
    "analıq müavinəti",
    "analıq məzuniyyəti",
    "birdəfəlik müavinət",
    "dəfn müavinəti",
    "işsizlik müavinəti",
    "işsizlikdən sığorta",
    "uşağın anadan olmasına görə",
    "çoxuşaqlı ailə",
    "aztəminatlı ailə",
    "uşaq pulu",
    # Şəhid ailələri və müharibə iştirakçıları
    "döyüşçü müavinəti",
    "döyüşçü pensiyası",
    # Xidmətlər / reabilitasiya
    "reabilitasiya xidməti",
    "protez",
    "protez-ortopedik",
    "sanatoriya-kurort",
    "sosial xidmət mərkəzi",
    "günərzi",
    "əlillik təyin",
    "əlilliyin təyin",
    # DOST proqramları
    "DOST mərkəzi",
    "DOST İnklüziv",
    "Könüllü DOST proqramı",
    "Qarabağ DOST",
    "Şərqi Zəngəzur DOST",
    "DOST modeli",
    "inklüziv inkişaf",
    # Ailə / qayğı
    "övladlığa götürmə",
    "himayədar ailə",
]

SEGMENT_SECONDS = 180  # length of each recorded chunk

# Only record continuously during these local-time hour windows (start
# inclusive, end exclusive) — main Azerbaijani news blocks, each padded
# with 30 extra minutes past its scheduled end in case a relevant story
# airs late or a broadcast overruns — to keep transcription volume (and
# cost) manageable across many channels. End values may carry ".5" for a
# half-hour boundary (e.g. 10.5 == 10:30). Leave empty for 24/7 recording.
NEWS_HOURS = [(7, 10.5), (12, 14.5), (18, 22.5)]

# Outside NEWS_HOURS, instead of recording nothing, take one short sample
# per channel every SAMPLE_INTERVAL_SECONDS (recorder.py records for
# SEGMENT_SECONDS + SAMPLE_MARGIN_SECONDS then stops, so the sample still
# behaves like a normal finished segment for watcher.py). Set
# SAMPLE_INTERVAL_SECONDS to 0 to disable off-hours sampling entirely.
SAMPLE_INTERVAL_SECONDS = 3600  # once per hour
SAMPLE_MARGIN_SECONDS = 10  # small buffer so the segment file fully closes

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"

N8N_WEBHOOK_URL = "https://n8n.mediapulse.az/webhook/tv-clip-approval"

CLIP_SERVER_PORT = 8787
# Reachable via Caddy (reverse proxy + auto-HTTPS) on the VPS, routing
# tvclips.mediapulse.az -> localhost:8787.
PUBLIC_BASE_URL = "https://tvclips.mediapulse.az"

DJANGO_INGEST_URL = "https://mediapulse.az/landing-admin/tv-clips/ingest/"
DJANGO_INGEST_SECRET = os.environ.get("DJANGO_INGEST_SECRET", "")
