"""Essai des trajets hybrides sur les trajets de référence (scripts/test_trips.toml), en vrai :
PRIM, graphe vélo avec relief, stations Vélib et leur état en temps réel.

Usage :
    python scripts/essai_hybride.py                 # arriver dans 45 min
    python scripts/essai_hybride.py --partir        # partir maintenant
    python scripts/essai_hybride.py --heure 18:30   # arriver à 18:30 aujourd'hui

Affiche, pour chaque trajet, le choix de Guideo, le classement des options avec leurs
pénalités (pour régler la fonction de coût) et le temps de calcul.
"""

from __future__ import annotations

import argparse
import time
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

from guideo.config import load_settings
from guideo.trajets import Point, calculateur_par_defaut
from guideo.trajets.cout import penalite_min

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--partir", action="store_true", help="partir à l'heure donnée (sinon : arriver)")
    parser.add_argument("--heure", help="HH:MM (défaut : maintenant, ou dans 45 min pour arriver)")
    args = parser.parse_args()

    maintenant = datetime.now().replace(second=0, microsecond=0)
    if args.heure:
        heure = datetime.combine(maintenant.date(), datetime.strptime(args.heure, "%H:%M").time())
    else:
        heure = maintenant if args.partir else maintenant + timedelta(minutes=45)
    arriver = not args.partir

    t0 = time.perf_counter()
    calc = calculateur_par_defaut(load_settings())
    print(f"Calculateur prêt en {time.perf_counter() - t0:.1f} s "
          f"(PRIM {'oui' if calc.prim else 'non'}, vélo {'oui' if calc.rues_velo else 'non'}, "
          f"stations {len(calc.stations.stations) if calc.stations else 0})")

    trips = tomllib.loads((ROOT / "scripts" / "test_trips.toml").read_text(encoding="utf-8"))["trip"]
    for trip in trips:
        (olon, olat), (dlon, dlat) = trip["from"], trip["to"]
        o, d = Point("o_" + trip["name"], trip["name"].split("_to_")[0], olat, olon), \
            Point("d_" + trip["name"], trip["name"].split("_to_")[-1], dlat, dlon)
        print(f"\n=== {trip['name']} ({'arriver à' if arriver else 'partir à'} {heure:%H:%M})")
        print(f"    attendu : {trip.get('note', '')}")
        t0 = time.perf_counter()
        trajet = calc.arriver(o, d, heure) if arriver else calc.partir(o, d, heure)
        duree = time.perf_counter() - t0
        print(f"  → {trajet.ligne() if trajet else 'aucun trajet'}   [{duree:.1f} s]")

        # Classement détaillé (relit les caches : pas de nouvel appel)
        opts = (calc._options_prim(o, d, heure, arriver) + calc._options_hybrides(o, d, heure, arriver)
                + calc._options_rues(o, d, heure))
        lignes = []
        for op in opts:
            dep, arr = op.placer(heure, arriver)
            pen = penalite_min(op.correspondances, op.proba_velib, op.avec_velib, calc.cout, op.velo_min)
            score = (dep.timestamp() - pen * 60) if arriver else -(arr.timestamp() + pen * 60)
            lignes.append((score, f"    {op.mode:<10} {dep:%H:%M}→{arr:%H:%M}  pénalité {pen:4.1f} min  {op.resume}"))
        print("  classement :")
        for _, ligne in sorted(lignes, reverse=True)[:8]:
            print(ligne)


if __name__ == "__main__":
    main()
