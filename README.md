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

## n8n tərəfi (eyni VPS, eyni n8n konteyneri)

n8n iki domenlə açılır (Caddy → `localhost:5678`): `n8n.mediapulse.az` və
`n8n.prdostu.com`. n8n-in qlobal `WEBHOOK_URL`-i **prdostu**-dadır (digər layihələr
üçün), amma **TV axınının hamısı `n8n.mediapulse.az` üzərindən işləyir**:

| Workflow | Giriş nöqtəsi | Kim çağırır |
|---|---|---|
| `TV Sujet İzləmə - Təsdiq və YouTube Yükləmə` | `POST /webhook/tv-clip-approval` | `watcher.py` |
| `TV Klip - Telegram Təsdiq Emalı` | `POST /webhook/tv-clip-telegram-callback` | Telegram (`tv_monitorinq_bot` düymələri) |
| `TV Klip - Telegram Təsdiq Emalı` | `POST /webhook/tv-clip-web-decision` | Django (`saas_core/monitor/views.py`) |

**Telegram trigger node-u qəsdən İŞLƏDİLMİR** (2026-10-16): n8n trigger-i botu
qlobal `WEBHOOK_URL`-ə (prdostu) bağlayırdı. Əvəzində adi Webhook node-u var,
bot isə əl ilə bağlanıb:

```bash
# /root/tv-monitor/.env: TELEGRAM_BOT_TOKEN, TG_CALLBACK_SECRET
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=https://n8n.mediapulse.az/webhook/tv-clip-telegram-callback" \
  --data-urlencode "secret_token=${TG_CALLBACK_SECRET}" \
  --data-urlencode 'allowed_updates=["callback_query"]'
```

Webhook node-u yalnız `X-Telegram-Bot-Api-Secret-Token` başlığı
`TG_CALLBACK_SECRET`-ə bərabər olanda icra yaradır (`onlyRunIf`). Eyni bota başqa
workflow-da Telegram trigger qoşma — bir botun yalnız bir webhook-u olur və TV
düymələri səssizcə işləməz olur (2026-09-də "Mağaza Telegram Agenti" məhz bunu
etmişdi, arxivləndi).

## Servis və healthcheck (serverdə)

- `tv-monitor.service` — `enable` edilib (reboot-dan sonra özü qalxır) +
  `/etc/systemd/system/tv-monitor.service.d/restart.conf`-da `Restart=always`
  (proses təmiz çıxsa belə yenidən qalxır; `systemctl stop` hələ də işləyir).
- Healthcheck cron-u **`/etc/cron.d/tv-monitor`**-dadır (`*/5`), root crontab-da
  deyil — başqa layihənin `crontab fayl` əmri onu silə bilməsin.

> **2026-08-11 → 2026-10-16 hadisəsi:** proses təmiz çıxdı (kod 0), `Restart=on-failure`
> qaldırmadı, servis `disabled` idi; healthcheck yalnız bir dəfə xəbərdarlıq edib
> susdu, 2026-10-12-də isə `ai_trader` quraşdırılarkən root crontab üstündən yazılıb
> healthcheck sətri itdi. ~2 ay heç bir kanal izlənmədi. Yuxarıdakı iki dəyişiklik
> bunun təkrarlanmaması üçündür.

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
| `test_tv_monitor.py` | Şəbəkəsiz testlər: `venv/bin/python -m unittest test_tv_monitor -v` |

## Mühit dəyişənləri

`.env.example` faylına bax. Real `.env` serverdədir və **repo-ya əlavə edilmir**.

## Qurулuş (serverdə)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt   # + ffmpeg, yt-dlp sistemdə quraşdırılmalıdır
python run.py
```
