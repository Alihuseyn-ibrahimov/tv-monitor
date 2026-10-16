import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SEGMENTS_DIR = BASE_DIR / "segments"
CLIPS_DIR = BASE_DIR / "clips"
LOGS_DIR = BASE_DIR / "logs"
STORY_DIR = BASE_DIR / "stories"
HEARTBEAT_DIR = BASE_DIR / "heartbeats"

# 2026-07-16 disk-dolması hadisəsindən sonra əlavə olundu (bax:
# ~/task_plan.md, "TV Monitor — Dayanıqlılıq Sistemi"). Normalda hər seqment
# emal olunub silinir, amma emal geriləsə (Groq kvota, şəbəkə problemi və s.)
# seqmentlər yığılıb diski dolduraraq HƏR ŞEYİ iflic edə bilər. Bu limitdən
# köhnə seqmentlər emal statusundan asılı olmayaraq silinir ki, disk heç vaxt
# kritik həddə çatmasın (bax: watcher.py `_purge_stale_segments`).
# 2026-07-18: 30 dəq → 60 dəq. Kvota darboğazında növbə 30 dəqiqədən çox
# geriləyəndə seqmentlər kütləvi şəkildə emala çatmadan silinirdi (günlərlə
# 0 süjet). Transkripsiya tutumu 3× artırıldığından (bax: transcribe.py)
# 60 dəqiqəlik backlog ~13GB-dan çox olmur; disk üçün əlavə təhlükəsizlik
# watcher.py-dəki DISK_FREE_FLOOR qoruyucusudur.
SEGMENT_MAX_AGE_SECONDS = 60 * 60  # 60 dəqiqə

# Disk üzərində minimum boş yer (bayt). Bundan aşağı düşəndə purge yaş
# limitini gözləmədən ən köhnə seqmentləri silir (2026-07-16 disk-dolması
# hadisəsinin təkrarlanmaması üçün ikinci, disk-əsaslı qoruyucu).
DISK_FREE_FLOOR_BYTES = 5 * 1024**3  # 5 GB

# Kanalın emal thread-i (watch_channel) hər dövrədə "heartbeat" yeniləyir.
# Bundan köhnə heartbeat = thread donub/ölüb sayılır (bax: healthcheck.py).
HEARTBEAT_STALE_SECONDS = 15 * 60  # 15 dəqiqə

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
# Matching is a plain case-insensitive substring test (transcribe.find_keyword_matches).
# Azerbaijani is agglutinative — a single stem takes many case/plural/possessive
# suffixes ("pensiya" -> "pensiyanın", "pensiyaların ödənilməsi", "pensiya artımı").
# So keywords are the shortest INVARIANT ROOT rather than one fully-inflected form;
# one root catches every declension as a substring. (Earlier the list held rigid
# phrases like "pensiya ödənişi", which missed the far more common "pensiyaların
# ödənilməsi" and let real ƏƏSMN stories slip through unrecorded.)
#
# Core institution/entity names (Dövlət Sosial Müdafiə Fondu, the ministry, DMA,
# DOST) are now INCLUDED even though on their own they are sentiment-neutral: they
# appear in virtually every relevant story, and two downstream filters keep noise
# out — classify.py asks an LLM whether the segment is genuinely about ƏƏSMN's
# areas of work (rejecting coincidental mentions), and every clip still needs
# manual Telegram approval before it reaches the YouTube channel.
KEYWORDS = [
    # ── Qurum / entity adları (əksər ƏƏSMN süjetlərində keçir) ──
    # ƏƏSMN tabeli qurumlar. Bəziləri aşağıdakı geniş köklərlə də tutulur
    # (məs. "Dövlət Sosial Müdafiə Fondu"→"sosial müdafiə", "Sosial Xidmətlər
    # Agentliyi"→"sosial xidmət", "DOST İnklüziv İnkişaf..."→"DOST İnklüziv").
    "sosial müdafiə",          # "Dövlət Sosial Müdafiə Fondu", "...sosial müdafiəsi", nazirlik adı, "sosial müdafiə naziri"
    "sosial sığorta",
    "sosial xidmət",           # "sosial xidmət mərkəzi", "Sosial Xidmətlər Agentliyi"
    "sosial təminat",          # "Dayanıqlı və Operativ Sosial Təminat Agentliyi" (DOST)
    "əmək müfəttiş",           # "Dövlət Əmək Müfəttişliyi Xidməti", "əmək müfəttişliyi/müfəttişinin"
    "tibbi sığorta",           # "Dövlət Tibbi Sığorta və Ekspertiza Agentliyi"
    "Dövlət Məşğulluq Agentliyi",
    "DMA",
    # ── Pensiya / təqaüd ──
    "pensiya",                 # pensiyaların, pensiyanın, pensiya artımı/təyini/ödənişi, pensiyaçı...
    "təqaüd",                  # "prezident təqaüdü" və s.
    # ── Əmək haqqı / maaş ──
    "əmək haqq",               # "əmək haqqı", "əmək haqqının artımı"
    "əməkhaqq",                # bitişik yazılış variantı
    "minimum maaş",
    "minimum əmək",            # "minimum əmək haqqı", "minimum əmək pensiyası"
    "maaş artım",
    "maaş artırıl",
    # ── Məşğulluq / iş yerləri ──
    "məşğulluq",               # məşğulluq mərkəzi/xidməti/portalı, məşğulluğun...
    "işsizlik",                # işsizlik müavinəti, işsizlikdən sığorta
    "iş yeri",                 # iş yeri açıldı/yaradıldı
    "iş yerlər",               # iş yerləri yaradıldı, iş yerlərinin
    "iş imkan",
    "işlə təmin",
    "işə düzəl",               # işə düzəldi, işə düzəlmə
    "özünüməşğulluq",
    "əmək yarmarka",
    "əmək bazar",
    "iş sərgisi",
    "peşə hazırlığı",
    "peşə kursu",
    "ixtisas kursu",
    "vakansiya",
    # ── Müavinətlər / sosial dəstək ──
    "müavinət",                # uşaq/körpə/analıq/dəfn/işsizlik/birdəfəlik müavinəti, müavinətlər...
    "sosial yardım",           # "ünvanlı sosial yardım" da daxil
    "sosial dəstək",
    "uşaq pulu",
    # "analıq" tək söz kimi SAXLANILMIR — serial/film dialoqlarında adi mənada
    # ("analıq hissi", "analıq borcu" və s.) tez-tez keçir və yalançı tutma
    # yaradırdı. Yalnız konkret dövlət müavinəti/məzuniyyəti ifadələri saxlanılır.
    "analıq məzuniyyət",       # analıq məzuniyyəti/məzuniyyətinin
    "analıq müavinət",         # analıq müavinəti/müavinətinin
    "çoxuşaqlı ailə",
    "aztəminatlı ailə",
    # ── Şəhid ailələri / veteranlar ──
    "şəhid ailə",              # şəhid ailələri/ailəsinə
    "döyüşçü müavinət",
    "veteran",
    "müharibə iştirakçı",
    "qazi",
    # ── Xidmətlər / reabilitasiya / əlillik ──
    "əlillik",                 # əlilliyin təyini, əlillik dərəcəsi
    "reabilitasiya",
    "protez",
    "sanatoriya",
    "günərzi",
    # ── DOST proqramları ──
    "DOST mərkəz",
    "DOST İnklüziv",
    "DOST modeli",
    "Könüllü DOST",
    "Qarabağ DOST",
    "Şərqi Zəngəzur DOST",
    "inklüziv inkişaf",
    # ── Ailə / qayğı ──
    "himayədar ailə",
    "övladlığa",               # övladlığa götürmə
    "uşaq evi",
    # ── İslahat ──
    "sosial islahat",
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
# Hər modelin Groq-da AYRICA pulsuz kvota hovuzu var (saatda 7200
# audio-saniyə + gündə 2000 sorğu). transcribe.py sorğuları iki hovuz
# arasında növbələşdirir — birləşmiş tutum ikiqat olur. 1.5× audioda hər
# iki modelin keyfiyyəti real seqmentdə müqayisə edilib: fərq cüzidir.
GROQ_MODELS = ["whisper-large-v3", "whisper-large-v3-turbo"]

# Süjetin ƏƏSMN-ə aidiyyətini yoxlayan + başlıq yazan LLM (classify.py).
# 2026-10-16: Groq `llama-3.3-70b-versatile`-i götürdü — hər sorğu 404 aldı və
# tapılan bütün süjetlər səssizcə itdi. Model indi .env-dən dəyişdirilə bilir
# (GROQ_CLASSIFY_MODEL) ki, növbəti köhnəlmədə kod dəyişmədən keçid edilsin.
# Mövcud modellər: GET https://api.groq.com/openai/v1/models
CLASSIFY_MODEL = os.environ.get("GROQ_CLASSIFY_MODEL", "openai/gpt-oss-120b")

N8N_WEBHOOK_URL ="https://n8n.mediapulse.az/webhook/tv-clip-approval"

CLIP_SERVER_PORT = 8787
# Reachable via Caddy (reverse proxy + auto-HTTPS) on the VPS, routing
# tvclips.mediapulse.az -> localhost:8787.
PUBLIC_BASE_URL = "https://tvclips.mediapulse.az"

DJANGO_INGEST_URL = "https://mediapulse.az/landing-admin/tv-clips/ingest/"
DJANGO_INGEST_SECRET = os.environ.get("DJANGO_INGEST_SECRET", "")
