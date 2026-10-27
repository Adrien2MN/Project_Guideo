"""Client for the Vélib Métropole GBFS feed (no API key needed).

The feed distinguishes mechanical (green) and electric (blue) bikes through
`num_bikes_available_types`, a list of single-key dicts such as
[{"mechanical": 3}, {"ebike": 1}].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole"
STATUS_URL = f"{BASE_URL}/station_status.json"
INFO_URL = f"{BASE_URL}/station_information.json"


@dataclass(frozen=True)
class StationInfo:
    station_id: int
    station_code: str
    name: str
    lat: float
    lon: float
    capacity: int


@dataclass(frozen=True)
class StationStatus:
    station_id: int
    mechanical: int
    ebike: int
    docks: int
    is_installed: bool
    is_renting: bool
    is_returning: bool
    last_reported: int | None

    def state_key(self) -> tuple:
        """What matters to detect a change (last_reported alone is not a change)."""
        return (
            self.mechanical,
            self.ebike,
            self.docks,
            self.is_installed,
            self.is_renting,
            self.is_returning,
        )


def _bike_types(raw: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                for key, value in item.items():
                    counts[key] = counts.get(key, 0) + int(value or 0)
    elif isinstance(raw, dict):
        counts = {k: int(v or 0) for k, v in raw.items()}
    return counts


def parse_status(payload: dict) -> tuple[int | None, list[StationStatus]]:
    """Return (feed last_updated epoch, list of station statuses)."""
    stations = []
    for s in payload.get("data", {}).get("stations", []):
        types = _bike_types(s.get("num_bikes_available_types"))
        stations.append(
            StationStatus(
                station_id=int(s["station_id"]),
                mechanical=types.get("mechanical", 0),
                ebike=types.get("ebike", 0),
                docks=int(s.get("num_docks_available", s.get("numDocksAvailable", 0)) or 0),
                is_installed=bool(s.get("is_installed", 1)),
                is_renting=bool(s.get("is_renting", 1)),
                is_returning=bool(s.get("is_returning", 1)),
                last_reported=s.get("last_reported"),
            )
        )
    last_updated = payload.get("last_updated") or payload.get("lastUpdatedOther")
    return (int(last_updated) if last_updated else None), stations


def parse_info(payload: dict) -> list[StationInfo]:
    return [
        StationInfo(
            station_id=int(s["station_id"]),
            station_code=str(s.get("stationCode", "")),
            name=s.get("name", ""),
            lat=float(s["lat"]),
            lon=float(s["lon"]),
            capacity=int(s.get("capacity", 0) or 0),
        )
        for s in payload.get("data", {}).get("stations", [])
    ]


class VelibClient:
    def __init__(self, timeout: float = 15.0):
        self._http = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "guideo/0.1 (portfolio project)"},
        )

    def fetch_status(self) -> tuple[int | None, list[StationStatus]]:
        r = self._http.get(STATUS_URL)
        r.raise_for_status()
        return parse_status(r.json())

    def fetch_info(self) -> list[StationInfo]:
        r = self._http.get(INFO_URL)
        r.raise_for_status()
        return parse_info(r.json())

    def close(self) -> None:
        self._http.close()
