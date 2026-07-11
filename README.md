# TV Monitorinq → YouTube Pipeline

Azərbaycan TV kanallarını 7/24 izləyən, açar sözlərə uyğun süjet tapanda klip kəsib
Telegram-da təsdiq alaraq YouTube-a yükləyən sistem.

> ⚠️ **Bu qovluq serverdə işləyən kodun repo nüsxəsidir.** İşlək sistem ayrı bir
> **Hetzner VPS-də** (`root@91.99.149.63:/root/tv-monitor/`) `tv-monitor.service`
> (systemd) altında işləyir. Buradakı fayllar versiya nəzarəti + backup üçündür;
> dəyişiklik serverə `scp` ilə köçürülüb servis restart edilməlidir.

## Axın

```
recorder.py   → hər kanalı HLS ilə qeydə alır (yalnız config.NEWS_HOURS saatlarında
                + off-hours saatda 1 dəfə qısa nümunə), segments/ qovluğuna .mp4 yazır
watcher.py    → hazır seqmentləri izləyir → transcribe → classify → uyğun olsa clip
transcribe.py → Groq Whisper API ilə audionu mətnə çevirir
classify.py   → config.KEYWORDS siyahısına uyğunluğu yoxlayır
clipper.py    → uyğun seqmentdən klip kəsir (clips/)
fileserver.py → klipi lokal 8787 portunda açır (Caddy → tvclips.mediapulse.az)
watcher.py    → əvvəl Django-ya ingest edir (POST /landing-admin/tv-clips/ingest/,
                pk alır), sonra n8n webhook-una djangoClipId ilə göndərir
```

Təsdiq/yükləmə n8n workflow-larında baş verir (Hetzner-də self-hosted n8n, Docker) —
o hissə bu repo-da deyil.

## Fayllar

| Fayl | Rol |
|------|-----|
| `run.py` | Giriş nöqtəsi — bütün kanallar üçün recorder+watcher thread-lərini qaldırır |
| `config.py` | Kanallar (HLS), açar sözlər, qeyd saatları, env açarları |
| `recorder.py` | ffmpeg ilə HLS qeydi + off-hours nümunələmə |
| `watcher.py` | Seqment izləmə → transkripsiya → təsnifat → klip → ingest/webhook |
| `transcribe.py` | Groq Whisper transkripsiyası |
| `classify.py` | Açar söz uyğunluğu |
| `clipper.py` | Klip kəsimi |
| `fileserver.py` | Klipləri HTTP ilə açan kiçik server |
| `story.py` | Süjet (mətn) emalı |
| `healthcheck.py` | Sistem sağlamlığı + Telegram xəbərdarlıq |
| `run_healthcheck.sh` | Healthcheck üçün cron wrapper |

## Mühit dəyişənləri

`.env.example` faylına bax. Real `.env` serverdədir və **repo-ya əlavə edilmir**.

## Qurулuş (serverdə)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt   # + ffmpeg, yt-dlp sistemdə quraşdırılmalıdır
python run.py
```
