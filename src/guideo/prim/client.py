"""Minimal client for the PRIM "Calculateur IDFM – Accès générique (Navitia)" API.

Authentication: the key goes in an `apikey` HTTP header.
Coordinates are given to Navitia as "lon;lat" (longitude first!).
"""

from __future__ import annotations

from datetime import datetime

import httpx

NAVITIA_BASE = "https://prim.iledefrance-mobilites.fr/marketplace/v2/navitia"


class PrimError(RuntimeError):
    pass


class PrimClient:
    def __init__(self, api_key: str, timeout: float = 20.0):
        if not api_key:
            raise PrimError("PRIM_API_KEY is missing: put it in your .env file.")
        self._http = httpx.Client(
            base_url=NAVITIA_BASE,
            timeout=timeout,
            headers={"apikey": api_key, "Accept": "application/json"},
        )

    def get(self, path: str, **params) -> dict:
        r = self._http.get(path, params=params)
        if r.status_code in (401, 403):
            raise PrimError(f"PRIM refused the key (HTTP {r.status_code}). "
                            "Check it is valid and subscribed to this API.")
        r.raise_for_status()
        return r.json()

    def journeys(
        self,
        from_lonlat: tuple[float, float],
        to_lonlat: tuple[float, float],
        when: datetime | None = None,
        **params,
    ) -> dict:
        p = {
            "from": f"{from_lonlat[0]};{from_lonlat[1]}",
            "to": f"{to_lonlat[0]};{to_lonlat[1]}",
            **params,
        }
        if when is not None:
            p["datetime"] = when.strftime("%Y%m%dT%H%M%S")
        return self.get("/journeys", **p)

    def close(self) -> None:
        self._http.close()
