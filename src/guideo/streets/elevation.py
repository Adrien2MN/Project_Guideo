"""Elevations from the IGN Géoplateforme altimetry API (free, no key).

Results are cached by OSM node id in a small SQLite file, so the (slow) download
only happens once, and a second graph (walk vs bike) reuses the shared nodes.
The service allows 5 requests per second per IP; we stay below.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import httpx

ELEVATION_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
RESOURCE = "ign_rge_alti_wld"


class ElevationError(RuntimeError):
    pass


def _parse_elevations(payload: dict) -> list[float | None]:
    """Accept both answer shapes: plain numbers (zonly) or {"z": ...} objects."""
    values = []
    for e in payload.get("elevations", []):
        z = e if isinstance(e, (int, float)) else (e or {}).get("z")
        values.append(None if z is None or z <= -1000 else float(z))  # -99999 = no data
    return values


class IgnElevationClient:
    def __init__(self, cache_path: Path, batch_size: int = 100, max_rps: float = 4.0):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cache_path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS elevation (node_id INTEGER PRIMARY KEY, z REAL)"
        )
        self.batch_size = batch_size
        self.min_interval = 1.0 / max_rps
        self._last_call = 0.0
        self._http = httpx.Client(timeout=30, headers={"User-Agent": "guideo/0.1"})

    def query(self, lonlats: list[tuple[float, float]]) -> list[float | None]:
        """One API call for a list of (lon, lat) points."""
        params = {
            "lon": "|".join(f"{lon:.6f}" for lon, _ in lonlats),
            "lat": "|".join(f"{lat:.6f}" for _, lat in lonlats),
            "resource": RESOURCE,
            "delimiter": "|",
            "zonly": "true",
            "indent": "false",
            "measures": "false",
        }
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                r = self._http.get(ELEVATION_URL, params=params)
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                r.raise_for_status()
                values = _parse_elevations(r.json())
                if len(values) != len(lonlats):
                    raise ElevationError(
                        f"asked {len(lonlats)} points, got {len(values)}: {r.text[:200]}")
                return values
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if attempt == 3:
                    raise ElevationError(f"IGN API failed: {exc}") from exc
                time.sleep(2 ** attempt)
        raise AssertionError("unreachable")

    def elevations(self, points: dict[int, tuple[float, float]],
                   progress: bool = True) -> dict[int, float | None]:
        """Elevation of each node id, from the cache or the API."""
        cached = dict(self.db.execute("SELECT node_id, z FROM elevation"))
        missing = [n for n in points if n not in cached]
        for i in range(0, len(missing), self.batch_size):
            batch = missing[i:i + self.batch_size]
            values = self.query([points[n] for n in batch])
            with self.db:
                self.db.executemany("INSERT OR REPLACE INTO elevation VALUES (?, ?)",
                                    list(zip(batch, values)))
            cached.update(zip(batch, values))
            if progress and (i // self.batch_size) % 20 == 0:
                print(f"  altitudes : {min(i + self.batch_size, len(missing))}/{len(missing)}")
        return {n: cached.get(n) for n in points}

    def close(self) -> None:
        self._http.close()
        self.db.close()
