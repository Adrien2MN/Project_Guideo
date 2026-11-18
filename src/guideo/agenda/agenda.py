"""Agenda de Guideo : les événements validés, chacun encadré de son trajet aller et de son trajet retour.

Stockage : data/agenda/agenda.json (privé, hors git). Export : data/agenda/guideo.ics, à importer
ou à abonner dans Google Agenda, Apple Calendrier, Outlook... Les trajets y apparaissent pour
bloquer le temps de déplacement ("Trajet → UGC Montparnasse"), mais marqués X-GUIDEO-TYPE:trajet :
ce ne sont pas des rendez-vous. Le fichier est réécrit en entier à chaque export, donc un trajet
devenu inutile disparaît (si l'agenda est ABONNÉ au fichier ; un import ponctuel, lui, se fige).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from guideo.trajets import Point, Trajet

VERSION = 1


@dataclass
class Evenement:
    titre: str
    activite: str
    debut: datetime
    fin: Optional[datetime]
    lieu: Optional[Point]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    # Souple : la durée peut bouger (escalade). Fixe : on arrive avant, on part à la fin (cinéma).
    souple: bool = False
    prolonger_max_min: int = 0
    ecourter_max_min: int = 0
    # Les trajets ne sont PAS des événements : ils sont rattachés à un événement et entièrement
    # recalculés à chaque planification de la journée (jamais figés, jamais des obstacles).
    aller: Optional[Trajet] = None
    retour: Optional[Trajet] = None
    alerte: Optional[str] = None
    debut_ajuste: Optional[datetime] = None   # posés par la planification (événements souples)
    fin_ajustee: Optional[datetime] = None
    cree_le: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def fin_ou_estimee(self) -> datetime:
        """Fin PRÉVUE (ou début + 1 h si inconnue)."""
        return self.fin or self.debut + timedelta(hours=1)

    @property
    def debut_effectif(self) -> datetime:
        return self.debut_ajuste or self.debut

    @property
    def fin_effective(self) -> datetime:
        return self.fin_ajustee or self.fin_ou_estimee

    def en_dict(self) -> dict:
        return {"id": self.id, "titre": self.titre, "activite": self.activite,
                "debut": self.debut.isoformat(timespec="minutes"),
                "fin": self.fin.isoformat(timespec="minutes") if self.fin else None,
                "lieu": self.lieu.en_dict() if self.lieu else None,
                "aller": self.aller.en_dict() if self.aller else None,
                "retour": self.retour.en_dict() if self.retour else None,
                "souple": self.souple, "prolonger_max_min": self.prolonger_max_min,
                "ecourter_max_min": self.ecourter_max_min,
                "debut_ajuste": self.debut_ajuste.isoformat(timespec="minutes") if self.debut_ajuste else None,
                "fin_ajustee": self.fin_ajustee.isoformat(timespec="minutes") if self.fin_ajustee else None,
                "alerte": self.alerte, "cree_le": self.cree_le}

    @classmethod
    def depuis(cls, d: dict) -> "Evenement":
        return cls(titre=d["titre"], activite=d["activite"], debut=datetime.fromisoformat(d["debut"]),
                   fin=datetime.fromisoformat(d["fin"]) if d.get("fin") else None, lieu=Point.depuis(d.get("lieu")),
                   id=d["id"], aller=Trajet.depuis(d.get("aller")), retour=Trajet.depuis(d.get("retour")),
                   alerte=d.get("alerte"), cree_le=d.get("cree_le", ""), souple=d.get("souple", False),
                   prolonger_max_min=d.get("prolonger_max_min", 0), ecourter_max_min=d.get("ecourter_max_min", 0),
                   debut_ajuste=datetime.fromisoformat(d["debut_ajuste"]) if d.get("debut_ajuste") else None,
                   fin_ajustee=datetime.fromisoformat(d["fin_ajustee"]) if d.get("fin_ajustee") else None)


class Agenda:
    def __init__(self, chemin: Path | str, lecture_seule: bool = False):
        self.chemin = Path(chemin)
        self.lecture_seule = lecture_seule
        self.evenements: list[Evenement] = []
        if self.chemin.exists():
            d = json.loads(self.chemin.read_text(encoding="utf-8"))
            if d.get("version") != VERSION:
                raise ValueError(f"{self.chemin} : version {d.get('version')} inconnue")
            self.evenements = [Evenement.depuis(e) for e in d["evenements"]]

    def sauver(self) -> None:
        if self.lecture_seule:
            return
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self.evenements.sort(key=lambda e: e.debut)
        tmp = self.chemin.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": VERSION, "evenements": [e.en_dict() for e in self.evenements]},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.chemin)

    def ajouter(self, evt: Evenement) -> Evenement:
        self.evenements.append(evt)
        return evt

    def supprimer(self, id_: str) -> Evenement:
        evt = self.trouver(id_)
        self.evenements.remove(evt)
        return evt

    def trouver(self, id_: str) -> Evenement:
        for e in self.evenements:
            if e.id == id_ or e.id.startswith(id_):
                return e
        raise KeyError(id_)

    def du_jour(self, jour: date) -> list[Evenement]:
        return sorted((e for e in self.evenements if e.debut.date() == jour), key=lambda e: e.debut)

    # ------------------------------------------------------------------ export .ics
    def exporter_ics(self, chemin: Path | str) -> Path:
        chemin = Path(chemin)
        lignes = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Guideo//Agenda//FR", "CALSCALE:GREGORIAN",
                  "X-WR-CALNAME:Guideo"]
        horodatage = datetime.now().strftime("%Y%m%dT%H%M%SZ")
        trajets_vus = set()
        for e in sorted(self.evenements, key=lambda e: e.debut):
            description = f"Lieu : {e.lieu.nom}" if e.lieu else "Lieu inconnu"
            description += "\nSouple : la durée peut s'ajuster aux trajets" if e.souple else "\nFixe"
            if e.debut_ajuste or e.fin_ajustee:
                description += f"\nPrévu : {e.debut:%H:%M}–{e.fin_ou_estimee:%H:%M}, ajusté aux trajets"
            if e.alerte:
                description += f"\nAttention : {e.alerte}"
            lignes += _vevent(f"{e.id}@guideo", horodatage, e.debut_effectif, e.fin_effective, e.titre,
                              e.lieu.nom if e.lieu else None, description, e.lieu, "evenement")
            for t in (e.aller, e.retour):
                if t is None or t.id in trajets_vus:
                    continue  # un trajet direct entre deux événements n'apparaît qu'une fois
                trajets_vus.add(t.id)
                desc = t.ligne() + (f"\n{t.note}" if t.note else "")
                uid = hashlib.sha1(t.id.encode()).hexdigest()[:16]
                lignes += _vevent(f"{uid}@guideo-trajet", horodatage, t.depart, t.arrivee,
                                  f"Trajet → {t.destination.nom}", t.origine.nom, desc, None, "trajet")
        lignes.append("END:VCALENDAR")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text("\r\n".join(_plier(l) for l in lignes) + "\r\n", encoding="utf-8")
        return chemin


def _echapper(texte: str) -> str:
    return texte.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _vevent(uid, horodatage, debut, fin, titre, lieu, description, point: Optional[Point], type_: str) -> list[str]:
    lignes = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{horodatage}",
              # heures "flottantes" (heure locale de l'utilisateur, ici Paris)
              f"DTSTART:{debut:%Y%m%dT%H%M%S}", f"DTEND:{fin:%Y%m%dT%H%M%S}",
              f"SUMMARY:{_echapper(titre)}", f"DESCRIPTION:{_echapper(description)}",
              # Marqueurs : un agenda (ou Guideo, en relisant ce fichier) sait qu'un trajet n'est pas un rendez-vous
              f"CATEGORIES:Guideo,{'Trajet' if type_ == 'trajet' else 'Événement'}", f"X-GUIDEO-TYPE:{type_}"]
    if lieu:
        lignes.append(f"LOCATION:{_echapper(lieu)}")
    if point:
        lignes.append(f"GEO:{point.lat:.6f};{point.lon:.6f}")
    lignes.append("END:VEVENT")
    return lignes


def _plier(ligne: str) -> str:
    """Les lignes .ics ne doivent pas dépasser 75 octets : on les replie (RFC 5545)."""
    octets = ligne.encode("utf-8")
    if len(octets) <= 75:
        return ligne
    morceaux, courant = [], b""
    for car in ligne:
        b = car.encode("utf-8")
        if len(courant) + len(b) > (75 if not morceaux else 74):
            morceaux.append(courant.decode("utf-8"))
            courant = b""
        courant += b
    morceaux.append(courant.decode("utf-8"))
    return "\r\n ".join(morceaux)
