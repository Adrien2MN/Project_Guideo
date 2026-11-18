"""Connaissances générales (connaissances_generales.toml) : valeurs de départ communes à tous."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

FICHIER = Path(__file__).with_name("connaissances_generales.toml")


@dataclass(frozen=True)
class Connaissances:
    plages: dict
    activites: dict
    trajets: dict = field(default_factory=dict)

    def bornes(self, plage: str) -> tuple[str, str]:
        p = self.plages[plage]
        return p["de"], p["a"]

    def plage_de(self, hhmm: str) -> Optional[str]:
        """Plage d'une heure (12:00 -> midi, 19:30 -> soir). Sert à ranger les observations."""
        for nom in ("midi", "matin", "apres-midi", "soir", "nuit"):
            de, a = self.bornes(nom)
            if de <= hhmm <= a:
                return nom
        return None

    def activite(self, nom: str) -> dict:
        return self.activites.get(nom, {})

    def debut_type(self, activite: str, plage: Optional[str]) -> Optional[tuple[str, str]]:
        """(heure, explication) de début par défaut, ou None si rien de raisonnable."""
        a = self.activite(activite)
        if plage and plage in a.get("heures", {}):
            return a["heures"][plage], f"{activite} {plage} (général)"
        if "debut" in a and (plage is None or self.plage_de(a["debut"]) == plage):
            return a["debut"], f"{activite} (général)"  # ex. une soirée : 20h, dite "soirée" ou "ce soir"
        if plage:
            return self.plages[plage]["heure_type"], f"« {plage} » (général)"
        return None

    def fin_type(self, activite: str, plage: Optional[str]) -> Optional[str]:
        a = self.activite(activite)
        return a["fin"] if plage in (None, "soir", "nuit") and "fin" in a else None

    def duree_type(self, activite: str) -> Optional[int]:
        return self.activite(activite).get("duree_min")

    def souplesse(self, activite: str) -> dict:
        """{"souple", "prolonger_max_min", "ecourter_max_min"} : fixe par défaut."""
        a = self.activite(activite)
        souple = bool(a.get("souple", False))
        return {"souple": souple, "prolonger_max_min": a.get("prolonger_max_min", 0) if souple else 0,
                "ecourter_max_min": a.get("ecourter_max_min", 0) if souple else 0}


def charger_connaissances(chemin: Path = FICHIER) -> Connaissances:
    with open(chemin, "rb") as f:
        d = tomllib.load(f)
    return Connaissances(plages=d["plages"], activites=d["activites"], trajets=d.get("trajets", {}))
