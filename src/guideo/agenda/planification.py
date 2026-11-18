"""Planifier les trajets d'une journée : d'où part-on pour chaque événement, et quand ?

Niveau 1 : sans autre événement autour, on part de chez soi et on y rentre.
Niveau 2 : si un événement précède de peu (moins de `fenetre_h` heures), on part de là-bas.
    Si le temps entre les deux permet de repasser chez soi (au moins `temps_min_domicile_min`
    à la maison), on rentre puis on repart ; sinon on va directement de l'un à l'autre.
    Si même le trajet direct ne permet pas d'arriver à l'heure, l'événement porte une alerte.

Toute la journée est recalculée à chaque ajout ou suppression : l'événement précédent voit
son trajet retour changer (il ne rentre plus forcément à la maison), le suivant son trajet aller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from guideo.trajets import Calculateur, Point, Trajet

from .agenda import Agenda, Evenement


@dataclass(frozen=True)
class Parametres:
    marge_arrivee_min: int = 5          # arriver un peu avant le début
    temps_min_domicile_min: int = 30    # en dessous, repasser chez soi ne vaut pas le coup
    fenetre_h: float = 6                # au-delà, l'événement précédent ne compte plus

    @classmethod
    def depuis(cls, d: dict) -> "Parametres":
        return cls(**{k: d[k] for k in ("marge_arrivee_min", "temps_min_domicile_min", "fenetre_h") if k in d})


def duree(td: timedelta) -> str:
    minutes = round(td.total_seconds() / 60)
    signe, minutes = ("-" if minutes < 0 else ""), abs(minutes)
    return f"{signe}{minutes // 60} h {minutes % 60:02d}" if minutes >= 60 else f"{signe}{minutes} min"


def planifier_jour(agenda: Agenda, jour: date, calc: Calculateur, domicile: Optional[Point],
                   p: Parametres = Parametres()) -> list[str]:
    """Recalcule les trajets aller et retour de tous les événements du jour.

    Les explications (repasser chez soi ou non) sont dans `trajet.note`, les problèmes dans
    `evenement.alerte` ; la liste renvoyée ne contient que ce qui empêche le calcul.
    """
    evts = agenda.du_jour(jour)
    messages: list[str] = []
    for e in evts:
        e.aller = e.retour = None
        e.alerte = None
    if domicile is None:
        return ["Domicile inconnu : je ne peux pas calculer les trajets."]

    precedent: Optional[Evenement] = None
    for e in evts:
        if e.lieu is None:
            messages.append(f"« {e.titre} » : lieu inconnu, pas de trajet calculé.")
            precedent = None  # on ne sait pas où l'on sera ensuite
            continue
        cible = e.debut - timedelta(minutes=p.marge_arrivee_min)

        if precedent is not None and e.debut - precedent.fin_ou_estimee <= timedelta(hours=p.fenetre_h):
            messages += _enchainer(precedent, e, cible, calc, domicile, p)
        else:
            if precedent is not None:  # long trou : on était rentré entre-temps
                precedent.retour = calc.partir(precedent.lieu, domicile, precedent.fin_ou_estimee)
            e.aller = calc.arriver(domicile, e.lieu, cible)
        precedent = e

    if precedent is not None:
        precedent.retour = calc.partir(precedent.lieu, domicile, precedent.fin_ou_estimee)
    return messages


def _enchainer(prec: Evenement, e: Evenement, cible: datetime, calc: Calculateur, domicile: Point,
               p: Parametres) -> list[str]:
    fin_prec = prec.fin_ou_estimee
    ecart = e.debut - fin_prec
    if ecart < timedelta(0):
        e.alerte = f"chevauche « {prec.titre} » ({duree(-ecart)} en commun)"

    # Même endroit, ou on était chez soi : pas de choix à faire
    if prec.lieu.id == e.lieu.id:
        return []
    if prec.lieu.id == domicile.id:
        e.aller = calc.arriver(domicile, e.lieu, cible)
        return []

    # Option 1 : repasser chez soi
    if ecart > timedelta(0):
        vers_maison = calc.partir(prec.lieu, domicile, fin_prec)
        depuis_maison = calc.arriver(domicile, e.lieu, cible)
        if vers_maison and depuis_maison:
            a_la_maison = depuis_maison.depart - vers_maison.arrivee
            if a_la_maison >= timedelta(minutes=p.temps_min_domicile_min):
                prec.retour, e.aller = vers_maison, depuis_maison
                note = f"tu as le temps de repasser chez toi ({duree(a_la_maison)} à la maison)"
                depuis_maison.note = vers_maison.note = note
                return []

    # Option 2 : y aller directement, en partant le plus tard possible
    direct = calc.arriver(prec.lieu, e.lieu, cible)
    if direct and direct.depart >= fin_prec:
        direct.note = (f"pas le temps de rentrer : direct depuis « {prec.titre} », "
                       f"{duree(direct.depart - fin_prec)} de battement avant de partir")
    else:
        direct = calc.partir(prec.lieu, e.lieu, fin_prec)  # au plus tôt après la fin du précédent
        if direct is None:
            return []
        retard = direct.arrivee - e.debut
        direct.note = f"direct depuis « {prec.titre} »"
        if retard > timedelta(0):
            alerte = f"arrivée à {direct.arrivee:%H:%M}, {duree(retard)} après le début"
            e.alerte = f"{e.alerte} ; {alerte}" if e.alerte else alerte
    prec.retour = e.aller = direct
    return []


def trajets_modifies(avant: dict[str, dict], agenda: Agenda, jour: date) -> list[tuple[Evenement, str, Trajet]]:
    """Les trajets qui ont changé lors d'une replanification (pour les signaler)."""
    changes = []
    for e in agenda.du_jour(jour):
        for sens in ("aller", "retour"):
            t = getattr(e, sens)
            if t is not None and avant.get(e.id, {}).get(sens) not in (None, t.id):
                changes.append((e, sens, t))
    return changes


def photographier(agenda: Agenda, jour: date) -> dict[str, dict]:
    return {e.id: {"aller": e.aller.id if e.aller else None, "retour": e.retour.id if e.retour else None}
            for e in agenda.du_jour(jour)}
