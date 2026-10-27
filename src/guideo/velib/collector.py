"""Long-running Vélib collector.

Run with:  python -m guideo.velib.collector
Stop with: Ctrl+C (the current poll finishes cleanly).
"""

from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler

import httpx

from guideo.config import load_settings

from .client import VelibClient
from .storage import VelibStore

INFO_REFRESH_SECONDS = 24 * 3600
MAX_BACKOFF_SECONDS = 600

log = logging.getLogger("guideo.velib")


def _setup_logging(log_dir) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(
        log_dir / "velib_collector.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    log.setLevel(logging.INFO)
    log.addHandler(file_handler)
    log.addHandler(console)


def run(poll_seconds: int, store: VelibStore, client: VelibClient) -> None:
    last_info = 0.0
    backoff = poll_seconds
    polls = 0

    while True:
        started = time.time()
        try:
            if started - last_info > INFO_REFRESH_SECONDS:
                info = client.fetch_info()
                store.save_info(info, int(started))
                last_info = started
                log.info("station_info refreshed: %d stations", len(info))

            feed_updated, statuses = client.fetch_status()
            n_changed = store.save_poll(int(started), feed_updated, statuses)
            polls += 1
            backoff = poll_seconds
            # One info line every 10 polls is enough to see it is alive.
            if polls % 10 == 1:
                log.info("poll ok: %d stations, %d changed", len(statuses), n_changed)
            sleep_for = max(0.0, poll_seconds - (time.time() - started))

        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("poll failed (%s: %s), retrying in %ds",
                        type(exc).__name__, exc, backoff)
            sleep_for = backoff
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

        time.sleep(sleep_for)


def main() -> None:
    settings = load_settings()
    _setup_logging(settings.log_dir)
    store = VelibStore(settings.velib_db_path)
    client = VelibClient()
    log.info("collector started, db=%s, every %ds",
             settings.velib_db_path, settings.velib_poll_seconds)
    try:
        run(settings.velib_poll_seconds, store, client)
    except KeyboardInterrupt:
        log.info("collector stopped by user")
    finally:
        client.close()
        store.close()


if __name__ == "__main__":
    main()
