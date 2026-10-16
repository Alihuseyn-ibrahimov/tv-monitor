import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import requests

import config

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
STATE_FILE = config.BASE_DIR / "healthcheck_state.json"

RESTART_LOOP_THRESHOLD = 10  # "ffmpeg started" hits for one channel in the lookback window
LOOKBACK_MINUTES = 20
DISK_FREE_MIN_PERCENT = 10
# 2026-07-16: disk 100% dolub servis 2 gün tamamilə iflic olmuşdu, çünki bu
# fayl yalnız xəbərdarlıq göndərirdi, özü heç nə etmirdi. Bu kritik həddən
# aşağı düşəndə segments/ (emal edilməmiş xam qeydlər — itirilsə də funksional
# ziyanı yoxdur, sadəcə köhnəlmiş məzmundur) avtomatik təmizlənir ki, bir daha
# günlərlə iflic vəziyyəti yaranmasın.
DISK_EMERGENCY_FREE_PERCENT = 5
EXPECTED_WEBHOOK_HOST = "n8n.mediapulse.az"
# Kvota (429) xətaları watcher-də təkrar cəhd olunur və bura düşmür; 3+ uğursuz
# klassifikasiya adətən daimi problemdir (model silinib, açar etibarsızdır).
CLASSIFY_FAILURE_THRESHOLD = 3


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[healthcheck] TELEGRAM_BOT_TOKEN/CHAT_ID not set, would have sent: {text}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"[healthcheck] failed to send Telegram alert: {e}")


def check_service_active() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "tv-monitor"], capture_output=True, text=True
    )
    return result.stdout.strip() == "active"


def check_n8n_running() -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", "n8n"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() == "true"


def check_n8n_healthy() -> bool:
    """Container "Running" olması kifayət deyil — 2026-07-16/17 hadisəsində
    postgres SIGABRT ilə çöküb recovery-də ilişmişdi, n8n konteyneri "Up"
    görünürdü, amma tətbiq 20 saat 503 qaytarırdı (heç bir klip təsdiqi/
    yükləməsi işləmədi). Ona görə HTTP səviyyəsində /healthz yoxlanılır."""
    try:
        resp = requests.get("http://localhost:5678/healthz", timeout=10)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def restart_n8n_stack() -> bool:
    """Restarts postgres first (n8n's dependency), then n8n, then re-checks
    health. Waits are generous because postgres crash recovery can take a
    while; the cron interval (5 min) tolerates it."""
    subprocess.run(["docker", "restart", "n8n-postgres"], capture_output=True, timeout=120)
    time.sleep(15)
    subprocess.run(["docker", "restart", "n8n"], capture_output=True, timeout=120)
    time.sleep(30)
    return check_n8n_healthy()


def check_disk_free_percent() -> float:
    total, used, free = shutil.disk_usage("/")
    return free / total * 100


def emergency_cleanup_segments() -> int:
    """Deletes all raw (unprocessed) recording chunks in SEGMENTS_DIR. These
    are intermediate files only — watcher.py normally deletes each one right
    after processing it, so anything still here either finished its useful
    life (news this stale is no longer worth transcribing) or is stuck behind
    a failure. Returns bytes freed."""
    freed = 0
    for path in config.SEGMENTS_DIR.rglob("*"):
        if path.is_file():
            try:
                freed += path.stat().st_size
                path.unlink()
            except OSError:
                pass
    return freed


def _journal_since(minutes: int) -> str:
    result = subprocess.run(
        ["journalctl", "-u", "tv-monitor", "--no-pager", f"--since=-{minutes}min"],
        capture_output=True, text=True,
    )
    return result.stdout


def count_classify_failures(log_text: str) -> int:
    return sum(1 for line in log_text.splitlines() if "AI relevance check failed" in line)


def check_classify_failures() -> int:
    """2026-10-16: classify.py-nin modeli Groq-da silinmişdi — hər açar söz
    uyğunluğu 404 ilə uğursuz olub seqment silinirdi, yəni bütün süjetlər
    itirdi, amma heç bir xəbərdarlıq yox idi (servis "sağlam" görünürdü).
    Qayıdan dəyər: LOOKBACK_MINUTES ərzində uğursuz klassifikasiya sayı."""
    return count_classify_failures(_journal_since(LOOKBACK_MINUTES))


def check_restart_loops() -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in _journal_since(LOOKBACK_MINUTES).splitlines():
        if "ffmpeg started" not in line:
            continue
        m = re.search(r"\[(\w+)\] ffmpeg started", line)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return {ch: c for ch, c in counts.items() if c >= RESTART_LOOP_THRESHOLD}


def check_dead_channels() -> dict[str, float]:
    """Returns {channel: minutes_since_last_heartbeat} for channels whose
    watch_channel thread has gone silent for longer than
    config.HEARTBEAT_STALE_SECONDS. A missing heartbeat file (channel never
    started, or the whole heartbeats/ dir was wiped) counts as infinitely
    stale so it's never missed."""
    stale: dict[str, float] = {}
    now = time.time()
    for channel in config.CHANNELS:
        hb_path = config.HEARTBEAT_DIR / f"{channel}.hb"
        try:
            age_seconds = now - float(hb_path.read_text())
        except (OSError, ValueError):
            age_seconds = float("inf")
        if age_seconds > config.HEARTBEAT_STALE_SECONDS:
            stale[channel] = age_seconds / 60
    return stale


def restart_service() -> bool:
    result = subprocess.run(["systemctl", "restart", "tv-monitor"], capture_output=True, text=True)
    return result.returncode == 0


def check_telegram_webhook_ok() -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return True  # nothing to check
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo", timeout=15
        )
        url = resp.json().get("result", {}).get("url", "")
        return EXPECTED_WEBHOOK_HOST in url
    except requests.RequestException:
        return True  # don't false-alarm on a transient network blip while checking


def collect_problems() -> dict[str, str]:
    """Returns {stable_key: display_message}. The key must stay constant
    while a problem is ongoing even if details in the message (like a
    restart count) fluctuate — otherwise every fluctuation reads as
    "resolved" + a brand new problem, even though nothing actually changed."""
    problems: dict[str, str] = {}

    if not check_service_active():
        problems["service_down"] = "tv-monitor.service AKTİV DEYİL"

    if not check_n8n_running():
        problems["n8n_down"] = "n8n Docker konteyneri işləmir"
    elif not check_n8n_healthy():
        recovered = restart_n8n_stack()
        problems["n8n_unhealthy"] = (
            "n8n cavab vermirdi (konteyner işləyir, tətbiq xəstə) — "
            f"postgres+n8n avtomatik restart edildi: {'✅ bərpa oldu' if recovered else '❌ HƏLƏ DƏ XƏSTƏ, əl ilə yoxla'}"
        )

    free_pct = check_disk_free_percent()
    if free_pct < DISK_EMERGENCY_FREE_PERCENT:
        freed = emergency_cleanup_segments()
        free_pct = check_disk_free_percent()
        problems["disk_low"] = (
            f"Disk kritik həddə idi, avtomatik təmizləndi: "
            f"{freed / (1024**3):.1f} GB azad edildi (indi {free_pct:.1f}% boş)"
        )
    elif free_pct < DISK_FREE_MIN_PERCENT:
        problems["disk_low"] = f"Disk yeri azdır: cəmi {free_pct:.1f}% boş qalıb"

    for channel, count in check_restart_loops().items():
        problems[f"restart_loop:{channel}"] = f"{channel}: son {LOOKBACK_MINUTES} dəqiqədə {count} dəfə restart oldu (bloklanmış ola bilər)"

    dead = check_dead_channels()
    if dead and len(dead) == len(config.CHANNELS):
        # HAMISI eyni anda susub — bu tək bir kanalın yayımının kəsilməsi
        # deyil (bu, per-channel problem olardı), bütöv prosesin daxilən
        # donmasıdır (2026-07-16 hadisəsi məhz bu idi: systemd "active"
        # görürdü, amma heç bir thread irəliləmirdi). systemd
        # `Restart=on-failure` bunu HEÇ VAXT aşkarlaya bilməz, çünki proses
        # çıxmır — ona görə məcburi restart burada edilir.
        restarted = restart_service()
        problems["process_frozen"] = (
            f"Bütün {len(dead)} kanal donmuşdu (heartbeat {config.HEARTBEAT_STALE_SECONDS//60} "
            f"dəq-dən köhnə) — servis avtomatik restart edildi: {'✅ uğurlu' if restarted else '❌ UĞURSUZ, əl ilə yoxla'}"
        )
    else:
        for channel, minutes in dead.items():
            problems[f"channel_dead:{channel}"] = f"{channel}: {minutes:.0f} dəqiqədir heç bir fəaliyyət yoxdur"

    classify_failures = check_classify_failures()
    if classify_failures >= CLASSIFY_FAILURE_THRESHOLD:
        problems["classify_failing"] = (
            f"AI klassifikasiyası son {LOOKBACK_MINUTES} dəqiqədə {classify_failures} dəfə "
            f"uğursuz oldu — tapılan süjetlər İTİR (model: {config.CLASSIFY_MODEL}; "
            "journalctl -u tv-monitor | grep 'AI relevance')"
        )

    if not check_telegram_webhook_ok():
        problems["webhook_wrong"] = "Telegram webhook VPS-ə deyil, başqa bir yerə qeydiyyatlıdır!"

    return problems


def main():
    problems = collect_problems()

    try:
        prev_state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {"problems": {}}
    except json.JSONDecodeError:
        # Boş/pozulmuş fayl (məs. disk tam dolarkən yazı yarımçıq kəsilib) —
        # healthcheck-in özünü çökdürməsinə qoymadan sıfırdan başla.
        prev_state = {"problems": {}}
    prev_problems = prev_state.get("problems", {})
    if isinstance(prev_problems, list):  # köhnə format (sadə mətn siyahısı) ilə geriyə uyğunluq
        prev_problems = {p: p for p in prev_problems}

    new_keys = set(problems) - set(prev_problems)
    resolved_keys = set(prev_problems) - set(problems)

    if new_keys:
        send_telegram("⚠️ TV Monitor problemi:\n" + "\n".join(f"• {problems[k]}" for k in sorted(new_keys)))
    if resolved_keys:
        send_telegram("✅ Düzəldi:\n" + "\n".join(f"• {prev_problems[k]}" for k in sorted(resolved_keys)))

    STATE_FILE.write_text(json.dumps({"problems": problems}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
