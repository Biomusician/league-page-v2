"""One-click launcher for the Commissioner's Desk.

Started by "Launch Commissioner Desk.cmd" in the repo root. Behavior:
  1. If a healthy Desk already answers on 8026, just open the browser.
  2. Otherwise pick 8026 (or the first free nearby port if a foreign
     program holds it), start the server IN THIS WINDOW, and open the
     browser once /health responds.
Closing this window stops the server; that is the intended lifecycle
(no orphan processes holding the port). Logs: logs/desk-startup.log.
Set LEAGUEPAGE_NO_BROWSER=1 to suppress the browser (used by tests).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

LOG_PATH = REPO / "logs" / "desk-startup.log"


def _open_when_healthy(port: int, log: logging.Logger) -> None:
    from leaguepage.desk import probe_health

    url = f"http://localhost:{port}/commissioner"
    for _ in range(60):
        time.sleep(0.5)
        if probe_health(port):
            log.info("Desk is ready: %s", url)
            print(f"\n  Commissioner's Desk is READY: {url}\n"
                  f"  Keep this window open while you work; closing it stops the Desk.\n",
                  flush=True)
            if not os.environ.get("LEAGUEPAGE_NO_BROWSER"):
                webbrowser.open(url)
            return
    log.error("Server never became healthy on port %s", port)
    print(f"\n  WARNING: the Desk did not answer on {url} within 30 seconds.\n"
          f"  See {LOG_PATH} for details.\n", flush=True)


def main() -> int:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("launcher")
    log.info("---- launch %s ----", dt.datetime.now().isoformat(timespec="seconds"))

    try:
        import uvicorn

        from leaguepage.desk import DEFAULT_PORT, create_app, pick_port, probe_health

        preferred = int(os.environ.get("LEAGUEPAGE_DESK_PORT", DEFAULT_PORT))
        port, situation = pick_port(preferred)
        if situation == "already-running":
            health = probe_health(port) or {}
            url = f"http://localhost:{port}/commissioner"
            log.info("Desk already running on %s (season %s); opening browser only.",
                     port, health.get("season"))
            print(f"\n  Commissioner's Desk is already running: {url}\n"
                  f"  Opening your browser. This window will close.\n", flush=True)
            if not os.environ.get("LEAGUEPAGE_NO_BROWSER"):
                webbrowser.open(url)
            time.sleep(2)
            return 0
        if situation == "fallback":
            log.warning("Port %s busy (not a Desk); using %s.", DEFAULT_PORT, port)
            print(f"  NOTE: port {DEFAULT_PORT} is in use by another program; "
                  f"the Desk will use {port} instead.", flush=True)

        threading.Thread(target=_open_when_healthy, args=(port, log), daemon=True).start()
        print(f"  Starting the Commissioner's Desk on http://localhost:{port} ...", flush=True)
        # reload=True: when League-Page code is updated (e.g. by a Claude Code
        # session) while the Desk is open, the server restarts itself with the
        # new routes. Without this, hot-loaded templates can post to routes an
        # old process never registered (the publish-start 404, 2026-08-31).
        # Watch ONLY the package dir: editorial/, data/ and logs/ churn must
        # never bounce the server.
        uvicorn.run("leaguepage.desk:create_app", factory=True,
                    host="127.0.0.1", port=port,
                    reload=True, reload_dirs=[str(REPO / "leaguepage")],
                    log_level="info", access_log=False, log_config=None)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        log.exception("Desk failed to start")
        print("\n  Commissioner's Desk failed to start.\n"
              f"  Error: {type(exc).__name__}: {exc}\n"
              f"  Full log: {LOG_PATH}\n", flush=True)
        try:
            input("  Press Enter to close...")
        except EOFError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
