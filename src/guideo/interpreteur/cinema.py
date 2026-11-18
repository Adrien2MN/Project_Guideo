"""Résolveur cinéma : trouver la vraie séance (cinéma + horaire) à partir de ce qui a été dit.

Sources de séances :
- SourceAllocine : séances réelles via la bibliothèque NON officielle allocine-seances
  (lecture du site : fragile, et réservé à un usage personnel) ;
- SourceFactice : séances inventées, pour tester sans réseau.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from rapidfuzz import fuzz

from guideo.memoire import Connaissances, Memoire, normaliser

from . import geo
from .analyse import Analyse

RAYONS_KM = (1.2, 2.5)    # rayon de recherche autour de la zone, élargi si le film ne passe pas près
TOLERANCE_HEURE_MIN = 10  # "22h" accepte les séances de 21h50 à 22h10
PUBS_MIN = 15             # publicités et bandes-annonces avant le film
SEUIL_TITRE = 75          # score minimal de correspondance du titre (0-100)
VERSIONS = {"ORIGINAL": "VO", "ORIGINAL_ST": "VOST", "DUBBED": "VF", "LOCAL": "VF"}

Demander = Callable[[str, list[str]], Optional[int]]


@dataclass
class Cinema:
    id: str
    nom: str
    adresse: str
    lat: Optional[float]
    lon: Optional[float]


@dataclass
class Seance:
    cinema: Cinema
    film: str
    duree_min: Optional[int]
    version: str
    debut: datetime
    film_id: Optional[str] = None   # identifiant du film dans la source, pour retrouver sa durée


@dataclass
class ChoixSeance:
    seance: Seance
    fin: datetime
    zone: str
    zone_pos: tuple[float, float]


def parser_runtime(valeur) -> Optional[int]:
    """Durée Allociné en minutes : accepte 6780 (secondes), 113 (minutes), '1h 53min', 'PT1H53M'."""
    if valeur in (None, "", 0):
        return None
    if isinstance(valeur, (int, float)):
        return int(valeur // 60) if valeur > 600 else int(valeur)
    texte = str(valeur).upper()
    h = re.search(r"(\d+)\s*H", texte)
    m = re.search(r"(\d+)\s*M", texte)
    total = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
    return total or (int(texte) // 60 if texte.isdigit() else None)


class SourceAllocine:
    """Séances réelles via allocine-seances (testé avec 0.0.14)."""

    # Paris et la petite couronne. Ajoute "Yvelines" etc. au besoin : seuls les manquants sont récupérés.
    EMPLACEMENTS = ("Paris", "Hauts-de-Seine", "Seine-Saint-Denis", "Val-de-Marne")

    def __init__(self, emplacements=EMPLACEMENTS, cache=Path("data/cinemas_cache.json")):
        from allocineAPI.allocineAPI import allocineAPI
        self.api = allocineAPI()
        self.emplacements = emplacements
        self.cache = cache
        self._cinemas: Optional[list[Cinema]] = None
        self._durees: dict[tuple[str, str], Optional[int]] = {}
        self.bloque = False  # Allociné refuse l'accès (403) : inutile d'insister cinéma par cinéma

    def _lire_cache(self) -> tuple[set[str], dict[str, Cinema]]:
        if not self.cache.exists():
            return set(), {}
        donnees = json.loads(self.cache.read_text(encoding="utf-8"))
        if isinstance(donnees, list):  # ancien format : liste de cinémas seule
            return set(), {c["id"]: Cinema(**c) for c in donnees}
        return set(donnees["emplacements"]), {c["id"]: Cinema(**c) for c in donnees["cinemas"]}

    def cinemas(self) -> list[Cinema]:
        if self._cinemas is not None:
            return self._cinemas
        faits, connus = self._lire_cache()
        manquants = [e for e in self.emplacements if normaliser(e) not in faits]
        if manquants:
            print(f"   [récupération des cinémas : {', '.join(manquants)} (une à deux minutes)...]")
            liste_allocine = list(self.api.get_top_villes()) + list(self.api.get_departements())
            for nom_cherche in manquants:
                n = normaliser(nom_cherche)
                trouves = [e for e in liste_allocine if normaliser(e["name"]) == n] or \
                          [e for e in liste_allocine if normaliser(e["name"]).startswith(n)]
                if not trouves:
                    proches = [e["name"] for e in liste_allocine if n[:4] in normaliser(e["name"])]
                    print(f"   [{nom_cherche!r} introuvable chez Allociné ; noms proches : {proches[:10]}]")
                    continue
                emplacement = trouves[0]
                avant = len(connus)
                for c in self.api.get_cinema(emplacement["id"]):
                    if c["id"] in connus:
                        continue
                    adresse = " ".join(c.get("address", "").split())
                    res = geo.candidats_ign(adresse, index=("address",), limite=1)
                    pos = (res[0]["lat"], res[0]["lon"]) if res else (None, None)
                    connus[c["id"]] = Cinema(id=c["id"], nom=c["name"], adresse=adresse, lat=pos[0], lon=pos[1])
                    time.sleep(0.05)
                faits.add(n)
                print(f"   [{emplacement['name']} ({emplacement['id']}) : {len(connus) - avant} nouveaux cinémas]")
            if connus:
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                self.cache.write_text(json.dumps(
                    {"emplacements": sorted(faits), "cinemas": [c.__dict__ for c in connus.values()]},
                    ensure_ascii=False, indent=1), encoding="utf-8")
            sans_pos = [c.nom for c in connus.values() if c.lat is None]
            print(f"   [{len(connus)} cinémas au total ; non géocodés : {sans_pos or 'aucun'}]")
        self._cinemas = list(connus.values())
        return self._cinemas

    def seances(self, cinema: Cinema, jour: date) -> list[Seance]:
        if self.bloque:
            return []
        try:
            films = self.api.get_showtime(cinema.id, jour.isoformat())
        except Exception as e:
            if "403" in str(e):
                self.bloque = True
                print("   [Allociné refuse l'accès automatique aux séances (erreur 403)]")
            else:
                print(f"   [séances indisponibles pour {cinema.nom} : {e}]")
            return []
        resultat = []
        for f in films:
            for s in f.get("showtimes", []):
                debut = datetime.fromisoformat(s["startsAt"])
                if debut.tzinfo is not None:
                    debut = debut.astimezone().replace(tzinfo=None)  # heure locale du PC
                version = VERSIONS.get(s.get("diffusionVersion"), s.get("diffusionVersion") or "?")
                resultat.append(Seance(cinema, f.get("title", "?"), None, version, debut))
        return resultat

    def duree(self, seance: Seance) -> Optional[int]:
        """Durée du film (minutes), demandée seulement pour la séance retenue."""
        cle = (seance.cinema.id, seance.film)
        if cle not in self._durees:
            try:
                for f in self.api.get_movies(seance.cinema.id, seance.debut.date().isoformat()):
                    self._durees[(seance.cinema.id, f.get("title"))] = parser_runtime(f.get("runtime"))
            except Exception as e:
                print(f"   [durée indisponible : {e}]")
        return self._durees.get(cle)


class SourceApiCinema:
    """Séances via API Cinéma (api-cinema.com), avec une clé personnelle (API_CINEMA_KEY dans .env).

    Le compte gratuit donne 100 requêtes par semaine : une recherche de séances coûte une requête
    pour toute la zone (plus une pour la durée du film), et les réponses sont gardées en cache.
    """

    PREFIXE = "apicinema"
    URL = "https://api.api-cinema.com/v1"
    VERSIONS = {"UNKNOWN": "?"}
    CACHE_SEANCES_H = 6        # les séances changent peu dans la journée
    CACHE_FILMS_J = 30

    def __init__(self, cle: str, cache_dir: Path = Path("data/cache/api_cinema")):
        if not cle:
            raise ValueError("clé API Cinéma manquante : ajoute API_CINEMA_KEY=... dans le fichier .env")
        self.cle = cle
        self.cache_dir = cache_dir
        self.bloque = False
        self._durees: dict[str, Optional[int]] = {}

    def _get(self, chemin: str, params: dict, duree_cache_s: float):
        import hashlib

        import httpx

        nom = hashlib.sha1(json.dumps([chemin, params], sort_keys=True).encode()).hexdigest()[:16]
        fichier = self.cache_dir / f"{nom}.json"
        if fichier.exists() and time.time() - fichier.stat().st_mtime < duree_cache_s:
            return json.loads(fichier.read_text(encoding="utf-8"))
        r = httpx.get(f"{self.URL}{chemin}", params=params, timeout=20,
                      headers={"Authorization": f"Bearer {self.cle}", "User-Agent": "Guideo/0.1 (projet personnel)"})
        reste = r.headers.get("X-Weekly-Quota-Remaining")
        if reste is not None:
            print(f"   [API Cinéma : {reste} requête(s) restante(s) cette semaine]")
        if r.status_code == 429:
            self.bloque = True
            print(f"   [API Cinéma : quota ou limite atteint(e) — {r.json().get('message', '')}]")
            return None
        if r.status_code in (401, 403):
            self.bloque = True
            print(f"   [API Cinéma refuse la requête ({r.status_code}) : vérifie la clé — {r.text[:200]}]")
            return None
        r.raise_for_status()
        donnees = r.json()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        fichier.write_text(json.dumps(donnees, ensure_ascii=False), encoding="utf-8")
        return donnees

    def seances_autour(self, pos: tuple[float, float], rayon_km: float, jour: date) -> list[Seance]:
        params = {"near": f"{pos[0]:.5f},{pos[1]:.5f}", "radius_km": rayon_km, "date": jour.isoformat(),
                  "include": "movie,cinema", "limit": 1000}
        seances, offset = [], 0
        while True:
            donnees = self._get("/showtimes", {**params, "offset": offset}, self.CACHE_SEANCES_H * 3600)
            if not donnees:
                break
            for s in donnees.get("data", []):
                if s.get("removedAt"):
                    continue  # séance annulée
                c, m = s.get("cinema") or {}, s.get("movie") or {}
                cinema = Cinema(id=str(c.get("id") or s.get("cinemaId")), nom=c.get("name", "?"),
                                adresse=", ".join(x for x in (c.get("address"), c.get("city")) if x),
                                lat=c.get("lat"), lon=c.get("lng"))
                duree = m.get("durationMin")
                if m.get("id") and duree:
                    self._durees[m["id"]] = duree
                version = self.VERSIONS.get(s.get("version"), s.get("version") or "?")
                seances.append(Seance(cinema, m.get("title") or "?", duree, version,
                                      datetime.fromisoformat(s["startsAt"][:16]), m.get("id") or s.get("movieId")))
            meta = donnees.get("meta", {})
            offset += len(donnees.get("data", []))
            if not donnees.get("data") or offset >= (meta.get("total") or 0):
                break
        print(f"   [API Cinéma : {len(seances)} séance(s) à moins de {rayon_km} km le {jour:%d/%m}]")
        return seances

    def duree(self, seance: Seance) -> Optional[int]:
        film_id = seance.film_id
        if not film_id:
            return None
        if film_id not in self._durees:
            donnees = self._get(f"/movies/{film_id}", {}, self.CACHE_FILMS_J * 86400)
            film = (donnees or {}).get("data", donnees) or {}
            self._durees[film_id] = film.get("durationMin") if isinstance(film, dict) else None
        return self._durees[film_id]

    def cinemas(self) -> list[Cinema]:
        return []  # les cinémas arrivent avec les séances (seances_autour)


class SourceFactice:
    """Séances inventées, pour tester la logique sans réseau. Tout est aujourd'hui."""

    ZONES = {"montparnasse": (48.8427, 2.3211)}

    def __init__(self):
        c1 = Cinema("F1", "Cinéma Factice Edgar-Quinet", "1 rue Factice, 75014 Paris", 48.8410, 2.3245)
        c2 = Cinema("F2", "Cinéma Factice Rennes", "2 rue Factice, 75006 Paris", 48.8445, 2.3260)
        c3 = Cinema("F3", "Cinéma Factice Vaugirard", "3 rue Factice, 75015 Paris", 48.8400, 2.3150)
        loin = Cinema("F4", "Cinéma Factice Bastille", "4 rue Factice, 75011 Paris", 48.8530, 2.3690)
        self._cinemas = [c1, c2, c3, loin]
        j = date.today()
        h = lambda hh, mm: datetime(j.year, j.month, j.day, hh, mm)
        self._seances = [
            Seance(c1, "Moulin", 118, "VF", h(19, 45)),
            Seance(c1, "Moulin", 118, "VO", h(22, 0)),
            Seance(c2, "Moulin", 118, "VO", h(22, 5)),
            Seance(c2, "Les Moulins de Paris", 95, "VF", h(20, 30)),
            Seance(c3, "Moulin", 118, "VF", h(20, 15)),
            Seance(loin, "Moulin", 118, "VF", h(22, 0)),
        ]

    def cinemas(self) -> list[Cinema]:
        return self._cinemas

    def duree(self, seance: Seance) -> Optional[int]:
        return seance.duree_min

    bloque = False

    def seances(self, cinema: Cinema, jour: date) -> list[Seance]:
        return [s for s in self._seances if s.cinema.id == cinema.id and s.debut.date() == jour]


def ref_cinema(source, cinema: Cinema) -> str:
    """Identifiant d'un cinéma dans sa source (ex. "apicinema:cnc_8422"), gardé dans la mémoire."""
    return f"{getattr(source, 'PREFIXE', 'allocine')}:{cinema.id}"


def localiser_zone(zone: str, memoire: Memoire, journal: Optional[Path] = None,
                   zones_factices: Optional[dict] = None) -> Optional[tuple[float, float]]:
    """Une zone connue de la mémoire d'abord ; sinon le géocodage, le plus probable près de chez soi."""
    connues = memoire.chercher(zone, types=("zones",))
    if connues:
        print(f"   [{zone!r} -> zone connue : {connues[0]['nom']}]")
        return connues[0]["lat"], connues[0]["lon"]
    if zones_factices is not None:
        return zones_factices.get(normaliser(zone))
    d = memoire.domicile
    reference = (d["lat"], d["lon"]) if d else geo.PARIS
    candidats = geo.classer(geo.candidats_ign(zone), reference)
    if not candidats:
        return None
    choisi = candidats[0]
    print(f"   [{zone!r} -> {choisi['nom']} ({choisi['km']:.1f} km de la référence) ; autres : "
          + "; ".join(f"{c['nom']} ({c['km']:.0f} km)" for c in candidats[1:4]) + "]")
    if journal:
        geo.journaliser(journal, zone, candidats, choisi, par="auto")
    return choisi["lat"], choisi["lon"]


def _dans_plage(d: datetime, plage: str, connaissances: Connaissances) -> bool:
    de, a = connaissances.bornes(plage)
    return de <= d.strftime("%H:%M") <= a


def resoudre_cinema(a: Analyse, source, memoire: Memoire, connaissances: Connaissances, maintenant: datetime,
                    demander: Demander, journal_geo: Optional[Path] = None,
                    demander_texte: Optional[Callable[[str], str]] = None) -> Optional[ChoixSeance]:
    if not a.film or not a.zone:
        print("   Il me faut le film et le quartier.")
        return None
    jour = date.fromisoformat(a.date) if a.date else maintenant.date()

    # Cinémas de la zone
    pos = localiser_zone(a.zone, memoire, journal_geo, getattr(source, "ZONES", None))
    if pos is None:
        print(f"   Je ne trouve pas où est {a.zone!r}.")
        return None
    if hasattr(source, "seances_autour"):
        # Une seule requête pour toute la zone (au plus grand rayon), filtrée ensuite par distance
        autour = [s for s in source.seances_autour(pos, RAYONS_KM[-1], jour) if s.debut >= maintenant]
        tous = {s.cinema.id: s.cinema for s in autour}
        par_cinema = lambda c, j: [s for s in autour if s.cinema.id == c.id]
    else:
        tous = {c.id: c for c in source.cinemas()}
        par_cinema = lambda c, j: [s for s in source.seances(c, j) if s.debut >= maintenant]
    distances = {c.id: geo.distance_km(*pos, c.lat, c.lon) for c in tous.values() if c.lat is not None}
    seances = []
    for rayon in RAYONS_KM:
        proches = sorted((i for i, d in distances.items() if d <= rayon), key=distances.get)
        if not proches:
            continue
        print(f"   [{len(proches)} cinéma(s) à moins de {rayon} km : "
              + ", ".join(f"{tous[i].nom} ({distances[i]:.1f} km)" for i in proches) + "]")
        seances = [s for i in proches for s in par_cinema(tous[i], jour)]
        if any(fuzz.token_set_ratio(normaliser(a.film), normaliser(s.film)) >= SEUIL_TITRE for s in seances):
            break  # le film passe près d'ici : inutile d'élargir
        if source.bloque:
            break
    if source.bloque:
        return _sans_seances(a, jour, source, tous, distances, memoire, connaissances, pos, demander, demander_texte)
    if not seances:
        print(f"   Aucune séance trouvée à moins de {RAYONS_KM[-1]} km de {a.zone} le {jour:%d/%m}.")
        return None

    # Film : correspondance approximative du titre
    demande = normaliser(a.film)
    titres = sorted({s.film for s in seances})
    exacts = [t for t in titres if normaliser(t) == demande]
    scores = {t: (fuzz.ratio(demande, normaliser(t)) + fuzz.token_set_ratio(demande, normaliser(t))) / 2
              for t in titres}
    meilleur = max(scores.values(), default=0)
    candidats = exacts or [t for t, s in scores.items() if s >= SEUIL_TITRE and s >= meilleur - 5]
    if not candidats:
        print(f"   Aucun film proche de {a.film!r} près de {a.zone} le {jour:%d/%m}.")
        print(f"   À l'affiche près d'ici : {', '.join(titres[:15])}")
        return None
    if len(candidats) > 1:
        i = demander("Quel film ?", candidats)
        if i is None:
            return None
        film = candidats[i]
    else:
        film = candidats[0]
    seances = [s for s in seances if s.film == film]

    # Horaire
    if a.heure_debut:
        cible = datetime.combine(jour, datetime.strptime(a.heure_debut, "%H:%M").time())
        filtrees = [s for s in seances if abs((s.debut - cible).total_seconds()) <= TOLERANCE_HEURE_MIN * 60]
        contrainte = f"vers {a.heure_debut}"
    elif a.plage:
        filtrees = [s for s in seances if _dans_plage(s.debut, a.plage, connaissances)]
        contrainte = f"le {a.plage}"
    else:
        filtrees, contrainte = seances, ""
    if not filtrees:
        print(f"   Pas de séance de {film} {contrainte} près de {a.zone}.")
        autres = sorted(seances, key=lambda s: s.debut)
        i = demander("Autres séances ce jour-là :", [f"{s.debut:%H:%M} — {s.cinema.nom} ({s.version})" for s in autres])
        if i is None:
            return None
        filtrees = [autres[i]]

    # Cinéma (les habituels d'abord, puis du plus proche au plus loin), puis séance
    def habitude(c: Cinema) -> int:
        lieu = memoire.lieu_par_ref(ref_cinema(source, c))
        return memoire.nb_observations(lieu["id"]) if lieu else 0

    filtrees.sort(key=lambda s: (-habitude(s.cinema), distances[s.cinema.id]))
    cinemas = list(dict.fromkeys(s.cinema.id for s in filtrees))
    if len(cinemas) > 1:
        options = []
        for cid in cinemas:
            c, n = tous[cid], habitude(tous[cid])
            horaires = ", ".join(f"{s.debut:%H:%M} {s.version}" for s in filtrees if s.cinema.id == cid)
            options.append(f"{c.nom} ({distances[cid]:.1f} km{f', déjà choisi {n} fois' if n else ''}) — {horaires}")
        i = demander(f"Plusieurs cinémas passent {film} {contrainte}. Lequel ?", options)
        if i is None:
            return None
        filtrees = [s for s in filtrees if s.cinema.id == cinemas[i]]

    filtrees.sort(key=lambda s: s.debut)
    if len(filtrees) > 1:
        i = demander("Quelle séance ?", [f"{s.debut:%H:%M} ({s.version})" for s in filtrees])
        if i is None:
            return None
        seance = filtrees[i]
    else:
        seance = filtrees[0]

    duree = seance.duree_min or source.duree(seance)
    if duree is None:
        duree = connaissances.duree_type("cinema") or 120
        print(f"   [durée du film inconnue : {duree} min par défaut]")
    seance.duree_min = duree
    return ChoixSeance(seance, seance.debut + timedelta(minutes=PUBS_MIN + duree), a.zone, pos)


def _sans_seances(a: Analyse, jour: date, source, tous: dict, distances: dict, memoire: Memoire,
                  connaissances: Connaissances, pos, demander: Demander,
                  demander_texte: Optional[Callable[[str], str]]) -> Optional[ChoixSeance]:
    """Mode dégradé quand les séances sont inaccessibles : l'utilisateur choisit le cinéma et donne l'heure."""
    if demander_texte is None:
        return None
    print("   Je ne peux pas lire les séances : choisis le cinéma et donne-moi l'heure de la séance.")

    def habitude(c: Cinema) -> int:
        lieu = memoire.lieu_par_ref(ref_cinema(source, c))
        return memoire.nb_observations(lieu["id"]) if lieu else 0

    proches = sorted((i for i, d in distances.items() if d <= RAYONS_KM[0]),
                     key=lambda i: (-habitude(tous[i]), distances[i]))[:8]
    if not proches:
        print("   Et je ne connais pas les cinémas de ce quartier : impossible de proposer une séance.")
        return None
    i = demander("Quel cinéma ?", [f"{tous[c].nom} ({distances[c]:.1f} km)" for c in proches])
    if i is None:
        return None
    cinema = tous[proches[i]]
    heure = a.heure_debut
    if heure is None:
        from .texte import heures_dites
        heure, _ = heures_dites(demander_texte("À quelle heure est la séance ?"))
        if heure is None:
            return None
    debut = datetime.combine(jour, datetime.strptime(heure, "%H:%M").time())
    duree = connaissances.duree_type("cinema") or 120
    print(f"   [durée du film inconnue : {duree} min par défaut, plus {PUBS_MIN} min de publicités]")
    seance = Seance(cinema, a.film, duree, "?", debut)
    return ChoixSeance(seance, debut + timedelta(minutes=PUBS_MIN + duree), a.zone, pos)
