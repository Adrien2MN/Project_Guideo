"""Planifier les trajets d'une journée : d'où part-on pour chaque événement, et quand ?

Niveau 1 : sans autre événement autour, on part de chez soi et on y rentre.
Niveau 2 : si un événement précède de peu (moins de `fenetre_h` heures), on part de là-bas.
    Si le temps entre les deux permet de repasser chez soi (au moins `temps_min_domicile_min`
    à la maison), on rentre puis on repart ; sinon on va directement de l'un à l'autre :
    - après une activité FIXE (cinéma), on part dès la fin, quitte à arriver en avance ;
    - après une activité SOUPLE (escalade), on la prolonge pour partir juste à temps, ou on
      l'écourte un peu pour ne pas être en retard (dans les limites de l'activité) ;
    - si l'on arrive malgré tout en retard à une activité souple, elle commence plus tard ;
      à une activité fixe, l'événement porte une alerte.

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

    Seuls les événements comptent : les trajets précédemment calculés sont effacés puis refaits,
    ils ne bloquent jamais l'ajout d'un nouvel événement. Les ajustements des événements souples
    sont eux aussi recalculés à partir de l'horaire prévu.
    Les explications sont dans `trajet.note`, les problèmes dans `evenement.alerte` ; la liste
    renvoyée ne contient que ce qui empêche le calcul.
    """
    evts = agenda.du_jour(jour)
    messages: list[str] = []
    for e in evts:
        e.aller = e.retour = None
        e.alerte = e.debut_ajuste = e.fin_ajustee = None
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
                precedent.retour = calc.partir(precedent.lieu, domicile, precedent.fin_effective)
            e.aller = calc.arriver(domicile, e.lieu, cible)
        precedent = e

    if precedent is not None:
        precedent.retour = calc.partir(precedent.lieu, domicile, precedent.fin_effective)
    return messages


def _enchainer(prec: Evenement, e: Evenement, cible: datetime, calc: Calculateur, domicile: Point,
               p: Parametres) -> list[str]:
    fin_prec = prec.fin_ou_estimee
    ecart = e.debut - fin_prec

    # Même endroit : pas de trajet
    if prec.lieu.id == e.lieu.id:
        if ecart < timedelta(0):
            e.alerte = f"chevauche « {prec.titre} » ({duree(-ecart)} en commun)"
        return []

    # Option 1 : repasser chez soi (à partir de la fin prévue)
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

    # Option 2 : y aller directement
    ideal = calc.arriver(prec.lieu, e.lieu, cible)  # le départ le plus tard qui arrive à l'heure
    if ideal is None:
        return []
    marge = ideal.depart - fin_prec                  # > 0 : du battement ; < 0 : il faudrait partir avant la fin
    notes, trajet = [], ideal

    if marge >= timedelta(0):
        if prec.souple and marge >= timedelta(minutes=1):
            # Activité souple : on en profite plus longtemps au lieu d'attendre
            prolongation = min(marge, timedelta(minutes=prec.prolonger_max_min))
            if prolongation >= timedelta(minutes=1):
                prec.fin_ajustee = fin_prec + prolongation
                notes.append(f"« {prec.titre} » prolongé de {duree(prolongation)}")
            if marge > prolongation:
                trajet = calc.partir(prec.lieu, e.lieu, prec.fin_effective)
        elif not prec.souple:
            # Activité fixe : on repart dès la fin, quitte à arriver en avance
            trajet = calc.partir(prec.lieu, e.lieu, fin_prec)
        avance = e.debut - trajet.arrivee
        if avance > timedelta(minutes=p.marge_arrivee_min + 1):
            notes.append(f"arrivée {duree(avance)} en avance")
    else:
        manque = -marge
        if prec.souple and manque <= timedelta(minutes=prec.ecourter_max_min):
            prec.fin_ajustee = ideal.depart
            notes.append(f"« {prec.titre} » écourté de {duree(manque)} pour arriver à l'heure")
        else:
            depart = fin_prec
            if prec.souple and prec.ecourter_max_min:
                depart = fin_prec - timedelta(minutes=prec.ecourter_max_min)
                prec.fin_ajustee = depart
                notes.append(f"« {prec.titre} » écourté au maximum ({prec.ecourter_max_min} min)")
            trajet = calc.partir(prec.lieu, e.lieu, depart)
            retard = trajet.arrivee - e.debut
            if retard > timedelta(0):
                if e.souple and retard <= timedelta(minutes=e.ecourter_max_min):
                    e.debut_ajuste = trajet.arrivee
                    notes.append(f"« {e.titre} » commence {duree(retard)} plus tard (activité souple)")
                else:
                    e.alerte = f"arrivée à {trajet.arrivee:%H:%M}, {duree(retard)} après le début"
                    if ecart < timedelta(0):
                        e.alerte = f"chevauche « {prec.titre} » ({duree(-ecart)} en commun) ; {e.alerte}"

    trajet.note = "direct depuis « " + prec.titre + " »" + "".join(f" ; {n}" for n in notes)
    prec.retour = e.aller = trajet
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
