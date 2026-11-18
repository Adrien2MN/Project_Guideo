"""Voir et gérer l'agenda de Guideo (événements + trajets aller/retour).

Utilisation (racine du projet, venv activé) :
    python scripts/agenda.py                          # les 7 prochains jours
    python scripts/agenda.py --jour 2026-11-20        # une journée
    python scripts/agenda.py --supprimer ID           # retire un événement et recalcule sa journée
    python scripts/agenda.py --replanifier 2026-11-20 # recalcule les trajets d'une journée (ex. après un changement de domicile)

À chaque modification, data/agenda/guideo.ics est réécrit : importe-le (ou abonne-toi) dans ton agenda.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

try:
    import guideo  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from guideo.agenda import Agenda, Parametres, planifier_jour
from guideo.config import load_settings
from guideo.memoire import Memoire, charger_connaissances
from guideo.memoire.memoire import JOURS
from guideo.trajets import Point, calculateur_par_defaut

DATA = load_settings().data_dir
AGENDA, ICS, MEMOIRE = DATA / "agenda" / "agenda.json", DATA / "agenda" / "guideo.ics", DATA / "memoire" / "memoire.json"


def afficher_jour(agenda: Agenda, jour: date) -> None:
    evts = agenda.du_jour(jour)
    if not evts:
        return
    print(f"\n{JOURS[jour.weekday()].capitalize()} {jour:%d/%m}")
    for e in evts:
        if e.aller:
            print(f"   {e.aller.depart:%H:%M}  ↳ trajet depuis {e.aller.origine.nom} ({e.aller.duree_min} min, {e.aller.resume})"
                  + (f" — {e.aller.note}" if e.aller.note else ""))
        fin = f"–{e.fin:%H:%M}" if e.fin else ""
        print(f"   {e.debut:%H:%M}{fin}  {e.titre} · {e.lieu.nom if e.lieu else 'lieu inconnu'}   [{e.id}]")
        if e.alerte:
            print(f"          /!\\ {e.alerte}")
        if e.retour and (e.retour.destination.id == "domicile"):
            print(f"   {e.retour.depart:%H:%M}  ↳ retour chez toi, arrivée {e.retour.arrivee:%H:%M} ({e.retour.resume})")


def replanifier(agenda: Agenda, jour: date) -> None:
    memoire = Memoire(MEMOIRE, lecture_seule=True)
    d = memoire.domicile
    domicile = Point("domicile", "chez toi", d["lat"], d["lon"]) if d else None
    for message in planifier_jour(agenda, jour, calculateur_par_defaut(load_settings()), domicile,
                                  Parametres.depuis(charger_connaissances().trajets)):
        print(f"   {message}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jour", type=date.fromisoformat)
    parser.add_argument("--supprimer", metavar="ID")
    parser.add_argument("--replanifier", type=date.fromisoformat, metavar="JOUR")
    args = parser.parse_args()
    agenda = Agenda(AGENDA)

    if args.supprimer:
        evt = agenda.supprimer(args.supprimer)
        print(f"Supprimé : {evt.titre} ({evt.debut:%d/%m %H:%M})")
        replanifier(agenda, evt.debut.date())
        args.jour = evt.debut.date()
    if args.replanifier:
        replanifier(agenda, args.replanifier)
        args.jour = args.replanifier
    if args.supprimer or args.replanifier:
        agenda.sauver()
        agenda.exporter_ics(ICS)
        print(f"[{ICS} mis à jour]")

    jours = [args.jour] if args.jour else [date.today() + timedelta(days=i) for i in range(7)]
    for jour in jours:
        afficher_jour(agenda, jour)
    if not any(agenda.du_jour(j) for j in jours):
        print("Rien de prévu." if args.jour else "Rien de prévu dans les 7 prochains jours.")


if __name__ == "__main__":
    main()
