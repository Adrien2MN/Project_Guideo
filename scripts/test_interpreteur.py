"""Test de l'interpréteur Guideo : phrase -> événement, avec Ollama en local.

Principe :
- le LLM COMPREND la phrase (activité, lieu, film, date, heure...) ;
- le CODE RÉSOUT : mémoire perso pour l'escalade ou les soirées, vraies séances
  pour le cinéma, et pose les questions quand plusieurs réponses sont vraies.

Prérequis (dans le venv du projet) :
    pip install ollama pydantic rapidfuzz "allocine-seances>=0.0.14"

Utilisation :
    python scripts/test_interpreteur.py                 # phrases de test, vraies séances Allociné
    python scripts/test_interpreteur.py --faux          # séances factices (test de la logique, sans réseau)
    python scripts/test_interpreteur.py -p "je vais voir Moulin ce soir à Montparnasse"
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Literal, Optional

import ollama
from pydantic import BaseModel, Field, field_validator
from rapidfuzz import fuzz

MODELE = "ministral-3:8b-instruct-2512-q4_K_M"

RAYONS_KM = (1.2, 2.5)    # rayon de recherche autour de la zone, élargi si rien de proche
TOLERANCE_HEURE_MIN = 10  # "22h" accepte les séances de 21h50 à 22h10
PUBS_MIN = 15             # publicités et bandes-annonces avant le film
SEUIL_TITRE = 75         # score minimal de correspondance du titre (0-100)
PLAGES = {                # plages horaires pour "ce matin", "ce soir"...
    "matin": ("06:00", "12:00"),
    "midi": ("11:30", "14:30"),
    "apres-midi": ("12:00", "18:00"),
    "soir": ("18:00", "23:59"),
    "nuit": ("21:00", "23:59"),
}

# --- Mémoire personnelle (écrite à la main pour l'instant) --------------------
MEMOIRE = {
    # Coordonnées approximatives de l'avenue Henri-Martin : mets ton adresse exacte.
    "domicile": {"id": "domicile", "nom": "Domicile (rue Henri Martin)", "lat": 48.8640, "lon": 2.2760},
    "lieux": [
        {
            "id": "arkose_pont_de_sevres",
            "nom": "Arkose Pont de Sèvres",
            "categorie": "escalade",
            "alias": ["Pont de Sèvres", "Arkose", "la salle", "la salle d'escalade"],
            "note": "abonnement Arkose ; salle d'escalade habituelle",
        },
    ],
    "contacts": [
        {"id": "contact_nico", "nom": "Nico", "quartier": "Montparnasse", "adresse_connue": True},
    ],
    "habitudes": {
        "escalade": {"duree_min": 120, "debut_soir": "19:00"},
        "soiree": {"debut": "20:00", "fin": "00:00"},
        "definitions": {"matin": "09:00", "midi": "12:30", "apres-midi": "14:00", "soir": "19:00"},
    },
}

ENTREES_MEMOIRE = {MEMOIRE["domicile"]["id"]: MEMOIRE["domicile"]["nom"]}
ENTREES_MEMOIRE |= {l["id"]: l["nom"] for l in MEMOIRE["lieux"]}
ENTREES_MEMOIRE |= {c["id"]: f"Chez {c['nom']} ({c['quartier']})" for c in MEMOIRE["contacts"]}
LieuId = Enum("LieuId", {i: i for i in ENTREES_MEMOIRE}, type=str)

FORMAT_DATETIME = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"
FORMAT_DATE = r"^\d{4}-\d{2}-\d{2}$"
FORMAT_HEURE = r"^\d{2}:\d{2}$"


# =============================================================================
# 1. Ce que le LLM remplit : la compréhension de la phrase
# =============================================================================
class SortieModele(BaseModel):
    raisonnement: str = Field(description="Une ou deux phrases : comment tu as compris la demande")
    activite: Literal["escalade", "soiree", "repas", "sport", "cinema", "spectacle", "travail", "rendez-vous", "autre"]
    titre: str = Field(description="Titre court pour l'agenda")
    lieu_id: Optional[LieuId] = Field(description="id d'un lieu ou d'un contact de la mémoire, ou null si aucun ne correspond")
    zone: Optional[str] = Field(description="Quartier, ville ou lieu prononcé par l'utilisateur (ex. 'Montparnasse'), ou null")
    film: Optional[str] = Field(description="Titre du film tel que prononcé, sans le corriger, ou null")
    date: Optional[str] = Field(pattern=FORMAT_DATE, description="Jour de l'activité, AAAA-MM-JJ, ou null si inconnu")
    heure_dite: Optional[str] = Field(description="L'heure précise prononcée, mot pour mot (ex. '22h'), ou null. 'soir' n'est PAS une heure précise.")
    heure: Optional[str] = Field(pattern=FORMAT_HEURE, description="heure_dite convertie en HH:MM, ou null")
    plage: Optional[Literal["matin", "midi", "apres-midi", "soir", "nuit"]] = Field(description="Moment de la journée dit sans heure précise ('ce soir' -> soir), ou null")
    debut: Optional[str] = Field(pattern=FORMAT_DATETIME, description="Début proposé AAAA-MM-JJTHH:MM, ou null (toujours null pour le cinéma)")
    fin: Optional[str] = Field(pattern=FORMAT_DATETIME, description="Fin proposée AAAA-MM-JJTHH:MM, ou null (toujours null pour le cinéma)")
    question: Optional[str] = Field(description="UNE seule question courte si une information essentielle manque, sinon null")

    @field_validator("question", "zone", "film", "heure_dite", "heure", "date", "debut", "fin", mode="before")
    @classmethod
    def vide_vers_null(cls, v):
        return None if isinstance(v, str) and not v.strip() else v


# =============================================================================
# 2. Ce que le programme produit : l'événement final
# =============================================================================
class Evenement(BaseModel):
    activite: str
    titre: str
    lieu_id: Optional[str] = None
    lieu_nom: Optional[str] = None
    adresse: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    debut: Optional[str] = None
    fin: Optional[str] = None
    horaire_flexible: bool
    confiance: float
    question: Optional[str] = None
    raisonnement: Optional[str] = None


# =============================================================================
# 3. Le LLM
# =============================================================================
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def prompt_systeme(maintenant: datetime) -> str:
    return f"""Tu es l'interpréteur d'agenda de l'application Guideo.
L'utilisateur décrit oralement une activité. Tu dois comprendre précisément sa demande.

Maintenant : {JOURS[maintenant.weekday()]} {maintenant:%Y-%m-%d %H:%M} (Europe/Paris).

Mémoire de l'utilisateur (JSON) :
{json.dumps(MEMOIRE, ensure_ascii=False, indent=2)}

Règles générales :
- Associe les mots de l'utilisateur aux lieux, alias et contacts de la mémoire (ex. "chez X" -> le contact X).
- Calcule les dates exactes à partir de "maintenant" (ce soir, demain, samedi...).
- Pour les activités de la mémoire, utilise les habitudes pour proposer debut et fin.
- Si une soirée finit à minuit, la fin est le lendemain à 00:00.
- Ne mets jamais de texte libre dans lieu_id, date, heure, debut ou fin : une valeur valide ou null.

Règles pour le cinéma (activite = "cinema") :
- Remplis film (le titre tel que dit, sans le corriger), zone, date, et heure ou plage.
- lieu_id, debut et fin sont TOUJOURS null : ils seront trouvés dans les vraies séances.
- Ne pose pas de question sur le cinéma ou la séance : le programme s'en charge.

Ne pose une question que si une information essentielle manque, et une seule.
Réponds uniquement avec le JSON demandé."""


def comprendre(phrase: str, maintenant: datetime) -> SortieModele:
    reponse = ollama.chat(
        model=MODELE,
        messages=[
            {"role": "system", "content": prompt_systeme(maintenant)},
            {"role": "user", "content": phrase},
        ],
        format=SortieModele.model_json_schema(),  # sortie forcée au schéma
        options={"temperature": 0},
    )
    return SortieModele.model_validate_json(reponse.message.content)


# =============================================================================
# 4. Sources de données cinéma
# =============================================================================
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


def distance_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geocoder_ign(texte: str, afficher: bool = False) -> Optional[tuple[float, float]]:
    """Géocodage IGN (Géoplateforme), sans clé. Renvoie (lat, lon) ou None."""
    params = urllib.parse.urlencode({"q": texte, "limit": 1, "lat": 48.8566, "lon": 2.3522})
    try:
        with urllib.request.urlopen(f"https://data.geopf.fr/geocodage/search?{params}", timeout=10) as r:
            donnees = json.load(r)
    except Exception as e:
        print(f"   [géocodage impossible pour {texte!r} : {e}]")
        return None
    feats = donnees.get("features") or []
    if not feats:
        return None
    lon, lat = feats[0]["geometry"]["coordinates"]
    if afficher:
        print(f"   [{texte!r} -> {feats[0]['properties'].get('label', '?')} ({lat:.4f}, {lon:.4f})]")
    return lat, lon


# --- Choix du bon lieu parmi les homonymes -------------------------------------
DISTANCE_MAX_KM = 40   # au-delà, un résultat est jugé improbable (sauf s'il n'y a rien de plus proche)
JOURNAL_GEOCODAGE = Path("data/geocodage_journal.jsonl")


def candidats_ign(texte: str, limite: int = 10) -> list[dict]:
    """Interroge l'IGN sur les adresses ET les points d'intérêt (quartiers, gares...)."""
    resultats = []
    for index in ("address", "poi"):
        params = urllib.parse.urlencode({"q": texte, "limit": limite, "index": index})
        try:
            with urllib.request.urlopen(f"https://data.geopf.fr/geocodage/search?{params}", timeout=10) as r:
                donnees = json.load(r)
        except Exception as e:
            print(f"   [géocodage IGN ({index}) impossible pour {texte!r} : {e}]")
            continue
        for f in donnees.get("features") or []:
            pr = f.get("properties", {})
            lon, lat = f["geometry"]["coordinates"][:2]
            nom = pr.get("label") or pr.get("name") or pr.get("toponym") or "?"
            if isinstance(nom, list):
                nom = nom[0]
            ville = pr.get("city") or pr.get("municipality") or ""
            if isinstance(ville, list):
                ville = ville[0] if ville else ""
            resultats.append({"nom": nom, "ville": ville, "lat": lat, "lon": lon, "index": index,
                              "score": float(pr.get("score") or pr.get("_score") or 0.5)})
    return resultats


def choisir_lieu(texte: str, candidats: list[dict], reference: tuple[float, float]) -> Optional[dict]:
    """Le plus probable = un bon score IGN ET proche de chez toi.

    Règle : on écarte ce qui est à plus de DISTANCE_MAX_KM de la référence (s'il reste quelque chose),
    puis on prend le meilleur score IGN, en départageant par la distance.
    """
    if not candidats:
        return None
    for c in candidats:
        c["km"] = distance_km(*reference, c["lat"], c["lon"])
    proches = [c for c in candidats if c["km"] <= DISTANCE_MAX_KM]
    if not proches:
        print(f"   [aucun {texte!r} à moins de {DISTANCE_MAX_KM} km de chez toi : résultat lointain]")
    choisi = max(proches or candidats, key=lambda c: (round(c["score"], 1), -c["km"]))

    classement = sorted(candidats, key=lambda c: c["km"])[:5]
    print(f"   [{texte!r} : {len(candidats)} résultats ; les plus proches : "
          + "; ".join(f"{c['nom']} ({c['km']:.0f} km, score {c['score']:.2f})" for c in classement) + "]")
    print(f"   [choisi : {choisi['nom']} ({choisi['lat']:.4f}, {choisi['lon']:.4f}), {choisi['km']:.1f} km de chez toi]")

    # Journal : chaque choix est gardé. Avec tes corrections, ce sera la base d'apprentissage du classement.
    try:
        JOURNAL_GEOCODAGE.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL_GEOCODAGE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"date": datetime.now().isoformat(timespec="seconds"), "requete": texte,
                                "candidats": candidats, "choisi": choisi["nom"]}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return choisi


def localiser_zone(zone: str) -> Optional[tuple[float, float]]:
    """Zones déjà connues dans la mémoire d'abord, sinon IGN + choix du plus probable."""
    for z in MEMOIRE.get("zones", []):
        if normaliser(zone) in [normaliser(a) for a in [z["nom"], *z.get("alias", [])]]:
            print(f"   [{zone!r} -> zone connue {z['nom']}]")
            return z["lat"], z["lon"]
    d = MEMOIRE["domicile"]
    choisi = choisir_lieu(zone, candidats_ign(zone), (d["lat"], d["lon"]))
    return (choisi["lat"], choisi["lon"]) if choisi else None


def parser_duree(texte: Optional[str]) -> Optional[int]:
    """'2h 14min' -> 134"""
    if not texte:
        return None
    h = re.search(r"(\d+)\s*h", texte)
    m = re.search(r"(\d+)\s*min", texte)
    total = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
    return total or None


def normaliser(texte: str) -> str:
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", sans_accents.lower()).strip()


VERSIONS = {"ORIGINAL": "VO", "ORIGINAL_ST": "VOST", "DUBBED": "VF", "LOCAL": "VF"}


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
    """Séances réelles via la bibliothèque non officielle allocine-seances (testé avec 0.0.14)."""

    # Paris et la petite couronne. Ajoute "Yvelines" etc. au besoin : seuls les manquants sont récupérés.
    EMPLACEMENTS = ("Paris", "Hauts-de-Seine", "Seine-Saint-Denis", "Val-de-Marne")

    def __init__(self, emplacements=EMPLACEMENTS, cache=Path("data/cinemas_cache.json")):
        from allocineAPI.allocineAPI import allocineAPI
        self.api = allocineAPI()
        self.emplacements = emplacements
        self.cache = cache
        self._cinemas: Optional[list[Cinema]] = None
        self._durees: dict[tuple[str, str], Optional[int]] = {}

    def localiser(self, zone: str) -> Optional[tuple[float, float]]:
        return localiser_zone(zone)

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
                    pos = geocoder_ign(adresse) or (None, None)
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
        try:
            films = self.api.get_showtime(cinema.id, jour.isoformat())
        except Exception as e:
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
                films = self.api.get_movies(seance.cinema.id, seance.debut.date().isoformat())
                for f in films:
                    self._durees[(seance.cinema.id, f.get("title"))] = parser_runtime(f.get("runtime"))
            except Exception as e:
                print(f"   [durée indisponible : {e}]")
        return self._durees.get(cle)


class SourceFactice:
    """Séances inventées, pour tester la logique sans réseau. Tout est aujourd'hui."""

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

    def localiser(self, zone: str) -> Optional[tuple[float, float]]:
        return {"montparnasse": (48.8427, 2.3211)}.get(normaliser(zone))

    def cinemas(self) -> list[Cinema]:
        return self._cinemas

    def duree(self, seance: Seance) -> Optional[int]:
        return seance.duree_min

    def seances(self, cinema: Cinema, jour: date) -> list[Seance]:
        return [s for s in self._seances if s.cinema.id == cinema.id and s.debut.date() == jour]


# =============================================================================
# 5. Résolveurs
# =============================================================================
def demander_console(question: str, options: list[str]) -> Optional[int]:
    """Pose une question à choix. Renvoie l'index choisi, ou None pour 'aucun'."""
    print(f"\n   ? {question}")
    for i, o in enumerate(options, 1):
        print(f"     {i}. {o}")
    print("     0. aucun")
    while True:
        rep = input("   > ").strip()
        if rep.isdigit() and 0 <= int(rep) <= len(options):
            return int(rep) - 1 if int(rep) else None
        print("   (tape un numéro)")


def resoudre_memoire(sortie: SortieModele) -> Evenement:
    confiance = 1.0
    if sortie.lieu_id is None:
        confiance -= 0.4
    if sortie.debut is None:
        confiance -= 0.4
    if sortie.question:
        confiance -= 0.1
    lieu_id = sortie.lieu_id.value if sortie.lieu_id else None
    return Evenement(
        activite=sortie.activite,
        titre=sortie.titre,
        lieu_id=lieu_id,
        lieu_nom=ENTREES_MEMOIRE.get(lieu_id) if lieu_id else sortie.zone,
        debut=sortie.debut,
        fin=sortie.fin,
        horaire_flexible=sortie.heure_dite is None,
        confiance=round(max(confiance, 0.0), 2),
        question=sortie.question,
        raisonnement=sortie.raisonnement,
    )


def _dans_plage(d: datetime, plage: str) -> bool:
    debut, fin = PLAGES[plage]
    return debut <= d.strftime("%H:%M") <= fin


def resoudre_cinema(sortie: SortieModele, source, maintenant: datetime,
                    demander: Callable[[str, list[str]], Optional[int]] = demander_console) -> Optional[Evenement]:
    if not sortie.film:
        print("   Quel film veux-tu voir ? (redis la phrase avec le titre)")
        return None
    if not sortie.zone:
        print("   Dans quel quartier ? (redis la phrase avec le lieu)")
        return None

    jour = date.fromisoformat(sortie.date) if sortie.date else maintenant.date()

    # Cinémas de la zone
    pos = source.localiser(sortie.zone)
    if pos is None:
        print(f"   Je ne trouve pas où est {sortie.zone!r}.")
        return None
    distances = {c.id: distance_km(*pos, c.lat, c.lon) for c in source.cinemas() if c.lat is not None}
    tous = {c.id: c for c in source.cinemas()}
    seances = []
    for rayon in RAYONS_KM:
        proches = sorted((i for i, d in distances.items() if d <= rayon), key=distances.get)
        if not proches:
            continue
        print(f"   [{len(proches)} cinéma(s) à moins de {rayon} km : "
              + ", ".join(f"{tous[i].nom} ({distances[i]:.1f} km)" for i in proches) + "]")
        # Séances du jour (pas celles déjà passées)
        seances = [s for i in proches for s in source.seances(tous[i], jour) if s.debut >= maintenant]
        if any(fuzz.token_set_ratio(normaliser(sortie.film), normaliser(s.film)) >= SEUIL_TITRE for s in seances):
            break  # le film passe près d'ici : inutile d'élargir
    if not distances or not seances:
        print(f"   Aucune séance trouvée à moins de {RAYONS_KM[-1]} km de {sortie.zone} le {jour:%d/%m}.")
        return None

    # Film : correspondance approximative du titre
    demande = normaliser(sortie.film)
    titres = sorted({s.film for s in seances})
    exacts = [t for t in titres if normaliser(t) == demande]
    # moyenne de la similarité du titre entier et des mots en commun
    scores = {t: (fuzz.ratio(demande, normaliser(t)) + fuzz.token_set_ratio(demande, normaliser(t))) / 2
              for t in titres}
    meilleur = max(scores.values(), default=0)
    # on garde les titres au-dessus du seuil ET proches du meilleur (évite "Moulin" pour "moulins de paris")
    candidats = exacts or [t for t, s in scores.items() if s >= SEUIL_TITRE and s >= meilleur - 5]
    if not candidats:
        print(f"   Aucun film proche de {sortie.film!r} près de {sortie.zone} le {jour:%d/%m}.")
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
    if sortie.heure:
        cible = datetime.combine(jour, datetime.strptime(sortie.heure, "%H:%M").time())
        filtrees = [s for s in seances if abs((s.debut - cible).total_seconds()) <= TOLERANCE_HEURE_MIN * 60]
        contrainte = f"vers {sortie.heure}"
    elif sortie.plage:
        filtrees = [s for s in seances if _dans_plage(s.debut, sortie.plage)]
        contrainte = f"le {sortie.plage}"
    else:
        filtrees, contrainte = seances, ""

    if not filtrees:
        print(f"   Pas de séance de {film} {contrainte} près de {sortie.zone}.")
        if not seances:
            return None
        options = [f"{s.debut:%H:%M} — {s.cinema.nom} ({s.version})" for s in sorted(seances, key=lambda s: s.debut)]
        i = demander("Autres séances ce jour-là :", options)
        if i is None:
            return None
        filtrees = [sorted(seances, key=lambda s: s.debut)[i]]

    # Cinéma, puis séance
    filtrees.sort(key=lambda s: distances[s.cinema.id])
    cinemas = list(dict.fromkeys(s.cinema.nom for s in filtrees))  # du plus proche au plus loin
    if len(cinemas) > 1:
        km = {s.cinema.nom: distances[s.cinema.id] for s in filtrees}
        options = [f"{nom} ({km[nom]:.1f} km) — "
                   + ", ".join(f"{s.debut:%H:%M} {s.version}" for s in filtrees if s.cinema.nom == nom)
                   for nom in cinemas]
        i = demander(f"Plusieurs cinémas passent {film} {contrainte}. Lequel ?", options)
        if i is None:
            return None
        filtrees = [s for s in filtrees if s.cinema.nom == cinemas[i]]

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
        print("   [durée du film inconnue : 2h par défaut]")
    fin = seance.debut + timedelta(minutes=PUBS_MIN + (duree or 120))
    return Evenement(
        activite="cinema",
        titre=f"{film} ({seance.version})",
        lieu_id=f"cinema_{seance.cinema.id}",
        lieu_nom=seance.cinema.nom,
        adresse=seance.cinema.adresse,
        lat=seance.cinema.lat,
        lon=seance.cinema.lon,
        debut=seance.debut.strftime("%Y-%m-%dT%H:%M"),
        fin=fin.strftime("%Y-%m-%dT%H:%M"),
        horaire_flexible=False,
        confiance=1.0,
        raisonnement=sortie.raisonnement,
    )


# =============================================================================
# 6. Programme principal
# =============================================================================
RE_HEURE = re.compile(r"\b(\d{1,2})\s*(?:h|heures?)\s*(\d{2})?\b")
MOTS_PLAGE = [  # ordre important : "après-midi" avant "midi"
    ("apres-midi", re.compile(r"apr[eè]s[- ]?midi")),
    ("matin", re.compile(r"\bmatin")),
    ("midi", re.compile(r"\bmidi\b")),
    ("soir", re.compile(r"\bsoir")),
    ("nuit", re.compile(r"\bnuit\b")),
]


def verifier_horaire(sortie: SortieModele, phrase: str) -> SortieModele:
    """Le code vérifie l'heure et la plage dans la phrase elle-même : plus fiable qu'un petit modèle."""
    texte = phrase.lower()
    m = RE_HEURE.search(texte)
    if m and int(m.group(1)) < 24:
        sortie.heure = f"{int(m.group(1)):02d}:{m.group(2) or '00'}"
        sortie.heure_dite = m.group(0)
        sortie.plage = None
    else:
        sortie.heure = sortie.heure_dite = None
        sortie.plage = next((nom for nom, motif in MOTS_PLAGE if motif.search(texte)), sortie.plage)
    if sortie.activite == "cinema":
        sortie.question = None  # pour le cinéma, c'est le programme qui pose les questions
    return sortie


def interpreter(phrase: str, source, maintenant: Optional[datetime] = None) -> Optional[Evenement]:
    maintenant = maintenant or datetime.now()
    sortie = verifier_horaire(comprendre(phrase, maintenant), phrase)
    print("   compris :", sortie.model_dump_json(exclude={"raisonnement"}, exclude_none=True))
    if sortie.activite == "cinema":
        return resoudre_cinema(sortie, source, maintenant)
    return resoudre_memoire(sortie)


PHRASES_TEST = [
    "je vais grimper à Pont de Sèvres demain soir",
    "je vais en soirée chez Nico samedi",
    "je vais voir Moulin ce soir à Montparnasse",
    "je vais au cinéma à Montparnasse pour voir le film Moulin à 22h",
]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--faux", action="store_true", help="séances factices au lieu d'Allociné")
    parser.add_argument("-p", "--phrase", action="append", help="phrase à tester (répétable)")
    args = parser.parse_args()

    source = SourceFactice() if args.faux else SourceAllocine()
    for phrase in args.phrase or PHRASES_TEST:
        print(f"\n>>> {phrase}")
        evt = interpreter(phrase, source)
        print("   =>", evt.model_dump_json(indent=2, exclude_none=True) if evt else "aucun événement")
