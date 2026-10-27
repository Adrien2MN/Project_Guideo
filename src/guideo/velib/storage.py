"""SQLite storage for the Vélib history.

To keep the database small, we store an *event log*: a row is written for a
station only when its state changed since the previous poll. Polling all
~1,500 stations every minute would otherwise produce ~2 million rows a day.

The `polls` table records every successful poll. It is what lets you tell
"the station did not change" apart from "the collector was not running"
(your PC was off, no network...). Keep this in mind when you build features:
a gap in `polls` is missing data, not a stable station.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .client import StationInfo, StationStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS station_info (
    station_id   INTEGER PRIMARY KEY,
    station_code TEXT,
    name         TEXT,
    lat          REAL,
    lon          REAL,
    capacity     INTEGER,
    updated_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS status_events (
    station_id    INTEGER NOT NULL,
    observed_at   INTEGER NOT NULL,   -- poll time, UTC epoch seconds
    last_reported INTEGER,            -- station's own timestamp
    mechanical    INTEGER NOT NULL,
    ebike         INTEGER NOT NULL,
    docks         INTEGER NOT NULL,
    is_installed  INTEGER NOT NULL,
    is_renting    INTEGER NOT NULL,
    is_returning  INTEGER NOT NULL,
    PRIMARY KEY (station_id, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_events_time ON status_events (observed_at);

CREATE TABLE IF NOT EXISTS polls (
    observed_at       INTEGER PRIMARY KEY,
    feed_last_updated INTEGER,
    n_stations        INTEGER NOT NULL,
    n_changed         INTEGER NOT NULL
);
"""


class VelibStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._last: dict[int, tuple] = self._load_last_states()

    def _load_last_states(self) -> dict[int, tuple]:
        rows = self.conn.execute(
            """
            SELECT e.station_id, e.mechanical, e.ebike, e.docks,
                   e.is_installed, e.is_renting, e.is_returning
            FROM status_events e
            JOIN (SELECT station_id, MAX(observed_at) AS t
                  FROM status_events GROUP BY station_id) last
              ON e.station_id = last.station_id AND e.observed_at = last.t
            """
        ).fetchall()
        return {
            r[0]: (r[1], r[2], r[3], bool(r[4]), bool(r[5]), bool(r[6])) for r in rows
        }

    def save_poll(
        self,
        observed_at: int,
        feed_last_updated: int | None,
        statuses: list[StationStatus],
    ) -> int:
        """Store the stations whose state changed. Returns how many changed."""
        changed = [s for s in statuses if self._last.get(s.station_id) != s.state_key()]
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO status_events VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        s.station_id, observed_at, s.last_reported,
                        s.mechanical, s.ebike, s.docks,
                        int(s.is_installed), int(s.is_renting), int(s.is_returning),
                    )
                    for s in changed
                ],
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO polls VALUES (?,?,?,?)",
                (observed_at, feed_last_updated, len(statuses), len(changed)),
            )
        for s in changed:
            self._last[s.station_id] = s.state_key()
        return len(changed)

    def save_info(self, stations: list[StationInfo], now: int) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO station_info VALUES (?,?,?,?,?,?,?)",
                [
                    (s.station_id, s.station_code, s.name, s.lat, s.lon, s.capacity, now)
                    for s in stations
                ],
            )

    def close(self) -> None:
        self.conn.close()
