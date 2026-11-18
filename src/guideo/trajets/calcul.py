"""Calcul d'un trajet entre deux points, pour arriver à l'heure ou pour partir à une heure donnée.

Sources, de la plus réaliste à la plus grossière :
- PRIM (Navitia IDFM) : transports en commun + marche, avec les vrais horaires ;
- les trajets hybrides (`hybride.py`) : Vélib pour rejoindre ou quitter ces transports ;
- le réseau des rues de Guideo (si les graphes sont construits) : vélo et marche, pente comprise ;
- une estimation à vol d'oiseau, quand rien d'autre ne répond (signalée comme telle).
Parmi les possibilités, on garde la meilleure selon la fonction de coût (`cout.py` :
durée + pénalités de correspondance et de risque Vélib) et on cite les autres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

DETOUR = 1.3              # un trajet réel est ~30 % plus long que la ligne droite
MARCHE_KMH = 4.8
SEULEMENT_A_PIED_KM = 0.8  # en dessous, on marche : inutile d'interroger PRIM
ATTENTE_TC_MIN = 8         # accès + attente moyenne, pour l'estimation en transports
TC_KMH = 20


@dataclass
class Point:
    id: str
    nom: str
    lat: float
    lon: float

    def en_dict(self) -> dict:
        return {"id": self.id, "nom": self.nom, "lat": self.lat, "lon": self.lon}

    @classmethod
    def depuis(cls, d: Optional[dict]) -> Optional["Point"]:
        return cls(**d) if d else None


@dataclass
class Trajet:
    origine: Point
    destination: Point
    depart: datetime
    arrivee: datetime
    mode: str                     # transports | velo | marche | estimation
    resume: str                   # ex. "marche 4 min → RER C → marche 6 min"
    source: str                   # prim | rues | estimation
    alternatives: list[str] = field(default_factory=list)
    note: Optional[str] = None    # ex. "tu repasses chez toi (1 h 10 à la maison)"

    @property
    def duree_min(self) -> int:
        return round((self.arrivee - self.depart).total_seconds() / 60)

    @property
    def id(self) -> str:
        return f"{self.origine.id}>{self.destination.id}@{self.depart:%Y%m%dT%H%M}"

    def ligne(self) -> str:
        texte = (f"départ {self.depart:%H:%M} de {self.origine.nom} → arrivée {self.arrivee:%H:%M} "
                 f"({self.duree_min} min : {self.resume})")
        if self.alternatives:
            texte += f" ; autres : {', '.join(self.alternatives)}"
        return texte

    def en_dict(self) -> dict:
        return {"origine": self.origine.en_dict(), "destination": self.destination.en_dict(),
                "depart": self.depart.isoformat(timespec="minutes"), "arrivee": self.arrivee.isoformat(timespec="minutes"),
                "mode": self.mode, "resume": self.resume, "source": self.source,
                "alternatives": self.alternatives, "note": self.note}

    @classmethod
    def depuis(cls, d: Optional[dict]) -> Optional["Trajet"]:
        if not d:
            return None
        return cls(Point.depuis(d["origine"]), Point.depuis(d["destination"]), datetime.fromisoformat(d["depart"]),
                   datetime.fromisoformat(d["arrivee"]), d["mode"], d["resume"], d["source"],
                   d.get("alternatives", []), d.get("note"))


def distance_km(a: Point, b: Point) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


@dataclass
class _Option:
    mode: str
    source: str
    resume: str
    duree_s: Optional[float] = None       # trajets sans horaires (rues, estimation)
    depart: Optional[datetime] = None     # trajets à horaires (PRIM)
    arrivee: Optional[datetime] = None
    correspondances: int = 0              # changements entre lignes de transport
    avec_velib: bool = False
    proba_velib: Optional[float] = None   # vélo au départ ET place à l'arrivée (None : inconnu)
    velo_min: float = 0.0                 # minutes de pédalage (effort)

    def placer(self, heure: datetime, arriver: bool) -> tuple[datetime, datetime]:
        if self.depart and self.arrivee:
            return self.depart, self.arrivee
        d = timedelta(minutes=math.ceil(self.duree_s / 60))  # à la minute : on arrondit la durée vers le haut
        return (heure - d, heure) if arriver else (heure, heure + d)

    def court(self, heure: datetime, arriver: bool) -> str:
        dep, arr = self.placer(heure, arriver)
        return f"{self.mode} {round((arr - dep).total_seconds() / 60)} min"


def _resume_prim(journey: dict) -> str:
    morceaux = []
    for s in journey.get("sections", []):
        t = s.get("type")
        if t == "public_transport":
            info = s.get("display_informations", {})
            morceaux.append(f"{info.get('commercial_mode', '')} {info.get('code', '')}".strip())
        elif t in ("street_network", "crow_fly") and s.get("duration", 0) >= 60:
            mode = s.get("mode", "walking")
            nom = {"walking": "marche", "bike": "vélo", "bss": "Vélib"}.get(mode, mode)
            morceaux.append(f"{nom} {round(s['duration'] / 60)} min")
    return " → ".join(morceaux) or "marche"


def _date_navitia(texte: str) -> datetime:
    return datetime.strptime(texte, "%Y%m%dT%H%M%S")


class Calculateur:
    """Calcule des trajets. Chaque source est facultative ; le cache évite de refaire un même calcul."""

    def __init__(self, prim=None, rues_velo=None, rues_marche=None, stations=None,
                 hybrides: bool = True, parametres_hybride=None, parametres_cout=None):
        from .cout import ParametresCout
        self.prim = prim              # guideo.prim.client.PrimClient
        self.rues_velo = rues_velo    # guideo.streets.router.StreetRouter (graphe vélo)
        self.rues_marche = rues_marche
        self.stations = stations      # guideo.velib.stations.StationsVelib (facultatif)
        self.cout = parametres_cout or ParametresCout()
        self._hybrideur = None
        if rues_velo is not None:
            from .hybride import Hybrideur
            self._hybrideur = Hybrideur(rues_velo, stations, parametres_hybride)
        self._hybrides = hybrides
        self._cache: dict = {}
        self._cache_prim: dict = {}
        self._prim_hs = False

    # ------------------------------------------------------------------ sources
    def _journeys_prim(self, o: Point, d: Point, heure: datetime, arriver: bool) -> list[dict]:
        """Réponse brute de PRIM (mise en cache : les hybrides la réutilisent)."""
        if self.prim is None or self._prim_hs:
            return []
        cle = (o.id, d.id, heure.isoformat(timespec="minutes"), arriver)
        if cle not in self._cache_prim:
            try:
                donnees = self.prim.journeys((o.lon, o.lat), (d.lon, d.lat), heure,
                                             datetime_represents="arrival" if arriver else "departure")
            except Exception as e:  # clé refusée, réseau...
                print(f"   [PRIM indisponible ({e}) : trajets estimés sans les transports en commun]")
                self._prim_hs = True
                return []
            self._cache_prim[cle] = donnees.get("journeys", [])
        return self._cache_prim[cle]

    def _options_prim(self, o: Point, d: Point, heure: datetime, arriver: bool) -> list[_Option]:
        options = []
        for j in self._journeys_prim(o, d, heure, arriver):
            try:
                dep, arr = _date_navitia(j["departure_date_time"]), _date_navitia(j["arrival_date_time"])
            except (KeyError, ValueError):
                continue
            if (arriver and arr > heure) or (not arriver and dep < heure):
                continue
            n_tc = sum(s.get("type") == "public_transport" for s in j.get("sections", []))
            options.append(_Option("transports" if n_tc else "marche", "prim", _resume_prim(j),
                                   depart=dep, arrivee=arr,
                                   correspondances=int(j.get("nb_transfers", max(n_tc - 1, 0)))))
        return options

    def _options_hybrides(self, o: Point, d: Point, heure: datetime, arriver: bool) -> list[_Option]:
        if not self._hybrides or self._hybrideur is None:
            return []
        try:
            return self._hybrideur.options(self._journeys_prim(o, d, heure, arriver), o, d, heure, arriver)
        except Exception as e:  # un hybride raté ne doit jamais empêcher de proposer un trajet
            print(f"   [trajets hybrides impossibles : {e}]")
            return []

    def _options_rues(self, o: Point, d: Point, heure: datetime) -> list[_Option]:
        options = []
        from guideo.streets import speed
        if self.rues_velo is not None:
            jambe = self._hybrideur.jambe_velib((o.lon, o.lat), (d.lon, d.lat), heure)
            if jambe is not None:
                resume = f"Vélib {round(jambe.velo_s / 60)} min"
                if jambe.station_depart is not None:
                    resume += f" ({jambe.station_depart.nom} → {jambe.station_arrivee.nom})"
                if not jambe.verifiee:
                    resume += " (stations non vérifiées)"
                options.append(_Option("velo", "rues", resume, duree_s=jambe.duree_s, avec_velib=True,
                                       proba_velib=jambe.proba, velo_min=jambe.velo_s / 60))
        if self.rues_marche is not None:
            try:
                r = self.rues_marche.route((o.lon, o.lat), (d.lon, d.lat), speed.WALK)
                options.append(_Option("marche", "rues", f"marche {r.distance_m / 1000:.1f} km", duree_s=r.duration_s))
            except Exception as e:
                print(f"   [calcul marche impossible : {e}]")
        return options

    @staticmethod
    def _options_estimees(o: Point, d: Point) -> list[_Option]:
        km = distance_km(o, d) * DETOUR
        options = [_Option("marche", "estimation", f"marche ~{km:.1f} km (estimation)", duree_s=km / MARCHE_KMH * 3600)]
        if km > SEULEMENT_A_PIED_KM:
            options.append(_Option("transports", "estimation", "transports (estimation, sans horaires)",
                                   duree_s=(ATTENTE_TC_MIN * 60) + km / TC_KMH * 3600))
        return options

    # ------------------------------------------------------------------ calcul
    def _calculer(self, o: Point, d: Point, heure: datetime, arriver: bool) -> Optional[Trajet]:
        if o.id == d.id or distance_km(o, d) < 0.05:
            return None  # même endroit : pas de trajet
        cle = (o.id, d.id, heure.isoformat(timespec="minutes"), arriver)
        if cle in self._cache:
            return self._cache[cle]
        options = []
        if distance_km(o, d) * DETOUR > SEULEMENT_A_PIED_KM:
            options += self._options_prim(o, d, heure, arriver)
            options += self._options_hybrides(o, d, heure, arriver)
        options += self._options_rues(o, d, heure)
        if not options:
            options = self._options_estimees(o, d)
        elif not any(opt.mode == "marche" for opt in options) and distance_km(o, d) * DETOUR <= 2:
            options += [opt for opt in self._options_estimees(o, d) if opt.mode == "marche"]

        from .cout import penalite_min

        def cle_tri(opt: _Option):
            dep, arr = opt.placer(heure, arriver)
            pen = penalite_min(opt.correspondances, opt.proba_velib, opt.avec_velib, self.cout, opt.velo_min) * 60
            # partir le plus tard / arriver le plus tôt, pénalités comprises
            return -(dep.timestamp() - pen) if arriver else arr.timestamp() + pen

        options.sort(key=cle_tri)
        meilleure = options[0]
        dep, arr = meilleure.placer(heure, arriver)
        # Une seule alternative par mode (la meilleure de chaque), sans répéter celui retenu
        autres, modes_vus = [], {meilleure.mode}
        for opt in options[1:]:
            if opt.mode not in modes_vus:
                modes_vus.add(opt.mode)
                autres.append(opt.court(heure, arriver))
        trajet = Trajet(o, d, dep, arr, meilleure.mode, meilleure.resume, meilleure.source, autres[:3])
        self._cache[cle] = trajet
        return trajet

    def arriver(self, o: Point, d: Point, heure: datetime) -> Optional[Trajet]:
        """Le trajet qui part le plus tard possible en arrivant au plus tard à `heure`."""
        return self._calculer(o, d, heure, arriver=True)

    def partir(self, o: Point, d: Point, heure: datetime) -> Optional[Trajet]:
        """Le trajet qui arrive le plus tôt possible en partant à `heure` ou après."""
        return self._calculer(o, d, heure, arriver=False)


def stations_par_defaut(settings):
    """Positions des stations Vélib (base du collecteur, sinon flux en ligne) + état en temps réel."""
    from guideo.velib.stations import StationsVelib
    statut = StationsVelib.statut_temps_reel()
    try:
        stations = StationsVelib.depuis_base(settings.velib_db_path, statut=statut)
        return stations or StationsVelib.depuis_flux(statut=statut)
    except Exception as e:
        print(f"   [stations Vélib inconnues ({e}) : temps de Vélib forfaitaires]")
        return None


def calculateur_par_defaut(settings) -> Calculateur:
    """PRIM si la clé est là, graphes des rues s'ils sont construits, stations Vélib, sinon estimations."""
    prim = None
    if settings.prim_api_key:
        from guideo.prim.client import PrimClient
        prim = PrimClient(settings.prim_api_key)
    rues = {}
    for nom in ("bike", "walk"):
        chemin = settings.streets_dir / f"{nom}.pkl"
        if chemin.exists():
            try:
                from guideo.streets.build import load_graph
                from guideo.streets.router import StreetRouter
                rues[nom] = StreetRouter(load_graph(chemin))
            except Exception as e:
                print(f"   [graphe {chemin.name} illisible : {e}]")
    stations = stations_par_defaut(settings) if rues.get("bike") is not None else None
    return Calculateur(prim, rues.get("bike"), rues.get("walk"), stations=stations)
