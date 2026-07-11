import threading
import time

import config
import fileserver
import recorder
import watcher

stop_event = threading.Event()


def main():
    if not config.GROQ_API_KEY:
        raise SystemExit(
            "GROQ_API_KEY environment variable is not set. "
            "Set it before running, e.g.:  $env:GROQ_API_KEY = 'your-key-here'"
        )

    fileserver.start_file_server()
    print(f"[run] Public clip URL base: {config.PUBLIC_BASE_URL}")
    print("[run] Make sure the 'tvclips-mediapulse' cloudflared tunnel is running separately.")

    threads = []
    for channel, url in config.CHANNELS.items():
        t_rec = threading.Thread(
            target=recorder.supervise, args=(channel, url, stop_event), daemon=True
        )
        t_watch = threading.Thread(
            target=watcher.watch_channel, args=(channel, stop_event), daemon=True
        )
        t_rec.start()
        t_watch.start()
        threads.extend([t_rec, t_watch])

    print(f"[run] Monitoring {len(config.CHANNELS)} channels for keywords: {config.KEYWORDS}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[run] Shutting down...")
        stop_event.set()


if __name__ == "__main__":
    main()
