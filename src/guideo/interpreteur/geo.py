"""Géocodage : trouver les coordonnées d'une adresse, d'un quartier ou d'un lieu nommé.

- IGN Géoplateforme (sans clé) : adresses et toponymes (quartiers, gares...).
- Nominatim / OpenStreetMap : lieux nommés (une salle d'escalade, un restaurant...), que l'IGN
  ne connaît pas. Règles d'usage : au plus une requête par seconde, un User-Agent qui identifie l'appli.

Parmi les homonymes, le plus probable est celui qui a un bon score ET qui est proche de la
référence (le domicile) : "Montparnasse" désigne le quartier parisien, pas le lieu-dit du Loir-et-Cher.
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

PARIS = (48.8566, 2.3522)
DISTANCE_MAX_KM = 40   # au-delà, un résultat est jugé improbable (sauf s'il n'y a rien de plus proche)
USER_AGENT = "Guideo/0.1 (projet personnel, planificateur de trajets)"
_dernier_appel_nominatim = 0.0


def distance_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _get_json(url: str, entetes: Optional[dict] = None):
    req = urllib.request.Request(url, headers=entetes or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _premier(v):
    return (v[0] if v else "") if isinstance(v, list) else (v or "")


def candidats_ign(texte: str, index: tuple[str, ...] = ("address", "poi"), limite: int = 10) -> list[dict]:
    """Adresses et/ou points d'intérêt IGN correspondant au texte."""
    resultats = []
    for idx in index:
        params = urllib.parse.urlencode({"q": texte, "limit": limite, "index": idx})
        try:
            donnees = _get_json(f"https://data.geopf.fr/geocodage/search?{params}")
        except Exception as e:
            print(f"   [géocodage IGN ({idx}) impossible pour {texte!r} : {e}]")
            continue
        for f in donnees.get("features") or []:
            pr = f.get("properties", {})
            lon, lat = f["geometry"]["coordinates"][:2]
            nom = _premier(pr.get("label") or pr.get("name") or pr.get("toponym")) or "?"
            resultats.append({"nom": nom, "adresse": nom if idx == "address" else _premier(pr.get("city")),
                              "lat": lat, "lon": lon, "source": f"ign-{idx}",
                              "score": float(pr.get("score") or pr.get("_score") or 0.5)})
    return resultats


def candidats_nominatim(texte: str, limite: int = 8) -> list[dict]:
    """Lieux nommés OpenStreetMap, en privilégiant l'Île-de-France."""
    global _dernier_appel_nominatim
    attente = 1.0 - (time.monotonic() - _dernier_appel_nominatim)
    if attente > 0:
        time.sleep(attente)
    params = urllib.parse.urlencode({"q": texte, "format": "jsonv2", "limit": limite, "countrycodes": "fr",
                                     "viewbox": "1.45,49.25,3.55,48.10", "addressdetails": 0})
    try:
        donnees = _get_json(f"https://nominatim.openstreetmap.org/search?{params}", {"User-Agent": USER_AGENT})
    except Exception as e:
        print(f"   [recherche OpenStreetMap impossible pour {texte!r} : {e}]")
        return []
    finally:
        _dernier_appel_nominatim = time.monotonic()
    return [{"nom": d.get("name") or d["display_name"].split(",")[0], "adresse": d["display_name"],
             "lat": float(d["lat"]), "lon": float(d["lon"]), "source": "osm",
             "score": float(d.get("importance") or 0.3)} for d in donnees]


def classer(candidats: list[dict], reference: tuple[float, float]) -> list[dict]:
    """Du plus probable au moins probable : les proches (< 40 km) d'abord, par score puis distance."""
    for c in candidats:
        c["km"] = distance_km(*reference, c["lat"], c["lon"])
    vus, uniques = set(), []
    for c in candidats:  # même endroit à 50 m près = doublon
        cle = (round(c["lat"], 3), round(c["lon"], 3))
        if cle not in vus:
            vus.add(cle)
            uniques.append(c)
    return sorted(uniques, key=lambda c: (c["km"] > DISTANCE_MAX_KM, -round(c["score"], 1), c["km"]))


def journaliser(chemin: Path, requete: str, candidats: list[dict], choisi: Optional[dict], par: str) -> None:
    """Chaque choix est gardé : avec les corrections, c'est la base d'apprentissage du classement."""
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        with chemin.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"le": datetime.now().isoformat(timespec="seconds"), "requete": requete,
                                "candidats": candidats, "choisi": choisi and choisi["nom"], "par": par},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass
