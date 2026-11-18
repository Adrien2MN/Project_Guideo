"""Vélib stations: where they are, which are close to a point, and whether they have bikes/docks.

Positions come from the collector's database (table `station_info`) or from the live feed.
Availability is only known for trips starting soon (live feed, refreshed at most once a
minute); further ahead it is unknown (None), and the cost function treats it as a risk.
This is the place to plug the availability predictor later (`predicteur`).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from guideo.streets.router import haversine_m

LIVE_HORIZON_MIN = 20     # au-delà, l'état actuel d'une station ne dit plus grand-chose
STATUS_MAX_AGE_S = 60


@dataclass(frozen=True)
class Station:
    id: int
    nom: str
    lat: float
    lon: float
    capacite: int = 0


class StationsVelib:
    def __init__(self, stations: list[Station], statut: Optional[Callable[[], dict]] = None,
                 predicteur: Optional[Callable] = None, maintenant: Callable[[], datetime] = datetime.now):
        """`statut()` renvoie {station_id: (mecaniques, electriques, places)} (flux temps réel).
        `predicteur(station_id, quand, besoin)` renverrait une probabilité (futur prédicteur)."""
        self.stations = stations
        self._lon = np.array([s.lon for s in stations])
        self._lat = np.array([s.lat for s in stations])
        self._statut_source = statut
        self._statut: dict = {}
        self._statut_t = 0.0
        self.predicteur = predicteur
        self.maintenant = maintenant

    # ------------------------------------------------------------------ construction
    @classmethod
    def depuis_base(cls, chemin: Path, **kw) -> Optional["StationsVelib"]:
        """Positions lues dans la base du collecteur (aucun appel réseau)."""
        if not chemin.exists():
            return None
        with sqlite3.connect(chemin) as conn:
            rows = conn.execute("SELECT station_id, name, lat, lon, capacity FROM station_info").fetchall()
        return cls([Station(int(i), n or "", float(la), float(lo), int(c or 0)) for i, n, la, lo, c in rows], **kw) if rows else None

    @classmethod
    def depuis_flux(cls, client=None, **kw) -> "StationsVelib":
        from guideo.velib.client import VelibClient
        client = client or VelibClient()
        infos = client.fetch_info()
        return cls([Station(s.station_id, s.name, s.lat, s.lon, s.capacity) for s in infos], **kw)

    @staticmethod
    def statut_temps_reel(client=None) -> Callable[[], dict]:
        """Source d'état pour le constructeur : interroge le flux Vélib."""
        from guideo.velib.client import VelibClient
        client = client or VelibClient()

        def lire() -> dict:
            _, statuses = client.fetch_status()
            return {s.station_id: (s.mechanical, s.ebike, s.docks if s.is_returning else 0)
                    for s in statuses if s.is_installed and s.is_renting}
        return lire

    # ------------------------------------------------------------------ recherche
    def proches(self, lon: float, lat: float, rayon_m: float, n: int = 3) -> list[tuple[Station, float]]:
        """Les `n` stations les plus proches à moins de `rayon_m`, avec leur distance (m)."""
        if not self.stations:
            return []
        d = haversine_m(lon, lat, self._lon, self._lat)
        idx = np.argsort(d)[:n]
        return [(self.stations[i], float(d[i])) for i in idx if d[i] <= rayon_m]

    # ------------------------------------------------------------------ disponibilité
    def _etat(self) -> dict:
        if self._statut_source and time.time() - self._statut_t > STATUS_MAX_AGE_S:
            try:
                self._statut = self._statut_source()
            except Exception as e:  # réseau...
                print(f"   [état des stations Vélib indisponible : {e}]")
                self._statut = {}
            self._statut_t = time.time()
        return self._statut

    def disponible(self, station: Station, quand: datetime, besoin: str) -> Optional[float]:
        """Probabilité d'avoir un vélo (`besoin="velo"`) ou une place (`"place"`) à `quand`.

        1.0 / 0.0 si on peut lire l'état actuel (départ imminent), la prédiction si un
        prédicteur est branché, sinon None (inconnu).
        """
        ecart_min = abs((quand - self.maintenant()).total_seconds()) / 60
        if ecart_min <= LIVE_HORIZON_MIN:
            etat = self._etat().get(station.id)
            if etat is not None:
                meca, elec, places = etat
                return float((meca + elec) > 0) if besoin == "velo" else float(places > 0)
        if self.predicteur is not None:
            try:
                return self.predicteur(station.id, quand, besoin)
            except Exception:
                return None
        return None
