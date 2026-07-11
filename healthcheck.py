import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import requests

import config

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
STATE_FILE = config.BASE_DIR / "healthcheck_state.json"

RESTART_LOOP_THRESHOLD = 10  # "ffmpeg started" hits for one channel in the lookback window
LOOKBACK_MINUTES = 20
DISK_FREE_MIN_PERCENT = 10
EXPECTED_WEBHOOK_HOST = "n8n.mediapulse.az"


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


def check_disk_free_percent() -> float:
    total, used, free = shutil.disk_usage("/")
    return free / total * 100


def check_restart_loops() -> dict[str, int]:
    result = subprocess.run(
        ["journalctl", "-u", "tv-monitor", "--no-pager", f"--since=-{LOOKBACK_MINUTES}min"],
        capture_output=True, text=True,
    )
    counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        if "ffmpeg started" not in line:
            continue
        m = re.search(r"\[(\w+)\] ffmpeg started", line)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return {ch: c for ch, c in counts.items() if c >= RESTART_LOOP_THRESHOLD}


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


def collect_problems() -> list[str]:
    problems = []

    if not check_service_active():
        problems.append("tv-monitor.service AKTİV DEYİL")

    if not check_n8n_running():
        problems.append("n8n Docker konteyneri işləmir")

    free_pct = check_disk_free_percent()
    if free_pct < DISK_FREE_MIN_PERCENT:
        problems.append(f"Disk yeri azdır: cəmi {free_pct:.1f}% boş qalıb")

    for channel, count in check_restart_loops().items():
        problems.append(f"{channel}: son {LOOKBACK_MINUTES} dəqiqədə {count} dəfə restart oldu (bloklanmış ola bilər)")

    if not check_telegram_webhook_ok():
        problems.append("Telegram webhook VPS-ə deyil, başqa bir yerə qeydiyyatlıdır!")

    return problems


def main():
    problems = collect_problems()

    prev_state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {"problems": []}
    prev_problems = set(prev_state.get("problems", []))
    curr_problems = set(problems)

    new_problems = curr_problems - prev_problems
    resolved_problems = prev_problems - curr_problems

    if new_problems:
        send_telegram("⚠️ TV Monitor problemi:\n" + "\n".join(f"• {p}" for p in sorted(new_problems)))
    if resolved_problems:
        send_telegram("✅ Düzəldi:\n" + "\n".join(f"• {p}" for p in sorted(resolved_problems)))

    STATE_FILE.write_text(json.dumps({"problems": sorted(curr_problems)}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
