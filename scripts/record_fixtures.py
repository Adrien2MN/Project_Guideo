"""Record real API responses for the reference trips (scripts/test_trips.toml).

For each trip it saves, in tests/fixtures/real/<trip name>/:
  - journeys_default.json   Navitia's usual answer (walking + public transport)
  - journeys_bss.json       Navitia allowed to use bike sharing at both ends
  - bike_direct.json        a direct bike route, with its geometry (for the slope work)
Plus one Vélib snapshot (station information + status) shared by all trips.

Usage:  python scripts/record_fixtures.py
"""

from __future__ import annotations

import json
import tomllib
from datetime import datetime
from pathlib import Path

import httpx

from guideo.config import load_settings
from guideo.prim.client import PrimClient, PrimError
from guideo.velib.client import INFO_URL, STATUS_URL

ROOT = Path(__file__).resolve().parent.parent
TRIPS_FILE = ROOT / "scripts" / "test_trips.toml"
OUT_DIR = ROOT / "tests" / "fixtures" / "real"

VARIANTS = {
    "journeys_default": {},
    "journeys_bss": {
        "first_section_mode[]": ["walking", "bss"],
        "last_section_mode[]": ["walking", "bss"],
    },
    "bike_direct": {
        "first_section_mode[]": ["bike"],
        "last_section_mode[]": ["bike"],
        "direct_path": "only",
    },
}


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    settings = load_settings()
    trips = tomllib.loads(TRIPS_FILE.read_text(encoding="utf-8"))["trip"]
    client = PrimClient(settings.prim_api_key or "")

    for trip in trips:
        when = datetime.fromisoformat(trip["when"]) if trip.get("when") else None
        print(f"\n{trip['name']}")
        for variant, params in VARIANTS.items():
            try:
                data = client.journeys(tuple(trip["from"]), tuple(trip["to"]), when, **params)
            except PrimError as exc:
                print(f"  {variant:<18} ERREUR : {exc}")
                continue
            except Exception as exc:  # keep going: one failing variant is still informative
                print(f"  {variant:<18} ERREUR : {type(exc).__name__}: {exc}")
                continue
            data["_guideo_request"] = {"trip": trip, "variant": variant,
                                       "params": params, "recorded_at": datetime.now().isoformat()}
            save(OUT_DIR / trip["name"] / f"{variant}.json", data)
            durations = [j.get("duration", 0) // 60 for j in data.get("journeys", [])]
            print(f"  {variant:<18} {len(durations)} itinéraire(s), durées {durations} min")

    # One Vélib snapshot, taken at the same time as the journeys
    for name, url in (("velib_station_information", INFO_URL), ("velib_station_status", STATUS_URL)):
        r = httpx.get(url, timeout=20)
        r.raise_for_status()
        save(OUT_DIR / f"{name}.json", r.json())
    print("\nInstantané Vélib enregistré.")
    print(f"Tout est dans : {OUT_DIR}")
    client.close()


if __name__ == "__main__":
    main()
