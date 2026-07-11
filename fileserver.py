import functools
import http.server
import threading

import config


def start_file_server():
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(config.CLIPS_DIR)
    )
    # Bound to loopback only — Caddy reverse-proxies the public hostname to
    # this port, so it doesn't need to be reachable directly.
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", config.CLIP_SERVER_PORT), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    print(f"[fileserver] serving {config.CLIPS_DIR} on port {config.CLIP_SERVER_PORT}")
    return httpd
