"""Ce que le code lit lui-même dans la phrase : jour, heures, moment de la journée, mots dits.

Ces règles sont plus fiables qu'un petit LLM pour tout ce qui est calcul (dates) ou recopie
exacte (titres, lieux). Le LLM, lui, s'occupe de comprendre le sens (quelle activité,
quel mot désigne le lieu, la personne, le film).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

from rapidfuzz import fuzz

from guideo.memoire.memoire import JOURS, normaliser

MOIS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
        "septembre", "octobre", "novembre", "decembre"]
NOMBRES = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7}


def _texte(phrase: str) -> str:
    """'Jusqu'à l'après-midi' -> 'jusqu a l apres midi' (apostrophes et tirets deviennent des espaces)."""
    return f" {normaliser(phrase)} "


# ---------------------------------------------------------------------------- jour
RE_DATE_CHIFFRES = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")

def date_dite(phrase: str, maintenant: datetime) -> Optional[date]:
    """Le jour exprimé dans la phrase, ou None si aucun jour n'est dit."""
    t = _texte(phrase)
    auj = maintenant.date()

    # Dates explicites : "12/11", "le 12 novembre", "le 12"
    m = RE_DATE_CHIFFRES.search(phrase)
    if m:
        return _date_valide(auj, int(m.group(1)), int(m.group(2)), m.group(3))
    m = re.search(rf"\b(\d{{1,2}}|1er) ({'|'.join(MOIS)})\b(?: (\d{{4}}))?", t)
    if m:
        jour = 1 if m.group(1) == "1er" else int(m.group(1))
        return _date_valide(auj, jour, MOIS.index(m.group(2)) + 1, m.group(3))
    m = re.search(r"\ble (\d{1,2}|1er)\b(?! ?h\b| ?heures?\b)", t)
    if m:
        jour = 1 if m.group(1) == "1er" else int(m.group(1))
        mois = auj.month if jour >= auj.day else auj.month % 12 + 1
        return _date_valide(auj, jour, mois, None)

    # Dates relatives
    if re.search(r"\bapres demain\b", t):
        return auj + timedelta(days=2)
    if re.search(r"\bdemain\b", t):
        return auj + timedelta(days=1)
    if re.search(r"\b(aujourd hui|ce soir|ce matin|cet apres midi|ce midi|cette nuit|tout a l heure"
                 r"|tout de suite|maintenant|tantot)\b", t):
        return auj
    m = re.search(rf"\bdans (\d+|{'|'.join(NOMBRES)}) jours?\b", t)
    if m:
        n = m.group(1)
        return auj + timedelta(days=int(n) if n.isdigit() else NOMBRES[n])
    if re.search(r"\bdans une semaine\b", t):
        return auj + timedelta(days=7)
    if re.search(r"\b(ce|le) week ?end\b", t):
        return auj + timedelta(days=(5 - auj.weekday()) % 7)
    for i, nom in enumerate(JOURS):
        if re.search(rf"\b{nom}\b", t):
            ecart = (i - auj.weekday()) % 7 or 7  # "jeudi" dit un jeudi = le jeudi suivant
            if re.search(rf"\b{nom} en huit\b", t):
                ecart += 7
            return auj + timedelta(days=ecart)
    return None


def _date_valide(auj: date, jour: int, mois: int, annee: Optional[str]) -> Optional[date]:
    if annee:
        a = int(annee) + (2000 if len(annee) == 2 else 0)
    else:
        a = auj.year + (1 if (mois, jour) < (auj.month, auj.day) else 0)  # date passée -> l'an prochain
    try:
        return date(a, mois, jour)
    except ValueError:
        return None


# ---------------------------------------------------------------------------- heures
_H = r"(?:(\d{1,2}) ?(?:h|heures?)(?: ?(\d{2}))?|(?<!apres )(midi)|(minuit))"


def _texte_heures(phrase: str) -> str:
    """Prépare la phrase pour lire les heures : "19:30" -> "19h30", et les dates "12/11" sont retirées."""
    phrase = RE_DATE_CHIFFRES.sub(" ", phrase)
    phrase = re.sub(r"\b(\d{1,2}):(\d{2})\b", r"\1h\2", phrase)
    return _texte(phrase)


def _hhmm(groupes: tuple) -> Optional[str]:
    h, m, midi, minuit = groupes
    if midi:
        return "12:00"
    if minuit:
        return "00:00"
    if h is None or int(h) > 23 or int(m or 0) > 59:
        return None
    return f"{int(h):02d}:{int(m or 0):02d}"


def heures_dites(phrase: str) -> tuple[Optional[str], Optional[str]]:
    """(début, fin) au format HH:MM, si l'utilisateur les a dits.

    "de 19h à 21h" -> (19:00, 21:00) ; "jusqu'à minuit" -> (None, 00:00) ; "à 22h30" -> (22:30, None).
    """
    t = _texte_heures(phrase)
    m = re.search(rf"{_H} (?:a |et |jusqu a )?{_H}", t)
    if m:
        debut, fin = _hhmm(m.groups()[:4]), _hhmm(m.groups()[4:])
        if debut and fin:
            return debut, fin
    fin = None
    m = re.search(rf"\bjusqu a {_H}", t)
    if m:
        fin = _hhmm(m.groups())
        t = t[:m.start()] + " " + t[m.end():]
    m = re.search(_H, t)
    return (_hhmm(m.groups()) if m else None), fin


# ---------------------------------------------------------------------------- moment de la journée
PLAGES_DITES = [  # ordre important : "après-midi" avant "midi"
    ("apres-midi", r"\b(apres midi|aprem|aprems)\b"),
    ("midi", r"\b(midi|dejeun\w*|brunch\w*)\b"),
    ("matin", r"\b(matin|matinee)\b"),
    ("soir", r"\b(soir|soiree|din\w*|en sortant du (boulot|travail|taf|bureau)|apres le (boulot|travail|taf|bureau))\b"),
    ("nuit", r"\bnuit\b"),
]


def plage_dite(phrase: str) -> Optional[str]:
    t = _texte(phrase)
    return next((nom for nom, motif in PLAGES_DITES if re.search(motif, t)), None)


# ---------------------------------------------------------------------------- mots dits
def mots_dits(valeur: Optional[str], phrase: str, seuil: int = 40) -> Optional[str]:
    """Garde une zone, un titre ou un prénom tel que l'utilisateur l'a DIT.

    Si le LLM l'a "corrigé" (Beaugrenelle -> Beaubourg), on reprend les mots les plus proches
    réellement présents dans la phrase. Si rien ne ressemble, la valeur n'a pas été dite : None.
    """
    if not valeur or not normaliser(valeur):
        return None
    if f" {normaliser(valeur)} " in _texte(phrase):
        return valeur
    mots = re.findall(r"[\w'-]+", phrase)
    n = len(valeur.split())
    meilleur, score = None, 0.0
    for taille in range(max(1, n - 1), n + 2):
        for i in range(len(mots) - taille + 1):
            morceau = mots[i:i + taille]
            if max(len(m) for m in morceau) < 4:
                continue  # pas de "la", "à", "3 a"...
            sc = fuzz.ratio(normaliser(valeur), normaliser(" ".join(morceau)))
            if sc > score:
                meilleur, score = " ".join(morceau), sc
    return meilleur if meilleur and score >= seuil else None


def chez_dit(valeur: Optional[str], phrase: str) -> Optional[str]:
    """La personne chez qui on va, seulement si "chez" est vraiment dit ("avec Nico" n'est pas "chez Nico")."""
    if not valeur or not re.search(r"\bchez\b", _texte(phrase)):
        return None
    valeur = re.sub(r"^\s*chez\s+", "", valeur, flags=re.I)
    return mots_dits(valeur, phrase)
