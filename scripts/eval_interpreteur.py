"""Évaluation de l'interpréteur Guideo sur le jeu de phrases tests/eval/phrases.jsonl.

Chaque phrase passe par toute la chaîne (LLM + code + mémoire), avec une mémoire de test FIXE
(tests/eval/memoire_eval.json) : le score ne dépend donc pas de ce que ta vraie mémoire a appris,
et deux versions du code ou deux modèles sont comparables. Le résolveur cinéma n'est pas lancé :
on évalue la compréhension, pas les séances du jour.

Utilisation (depuis la racine du projet, venv activé, Ollama lancé) :
    python scripts/eval_interpreteur.py
    python scripts/eval_interpreteur.py -v                      # détail de chaque erreur
    python scripts/eval_interpreteur.py --filtre cine           # seulement les id contenant "cine"
    python scripts/eval_interpreteur.py --modele qwen3:8b       # comparer un autre modèle
    python scripts/eval_interpreteur.py --phrases tests/eval/phrases_2.jsonl

Règles de comparaison (champs de "attendu") :
    valeur exacte -> doit être égale (film, zone, chez : sans tenir compte des majuscules ni des accents)
    null          -> le champ doit être vide
    "*"           -> le champ doit être rempli, peu importe la valeur
    champ absent  -> non vérifié
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    import guideo  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from guideo.interpreteur import analyser
from guideo.interpreteur.comprendre import MODELE, comprendre
from guideo.memoire import Memoire, charger_connaissances, normaliser

RACINE = Path(__file__).resolve().parent.parent
PHRASES = RACINE / "tests" / "eval" / "phrases.jsonl"
MEMOIRE = RACINE / "tests" / "eval" / "memoire_eval.json"
RESULTATS = RACINE / "data" / "eval"          # ignoré par git : les sorties brutes restent locales
CHAMPS_TEXTE = {"film", "zone", "chez", "titre"}


def charger(chemin: Path) -> list[dict]:
    cas = []
    for n, ligne in enumerate(chemin.read_text(encoding="utf-8").splitlines(), 1):
        if not ligne.strip():
            continue
        try:
            cas.append(json.loads(ligne))
        except json.JSONDecodeError as e:
            sys.exit(f"{chemin.name}, ligne {n} : JSON invalide ({e.msg}, colonne {e.colno})")
    return cas


def comparer(attendu, obtenu, champ) -> bool:
    if attendu == "*":
        return obtenu not in (None, "", [])
    if attendu is None:
        return obtenu in (None, "", [])
    if obtenu is None:
        return False
    if champ in CHAMPS_TEXTE:
        return normaliser(str(attendu)) == normaliser(str(obtenu))
    if champ == "avec":
        return sorted(map(normaliser, attendu)) == sorted(map(normaliser, obtenu))
    return attendu == obtenu


def evaluer(cas: dict, memoire: Memoire, connaissances, modele: str) -> dict:
    t0 = time.perf_counter()
    try:
        a = analyser(cas["phrase"], datetime.fromisoformat(cas["maintenant"]), memoire, connaissances,
                     lambda p: comprendre(p, modele))
    except Exception as e:  # sortie illisible, Ollama injoignable...
        return {"id": cas["id"], "erreur": f"{type(e).__name__}: {e}", "champs": {},
                "duree_s": round(time.perf_counter() - t0, 1)}
    sortie = a.en_dict()
    champs = {}
    for champ, att in cas["attendu"].items():
        if champ not in sortie:
            champs[champ] = {"ok": False, "attendu": att, "obtenu": "(champ inconnu)"}
        else:
            champs[champ] = {"ok": comparer(att, sortie[champ], champ), "attendu": att, "obtenu": sortie[champ]}
    return {"id": cas["id"], "phrase": cas["phrase"], "champs": champs,
            "duree_s": round(time.perf_counter() - t0, 1), "sortie": sortie}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phrases", type=Path, default=PHRASES)
    parser.add_argument("--memoire", type=Path, default=MEMOIRE)
    parser.add_argument("--modele", default=MODELE)
    parser.add_argument("--filtre", help="ne garder que les id contenant ce texte")
    parser.add_argument("-v", "--verbose", action="store_true", help="afficher le détail des erreurs")
    args = parser.parse_args()

    memoire = Memoire(args.memoire, lecture_seule=True)
    connaissances = charger_connaissances()
    cas = [c for c in charger(args.phrases) if not args.filtre or args.filtre in c["id"]]
    print(f"Modèle : {args.modele} — {len(cas)} phrase(s) — mémoire : {args.memoire.name}\n")

    resultats, par_champ = [], defaultdict(lambda: [0, 0])
    for c in cas:
        r = evaluer(c, memoire, connaissances, args.modele)
        resultats.append(r)
        if "erreur" in r:
            print(f"  ERREUR {r['id']:10} {r['erreur']}")
            continue
        ok, total = sum(v["ok"] for v in r["champs"].values()), len(r["champs"])
        for champ, v in r["champs"].items():
            par_champ[champ][0] += v["ok"]
            par_champ[champ][1] += 1
        rates = [ch for ch, v in r["champs"].items() if not v["ok"]]
        print(f"  {'OK    ' if not rates else 'RATÉ  '}{r['id']:10} {ok}/{total}  {r['duree_s']:>4}s  {', '.join(rates)}")
        if args.verbose and rates:
            for ch in rates:
                v = r["champs"][ch]
                print(f"           {ch}: attendu {v['attendu']!r}, obtenu {v['obtenu']!r}")
            s = r["sortie"]
            print(f"           compris : activite={s['activite']!r} zone={s['zone']!r} chez={s['chez']!r} "
                  f"avec={s['avec']} film={s['film']!r}")
            print(f"           raisonnement : {s['raisonnement']}")

    valides = [r for r in resultats if "erreur" not in r]
    parfaits = sum(all(v["ok"] for v in r["champs"].values()) for r in valides)
    n_ok = sum(v[0] for v in par_champ.values())
    n_tot = sum(v[1] for v in par_champ.values())
    print(f"\nPhrases entièrement justes : {parfaits}/{len(cas)}")
    print(f"Champs justes : {n_ok}/{n_tot} ({100 * n_ok / max(n_tot, 1):.0f} %)")
    print("Par champ : " + ", ".join(f"{ch} {a}/{b}" for ch, (a, b) in sorted(par_champ.items())))
    if valides:
        print(f"Temps moyen : {sum(r['duree_s'] for r in valides) / len(valides):.1f} s par phrase")

    RESULTATS.mkdir(parents=True, exist_ok=True)
    nom = args.modele.replace(":", "_").replace("/", "_")
    fichier = RESULTATS / f"resultats_{args.phrases.stem}_{nom}_{datetime.now():%Y%m%d_%H%M}.jsonl"
    fichier.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in resultats) + "\n", encoding="utf-8")
    print(f"Détail enregistré dans {fichier.relative_to(RACINE)}")


if __name__ == "__main__":
    main()
