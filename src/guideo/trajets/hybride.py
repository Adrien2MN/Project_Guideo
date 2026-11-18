"""Trajets hybrides : un Vélib pour rejoindre ou quitter les transports en commun.

Idée : Navitia propose des trajets en transports, avec la liste de TOUS les arrêts de
chaque tronçon et leurs horaires. Plutôt que de suivre son trajet depuis le début, on
peut :
- **premier kilomètre** : rejoindre en Vélib n'importe quel arrêt d'un tronçon (le
  sien ou un suivant), puis monter dans le même train à cet arrêt. Ex. Henri-Martin →
  Issy : Vélib jusqu'à Javel puis RER C, au lieu de C + changement à Champ de Mars ;
- **dernier kilomètre** : descendre à n'importe quel arrêt d'un tronçon et finir en
  Vélib. Ex. retour d'Issy : descendre à Champ de Mars et finir en Vélib, au lieu
  d'attendre la correspondance.

On réutilise les horaires que Navitia a déjà renvoyés (le même train, à un autre
arrêt) : aucun appel PRIM supplémentaire. Limite : on ne découvre que les lignes que
Navitia a proposées.

Chaque possibilité devient une `_Option` du calculateur ; la fonction de coût
(`cout.py`) les départage avec les trajets purs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from guideo.streets import speed
from guideo.streets.router import haversine_m

from .calcul import _Option, _date_navitia, _resume_prim


@dataclass
class ParametresHybride:
    velo_min_km: float = 0.6            # en dessous, la marche suffit
    velo_max_km: float = 5.0            # au-delà, le Vélib n'est plus un « petit » tronçon
    rayon_station_m: float = 450        # stations cherchées autour d'un point
    prise_min: float = 1.0              # décrocher le vélo
    depose_min: float = 1.0             # le raccrocher
    marge_min: float = 1.0              # sur le quai avant le passage
    acces_quai_min: dict = field(default_factory=lambda: {
        "RER": 3.0, "Train": 3.0, "Transilien": 3.0, "Métro": 2.0, "Tramway": 1.0, "Bus": 0.5})
    acces_defaut_min: float = 2.0
    candidats_max: int = 6              # par côté, calculés exactement après un pré-tri
    detour: float = 1.3                 # marche réelle / ligne droite
    velo_estime_kmh: float = 14.0       # pour le pré-tri seulement
    forfait_sans_station_min: float = 4.0  # si on ne connaît pas les stations
    profil_velo: object = speed.MECHANICAL
    marche_kmh: float = speed.WALK.flat_kmh  # ta vitesse de marche (mesurée : ~6,1 km/h)


@dataclass
class JambeVelib:
    """Aller d'un point à un autre en Vélib : marche → station → vélo → station → marche."""
    duree_s: float
    velo_s: float
    station_depart: Optional[object] = None
    station_arrivee: Optional[object] = None
    proba: Optional[float] = None       # tout se passe bien côté Vélib (None : inconnu)

    @property
    def verifiee(self) -> bool:
        return self.proba is not None


@dataclass
class _Candidat:
    debut: bool                 # True : premier kilomètre ; False : dernier
    journey: dict
    k: int                      # index du tronçon de transport dans journey["sections"]
    arret_nom: str
    arret_lonlat: tuple[float, float]
    heure: datetime             # passage du train à cet arrêt (départ ou arrivée)
    mode_tc: str
    score: float = 0.0


def _arret(sd: dict) -> tuple[str, tuple[float, float]]:
    sp = sd["stop_point"]
    return sp.get("name", "?"), (float(sp["coord"]["lon"]), float(sp["coord"]["lat"]))


def _minute_inf(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0)


def _minute_sup(t: datetime) -> datetime:
    return _minute_inf(t) + timedelta(minutes=1) if (t.second or t.microsecond) else t


class Hybrideur:
    def __init__(self, rues_velo, stations=None, parametres: Optional[ParametresHybride] = None):
        self.rues_velo = rues_velo          # StreetRouter du graphe vélo
        self.stations = stations            # guideo.velib.stations.StationsVelib, ou None
        self.p = parametres or ParametresHybride()
        self._velo_cache: dict = {}

    # ------------------------------------------------------------------ briques
    def _marche_s(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        return float(haversine_m(*a, *b)) * self.p.detour / (self.p.marche_kmh / 3.6)

    def _velo_s(self, a: tuple[float, float], b: tuple[float, float], cle=None) -> Optional[float]:
        cle = cle or (round(a[0], 5), round(a[1], 5), round(b[0], 5), round(b[1], 5))
        cle = (*cle, self.p.profil_velo.name)
        if cle not in self._velo_cache:
            try:
                self._velo_cache[cle] = self.rues_velo.route(a, b, self.p.profil_velo).duration_s
            except Exception:
                self._velo_cache[cle] = None
        return self._velo_cache[cle]

    def _choisir_station(self, point, quand: datetime, besoin: str):
        """La station la plus proche qui n'est pas connue comme vide (ou pleine)."""
        for station, _ in self.stations.proches(*point, self.p.rayon_station_m, n=4):
            proba = self.stations.disponible(station, quand, besoin)
            if proba is None or proba > 0:
                return station, proba
        return None, None

    def jambe_velib(self, a: tuple[float, float], b: tuple[float, float], quand: datetime) -> Optional[JambeVelib]:
        """Durée d'un trajet en Vélib de `a` à `b` (lon, lat), stations comprises."""
        if self.stations is None:
            velo = self._velo_s(a, b)
            if velo is None:
                return None
            return JambeVelib(velo + self.p.forfait_sans_station_min * 60, velo)
        sa, pa = self._choisir_station(a, quand, "velo")
        if sa is None:
            return None
        sb, pb = self._choisir_station(b, quand, "place")
        if sb is None or sb.id == sa.id:
            return None
        velo = self._velo_s((sa.lon, sa.lat), (sb.lon, sb.lat), cle=(sa.id, sb.id))
        if velo is None:
            return None
        duree = (self._marche_s(a, (sa.lon, sa.lat)) + self.p.prise_min * 60 + velo
                 + self.p.depose_min * 60 + self._marche_s((sb.lon, sb.lat), b))
        proba = None if pa is None or pb is None else pa * pb
        return JambeVelib(duree, velo, sa, sb, proba)

    def _acces_s(self, mode_tc: str) -> float:
        return self.p.acces_quai_min.get(mode_tc, self.p.acces_defaut_min) * 60

    # ------------------------------------------------------------------ candidats
    def _candidats(self, journeys: list[dict], o, d, heure: datetime, arriver: bool) -> list[_Candidat]:
        p = self.p
        fixe_s = (p.prise_min + p.depose_min + p.marge_min) * 60
        meilleurs: dict = {}
        for j in journeys:
            secs = j.get("sections", [])
            troncons = [k for k, s in enumerate(secs) if s.get("type") == "public_transport"]
            if not troncons:
                continue
            try:
                dep_j, arr_j = _date_navitia(j["departure_date_time"]), _date_navitia(j["arrival_date_time"])
            except (KeyError, ValueError):
                continue
            for k in troncons:
                sdts = secs[k].get("stop_date_times", [])
                mode_tc = secs[k].get("display_informations", {}).get("commercial_mode", "")
                for i, sd in enumerate(sdts):
                    try:
                        nom, pos = _arret(sd)
                    except (KeyError, ValueError, TypeError):
                        continue
                    for debut in (True, False):
                        if (debut and i == len(sdts) - 1) or (not debut and i == 0):
                            continue  # on monte avant le terminus du tronçon, on descend après son départ
                        bout = (o.lon, o.lat) if debut else (d.lon, d.lat)
                        km = float(haversine_m(*bout, *pos)) / 1000
                        if not p.velo_min_km <= km <= p.velo_max_km:
                            continue
                        try:
                            t = _date_navitia(sd["departure_date_time" if debut else "arrival_date_time"])
                        except (KeyError, ValueError):
                            continue
                        estime = km * p.detour / p.velo_estime_kmh * 3600 + fixe_s + self._acces_s(mode_tc)
                        dep, arr = (t - timedelta(seconds=estime), arr_j) if debut else (dep_j, t + timedelta(seconds=estime))
                        if (arriver and arr > heure) or (not arriver and dep < heure):
                            continue
                        score = -dep.timestamp() if arriver else arr.timestamp()
                        cle = (debut, sd["stop_point"].get("id", nom), t)
                        if cle not in meilleurs or score < meilleurs[cle].score:
                            meilleurs[cle] = _Candidat(debut, j, k, nom, pos, t, mode_tc, score)
        tries = sorted(meilleurs.values(), key=lambda c: c.score)
        return ([c for c in tries if c.debut][:p.candidats_max]
                + [c for c in tries if not c.debut][:p.candidats_max])

    # ------------------------------------------------------------------ options
    def options(self, journeys: list[dict], o, d, heure: datetime, arriver: bool) -> list[_Option]:
        if self.rues_velo is None or not journeys:
            return []
        options = []
        for c in self._candidats(journeys, o, d, heure, arriver):
            secs = c.journey["sections"]
            arr_j = _date_navitia(c.journey["arrival_date_time"])
            dep_j = _date_navitia(c.journey["departure_date_time"])
            if c.debut:
                quand = c.heure - timedelta(minutes=20)
                jambe = self.jambe_velib((o.lon, o.lat), c.arret_lonlat, quand)
                if jambe is None:
                    continue
                avant = jambe.duree_s + self._acces_s(c.mode_tc) + self.p.marge_min * 60
                dep, arr = _minute_inf(c.heure - timedelta(seconds=avant)), arr_j
                suite = secs[c.k:]
                tc = sum(s.get("type") == "public_transport" for s in suite)
                resume = f"Vélib {round(jambe.velo_s / 60)} min jusqu'à {c.arret_nom} → {_resume_prim({'sections': suite})}"
            else:
                jambe = self.jambe_velib(c.arret_lonlat, (d.lon, d.lat), c.heure)
                if jambe is None:
                    continue
                apres = self._acces_s(c.mode_tc) + jambe.duree_s
                dep, arr = dep_j, _minute_sup(c.heure + timedelta(seconds=apres))
                debut_tc = secs[:c.k + 1]
                tc = sum(s.get("type") == "public_transport" for s in debut_tc)
                deb = _resume_prim({"sections": debut_tc})
                resume = f"{deb} jusqu'à {c.arret_nom} → Vélib {round(jambe.velo_s / 60)} min"
            if (arriver and arr > heure) or (not arriver and dep < heure):
                continue
            if not jambe.verifiee:
                resume += " (stations non vérifiées)"
            options.append(_Option("hybride", "hybride", resume, depart=dep, arrivee=arr,
                                   correspondances=max(tc - 1, 0), avec_velib=True, proba_velib=jambe.proba,
                                   velo_min=jambe.velo_s / 60))
        return options
