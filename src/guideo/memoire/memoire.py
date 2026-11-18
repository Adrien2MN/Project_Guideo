"""Mémoire personnelle de Guideo : ce que l'application apprend sur l'utilisateur.

Principes :
- elle commence VIDE et ne se remplit qu'à partir de ce que l'utilisateur dit, choisit,
  corrige ou fait (plus tard : traces GPS) ;
- seul le code écrit dedans (jamais le LLM directement), et chaque modification est
  journalisée avec sa source, pour pouvoir expliquer, annuler ou oublier ;
- les habitudes ne sont pas des valeurs figées mais des OBSERVATIONS (une par activité
  confirmée), dont on tire des statistiques (heure de début, durée, lieu habituel) ;
- elle est privée : fichier dans data/ (ignoré par git), et le LLM n'en reçoit jamais
  les adresses ni les coordonnées.

Format du fichier (JSON lisible et modifiable à la main) :
{
  "version": 1,
  "domicile": {"adresse", "lat", "lon", "source", "maj_le"} ou null,
  "lieux":    [{"id", "nom", "categorie", "adresse", "lat", "lon", "alias": [...], "source",
                "cree_le", "utilise_le", "nb_utilisations"}],
  "contacts": [{"id", "nom", "adresse", "lat", "lon", "alias": [...], "source", "cree_le", ...}],
  "zones":    [{"id", "nom", "lat", "lon", "alias": [...], "source", "cree_le", ...}],
  "observations": [{"activite", "lieu_id", "date", "jour", "plage", "debut", "fin",
                    "duree_min", "source", "le"}]
}
Un alias est {"texte", "source", "ajoute_le"}.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

VERSION = 1
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
SOURCES = {"reponse", "choix", "correction", "confirmation", "trace_gps", "manuel", "import"}
TYPES = ("lieux", "contacts", "zones")


def normaliser(texte: str) -> str:
    """Minuscules, sans accents ni ponctuation : 'Pont-de-Sèvres' -> 'pont de sevres'."""
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]", " ", sans_accents.lower())).strip()


def _slug(texte: str) -> str:
    return normaliser(texte).replace(" ", "_") or "sans_nom"


def _maintenant() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _hhmm(minutes: float) -> str:
    minutes = int(round(minutes)) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def vide() -> dict:
    return {"version": VERSION, "domicile": None, "lieux": [], "contacts": [], "zones": [], "observations": []}


class Memoire:
    """Accès à la mémoire personnelle. Toute écriture passe par ces méthodes."""

    def __init__(self, chemin: Path | str, lecture_seule: bool = False, journal: Path | str | None = None):
        self.chemin = Path(chemin)
        self.lecture_seule = lecture_seule
        self.journal = Path(journal) if journal else self.chemin.with_name("journal.jsonl")
        self.donnees = self._charger()

    # ------------------------------------------------------------------ fichier
    def _charger(self) -> dict:
        if not self.chemin.exists():
            return vide()
        donnees = json.loads(self.chemin.read_text(encoding="utf-8"))
        if donnees.get("version") != VERSION:
            raise ValueError(f"{self.chemin} : version {donnees.get('version')} inconnue (attendue {VERSION})")
        for cle, valeur in vide().items():
            donnees.setdefault(cle, valeur)
        return donnees

    def _sauver(self) -> None:
        if self.lecture_seule:
            return
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.chemin.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.donnees, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.chemin)  # écriture atomique : jamais de fichier à moitié écrit

    def _journaliser(self, action: str, source: str, **details) -> None:
        if self.lecture_seule:
            return
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        ligne = {"le": _maintenant(), "action": action, "source": source, **details}
        with self.journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")

    @staticmethod
    def _verifier_source(source: str) -> None:
        if source not in SOURCES:
            raise ValueError(f"source inconnue : {source!r} (attendu : {sorted(SOURCES)})")

    # ------------------------------------------------------------------ lecture
    @property
    def domicile(self) -> Optional[dict]:
        return self.donnees["domicile"]

    def lieux(self, categorie: Optional[str] = None) -> list[dict]:
        return [l for l in self.donnees["lieux"] if categorie is None or l["categorie"] == categorie]

    def contacts(self) -> list[dict]:
        return list(self.donnees["contacts"])

    def zones(self) -> list[dict]:
        return list(self.donnees["zones"])

    def entite(self, id_: str) -> Optional[dict]:
        if id_ == "domicile":
            return {"id": "domicile", "nom": "Domicile", **(self.domicile or {})} if self.domicile else None
        for type_ in TYPES:
            for e in self.donnees[type_]:
                if e["id"] == id_:
                    return e
        return None

    @staticmethod
    def textes(entite: dict) -> list[str]:
        """Nom et alias d'une entité, normalisés."""
        return [normaliser(entite["nom"])] + [normaliser(a["texte"]) for a in entite.get("alias", [])]

    def chercher(self, texte: str, types: Iterable[str] = TYPES, categorie: Optional[str] = None) -> list[dict]:
        """Entités dont le nom ou un alias correspond exactement (après normalisation) à `texte`.

        Pour les lieux, si `categorie` est donnée, seuls ceux de cette catégorie sont gardés :
        "Issy" peut désigner la salle d'escalade sans désigner un cinéma d'Issy.
        """
        n = normaliser(texte)
        if not n:
            return []
        resultats = []
        for type_ in types:
            for e in self.donnees[type_]:
                if type_ == "lieux" and categorie and e["categorie"] != categorie:
                    continue
                if n in self.textes(e):
                    resultats.append(e)
        return resultats

    def trouver_dans_phrase(self, phrase: str, types: Iterable[str] = ("lieux",),
                            categorie: Optional[str] = None) -> list[dict]:
        """Entités dont un nom ou un alias apparaît, en mots entiers, dans la phrase."""
        n = f" {normaliser(phrase)} "
        trouves = []
        for type_ in types:
            for e in self.donnees[type_]:
                if type_ == "lieux" and categorie and e["categorie"] != categorie:
                    continue
                if any(t and f" {t} " in n for t in self.textes(e)):
                    trouves.append(e)
        return trouves

    def resume(self) -> str:
        d = self.donnees
        return (f"{len(d['lieux'])} lieu(x), {len(d['contacts'])} contact(s), {len(d['zones'])} zone(s), "
                f"{len(d['observations'])} observation(s), domicile {'connu' if d['domicile'] else 'inconnu'}")

    # ------------------------------------------------------------------ écriture : entités
    def definir_domicile(self, adresse: str, lat: float, lon: float, source: str = "reponse") -> None:
        self._verifier_source(source)
        avant = self.domicile
        self.donnees["domicile"] = {"adresse": adresse, "lat": lat, "lon": lon, "source": source, "maj_le": _maintenant()}
        self._journaliser("definir_domicile", source, avant=avant, apres=self.donnees["domicile"])
        self._sauver()

    def _nouvel_id(self, base: str) -> str:
        existants = {e["id"] for t in TYPES for e in self.donnees[t]}
        id_, i = base, 2
        while id_ in existants:
            id_, i = f"{base}_{i}", i + 1
        return id_

    def _ajouter(self, type_: str, prefixe: str, nom: str, source: str, alias: Iterable[str] = (), **champs) -> dict:
        self._verifier_source(source)
        entite = {"id": self._nouvel_id(f"{prefixe}_{_slug(nom)}"), "nom": nom, **champs, "alias": [],
                  "source": source, "cree_le": _maintenant(), "utilise_le": None, "nb_utilisations": 0}
        self.donnees[type_].append(entite)
        for a in alias:
            self._ajouter_alias_sans_sauver(entite, a, source)
        self._journaliser(f"ajouter_{type_[:-1] if type_ != 'lieux' else 'lieu'}", source, entite=entite)
        self._sauver()
        return entite

    def ajouter_lieu(self, nom: str, categorie: str, lat: Optional[float], lon: Optional[float],
                     adresse: Optional[str] = None, alias: Iterable[str] = (), source: str = "reponse",
                     ref: Optional[str] = None) -> dict:
        """`ref` : identifiant dans une source externe (ex. "allocine:C0158"), pour le retrouver."""
        return self._ajouter("lieux", categorie, nom, source, alias,
                             categorie=categorie, adresse=adresse, lat=lat, lon=lon, ref=ref)

    def lieu_par_ref(self, ref: str) -> Optional[dict]:
        return next((l for l in self.donnees["lieux"] if l.get("ref") == ref), None)

    def nb_observations(self, lieu_id: str) -> int:
        return sum(o["lieu_id"] == lieu_id for o in self.donnees["observations"])

    def ajouter_contact(self, nom: str, adresse: Optional[str] = None, lat: Optional[float] = None,
                        lon: Optional[float] = None, alias: Iterable[str] = (), source: str = "reponse") -> dict:
        return self._ajouter("contacts", "contact", nom, source, alias, adresse=adresse, lat=lat, lon=lon)

    def ajouter_zone(self, nom: str, lat: float, lon: float, alias: Iterable[str] = (),
                     source: str = "confirmation") -> dict:
        existantes = self.chercher(nom, types=("zones",))
        if existantes:
            return existantes[0]
        return self._ajouter("zones", "zone", nom, source, alias, lat=lat, lon=lon)

    def _ajouter_alias_sans_sauver(self, entite: dict, texte: str, source: str) -> bool:
        texte = texte.strip()
        if not normaliser(texte) or normaliser(texte) in self.textes(entite):
            return False
        entite.setdefault("alias", []).append({"texte": texte, "source": source, "ajoute_le": _maintenant()})
        return True

    def ajouter_alias(self, id_: str, texte: str, source: str = "reponse") -> bool:
        self._verifier_source(source)
        entite = self.entite(id_)
        if entite is None or id_ == "domicile":
            raise KeyError(id_)
        if not self._ajouter_alias_sans_sauver(entite, texte, source):
            return False
        self._journaliser("ajouter_alias", source, id=id_, alias=texte)
        self._sauver()
        return True

    def modifier(self, id_: str, source: str = "correction", **champs) -> dict:
        """Corrige des champs d'une entité (ex. l'adresse d'un contact). L'ancien état est journalisé."""
        self._verifier_source(source)
        entite = self.entite(id_)
        if entite is None or id_ == "domicile":
            raise KeyError(id_)
        interdits = set(champs) & {"id", "alias", "source", "cree_le"}
        if interdits:
            raise ValueError(f"champs non modifiables : {interdits}")
        avant = {k: entite.get(k) for k in champs}
        entite.update(champs)
        self._journaliser("modifier", source, id=id_, avant=avant, apres=champs)
        self._sauver()
        return entite

    def supprimer(self, id_: str, source: str = "manuel") -> None:
        """Oubli d'une entité et de ses observations (droit à l'effacement)."""
        self._verifier_source(source)
        for type_ in TYPES:
            for e in list(self.donnees[type_]):
                if e["id"] == id_:
                    self.donnees[type_].remove(e)
                    nb = len(self.donnees["observations"])
                    self.donnees["observations"] = [o for o in self.donnees["observations"] if o["lieu_id"] != id_]
                    self._journaliser("supprimer", source, entite=e,
                                      observations_supprimees=nb - len(self.donnees["observations"]))
                    self._sauver()
                    return
        raise KeyError(id_)

    def marquer_utilise(self, id_: Optional[str]) -> None:
        entite = self.entite(id_) if id_ else None
        if entite is None or id_ == "domicile":
            return
        entite["utilise_le"] = _maintenant()
        entite["nb_utilisations"] = entite.get("nb_utilisations", 0) + 1
        self._sauver()

    # ------------------------------------------------------------------ écriture : observations
    def observer(self, activite: str, lieu_id: Optional[str], debut: datetime, fin: Optional[datetime],
                 plage: Optional[str] = None, source: str = "confirmation") -> dict:
        """Enregistre une activité qui a eu (ou aura) lieu : c'est la matière des habitudes."""
        self._verifier_source(source)
        obs = {
            "activite": activite,
            "lieu_id": lieu_id,
            "date": debut.date().isoformat(),
            "jour": JOURS[debut.weekday()],
            "plage": plage,
            "debut": debut.strftime("%H:%M"),
            "fin": fin.strftime("%H:%M") if fin else None,
            "duree_min": int((fin - debut).total_seconds() // 60) if fin else None,
            "source": source,
            "le": _maintenant(),
        }
        self.donnees["observations"].append(obs)
        self._journaliser("observer", source, observation=obs)
        self._sauver()
        return obs

    # ------------------------------------------------------------------ statistiques
    def _observations(self, activite: str, plage: Optional[str] = None, lieu_id: Optional[str] = None,
                      n_max: int = 20) -> list[dict]:
        obs = [o for o in self.donnees["observations"] if o["activite"] == activite]
        if plage:
            obs = [o for o in obs if o.get("plage") == plage]
        if lieu_id:
            obs = [o for o in obs if o["lieu_id"] == lieu_id]
        return obs[-n_max:]  # les plus récentes seulement : une vieille habitude s'efface peu à peu

    def horaires_habituels(self, activite: str, plage: Optional[str] = None,
                           lieu_id: Optional[str] = None) -> Optional[dict]:
        """Heure de début et durée médianes des dernières observations, ou None s'il n'y en a pas."""
        obs = self._observations(activite, plage, lieu_id)
        if lieu_id and not obs:
            obs = self._observations(activite, plage)  # pas encore vu à ce lieu : habitudes de l'activité
        if not obs:
            return None
        durees = [o["duree_min"] for o in obs if o.get("duree_min")]
        return {
            "debut": _hhmm(statistics.median(_minutes(o["debut"]) for o in obs)),
            "duree_min": int(statistics.median(durees)) if durees else None,
            "n": len(obs),
        }

    def lieu_habituel(self, activite: str, part_min: float = 0.8, n_min: int = 3) -> Optional[dict]:
        """Le lieu où l'activité a lieu presque toujours (>= 80 % des fois, au moins 3 fois), sinon None."""
        obs = [o for o in self._observations(activite) if o["lieu_id"]]
        if len(obs) < n_min:
            return None
        lieu_id, nb = Counter(o["lieu_id"] for o in obs).most_common(1)[0]
        if nb / len(obs) < part_min or self.entite(lieu_id) is None:
            return None
        return {"lieu": self.entite(lieu_id), "part": nb / len(obs), "n": len(obs)}
