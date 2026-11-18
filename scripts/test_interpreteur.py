"""Interpréteur Guideo en console : phrase -> événement, avec une mémoire qui apprend.

Prérequis (dans le venv du projet, depuis la racine) :
    pip install -e ".[interpreteur]"
Ollama doit tourner, avec le modèle téléchargé.

Utilisation :
    python scripts/test_interpreteur.py --discuter          # dialogue : la mémoire se remplit au fil des réponses
    python scripts/test_interpreteur.py -p "je grimpe ce soir"   # une phrase, mémoire en lecture seule
Options :
    --faux                 séances de cinéma factices (sans Allociné)
    --memoire CHEMIN       autre fichier mémoire (défaut : data/memoire/memoire.json)
    --modele NOM           autre modèle Ollama

Tout ce que Guideo apprend est dans data/memoire/memoire.json (lisible, modifiable),
et chaque modification est tracée dans data/memoire/journal.jsonl.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

try:
    import guideo  # noqa: F401
except ImportError:  # projet pas installé avec pip install -e : on ajoute src/ au chemin
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from guideo.config import load_settings
from guideo.interpreteur import Analyse, Manque, analyser, appliquer_horaire, completer, expliquer
from guideo.interpreteur import cinema as cine
from guideo.interpreteur import geo
from guideo.interpreteur.comprendre import MODELE, comprendre
from guideo.interpreteur.texte import date_dite
from guideo.memoire import Memoire, charger_connaissances, normaliser
from guideo.memoire.memoire import JOURS

DATA = load_settings().data_dir
JOURNAL_GEO = DATA / "geocodage_journal.jsonl"
NOMS_ACTIVITES = {"soiree": "Soirée", "rendez-vous": "Rendez-vous", "cinema": "Cinéma"}


# ============================================================================ saisie console
def texte(question: str) -> str:
    return input(f"   {question} > ").strip()


def oui(question: str, defaut: bool = True) -> bool:
    rep = texte(f"{question} [{'O/n' if defaut else 'o/N'}]").lower()
    return defaut if not rep else rep.startswith("o")


def choix(question: str, options: list[str]) -> Optional[int]:
    """Question à choix : renvoie l'index choisi, ou None pour 'aucun'."""
    print(f"\n   ? {question}")
    for i, o in enumerate(options, 1):
        print(f"     {i}. {o}")
    print("     0. aucun (ou Entrée)")
    while True:
        rep = texte("numéro") or "0"
        if rep.isdigit() and 0 <= int(rep) <= len(options):
            return int(rep) - 1 if int(rep) else None
        print("   (tape un numéro)")


# ============================================================================ affichage
def nom_lieu(memoire: Memoire, lieu_id: Optional[str]) -> str:
    if not lieu_id:
        return "lieu inconnu"
    e = memoire.entite(lieu_id)
    if e is None:
        return lieu_id
    return f"chez {e['nom']}" if lieu_id.startswith("contact_") else e["nom"]


def resume(a: Analyse, memoire: Memoire) -> str:
    morceaux = [a.titre or NOMS_ACTIVITES.get(a.activite, a.activite.capitalize()), nom_lieu(memoire, a.lieu_id)]
    if a.date:
        d = date.fromisoformat(a.date)
        morceaux.append(f"{JOURS[d.weekday()]} {d:%d/%m}")
    if a.debut:
        morceaux.append(f"{a.heure_debut} → {a.heure_fin}" if a.heure_fin else f"à {a.heure_debut}")
    return " · ".join(morceaux)


# ============================================================================ géocodage interactif
def reference(memoire: Memoire) -> tuple[float, float]:
    d = memoire.domicile
    return (d["lat"], d["lon"]) if d else geo.PARIS


def chercher_adresse(adresse: str, memoire: Memoire) -> Optional[dict]:
    candidats = geo.classer(geo.candidats_ign(adresse, index=("address",), limite=5), reference(memoire))
    if not candidats:
        print("   Adresse introuvable.")
        return None
    if len(candidats) == 1 or candidats[0]["score"] > 0.85:
        return candidats[0] if oui(f"C'est bien : {candidats[0]['nom']} ?") else None
    i = choix("Laquelle ?", [f"{c['nom']} ({c['km']:.1f} km)" for c in candidats[:5]])
    return None if i is None else candidats[i]


def demander_domicile(memoire: Memoire) -> None:
    print("\n   Je ne connais pas encore ton adresse. Elle sert de point de départ et à choisir")
    print("   le bon lieu parmi les homonymes. Elle reste sur ton ordinateur (data/memoire).")
    adresse = texte("Ton adresse (Entrée pour passer)")
    if adresse:
        c = chercher_adresse(adresse, memoire)
        if c:
            memoire.definir_domicile(c["nom"], c["lat"], c["lon"], source="reponse")
            print("   [mémoire : domicile enregistré]")


def nouveau_lieu(a: Analyse, dit: Optional[str], memoire: Memoire) -> bool:
    recherche = texte(f"Comment s'appelle ce lieu, ou son adresse ?{f' [{dit}]' if dit else ''}") or dit
    if not recherche:
        return False
    candidats = geo.classer(geo.candidats_nominatim(recherche) + geo.candidats_ign(recherche, index=("address",)),
                            reference(memoire))
    # Lieux nommés (OpenStreetMap) avant les simples adresses, puis du plus proche au plus loin
    candidats = sorted(candidats, key=lambda c: (c["km"] > geo.DISTANCE_MAX_KM, c["source"] != "osm", c["km"]))[:6]
    if not candidats:
        print("   Rien trouvé. Essaie avec le nom complet ou l'adresse.")
        return False
    i = choix("Lequel ?", [f"{c['nom']} — {c['adresse'][:70]} ({c['km']:.1f} km)" for c in candidats])
    geo.journaliser(JOURNAL_GEO, recherche, candidats, None if i is None else candidats[i], par="utilisateur")
    if i is None:
        return False
    c = candidats[i]
    nom = texte(f"Nom à retenir [{c['nom']}]") or c["nom"]
    alias = list({normaliser(x): x for x in (dit, recherche) if x and normaliser(x) != normaliser(nom)}.values())
    lieu = memoire.ajouter_lieu(nom, a.activite, c["lat"], c["lon"], adresse=c["adresse"],
                                alias=alias, source="reponse")
    print(f"   [mémoire : nouveau lieu « {nom} » ({a.activite})"
          + (f", aussi appelé {', '.join(repr(x) for x in alias)}" if alias else "") + "]")
    a.fixer("lieu_id", lieu["id"], "réponse")
    return True


def adresse_contact(prenom: str, memoire: Memoire) -> Optional[dict]:
    adresse = texte(f"Où habite {prenom} ? (adresse, ou Entrée pour ne pas la donner)")
    return chercher_adresse(adresse, memoire) if adresse else None


# ============================================================================ questions
def traiter(m: Manque, a: Analyse, memoire: Memoire, maintenant: datetime) -> bool:
    """Pose la question `m` et enregistre la réponse. False si l'utilisateur ne répond pas."""
    if m.champ == "date":
        rep = texte(m.question)
        jour = date_dite(rep, maintenant)
        if jour is None:
            return False
        a.fixer("date", jour.isoformat(), "réponse")
        appliquer_horaire(a, rep)
        return True

    if m.champ == "heure":
        rep = texte(m.question)
        return bool(rep) and appliquer_horaire(a, rep)

    if m.champ in ("film", "zone"):
        rep = texte(m.question)
        if rep:
            a.fixer(m.champ, rep, "réponse")
        return bool(rep)

    if m.champ in ("lieu_choix", "lieu"):
        options = [memoire.entite(i) for i in m.options]
        if options:
            question = m.question if m.champ == "lieu_choix" else f"{m.question} L'un de ceux-là ?"
            i = choix(question, [o["nom"] for o in options] + ["un autre lieu"])
            if i is None:
                return False
            if i < len(options):
                a.fixer("lieu_id", options[i]["id"], "réponse")
                return True
        else:
            print(f"   ? {m.question}")
        return nouveau_lieu(a, m.texte, memoire)

    if m.champ == "contact_choix":
        options = [memoire.entite(i) for i in m.options]
        i = choix(m.question, [f"{o['nom']} ({o.get('adresse') or 'adresse inconnue'})" for o in options])
        if i is None:
            return False
        a.fixer("lieu_id", options[i]["id"], "réponse")
        return True

    if m.champ == "contact":
        print(f"   Je ne connais pas encore {m.texte}.")
        c = adresse_contact(m.texte, memoire)
        contact = memoire.ajouter_contact(m.texte, adresse=c and c["nom"], lat=c and c["lat"], lon=c and c["lon"],
                                          source="reponse")
        print(f"   [mémoire : nouveau contact « {m.texte} »{'' if c else ' (sans adresse : pas de trajet possible)'}]")
        a.fixer("lieu_id", contact["id"], "réponse")
        return True

    if m.champ == "contact_adresse":
        contact = memoire.entite(m.options[0])
        c = adresse_contact(contact["nom"], memoire)
        if not c:
            return False
        memoire.modifier(contact["id"], adresse=c["nom"], lat=c["lat"], lon=c["lon"], source="reponse")
        print(f"   [mémoire : adresse de {contact['nom']} enregistrée]")
        return True

    if m.champ == "domicile":
        demander_domicile(memoire)
        return memoire.domicile is not None

    return False


def poser_questions(a: Analyse, memoire: Memoire, connaissances, maintenant: datetime) -> None:
    refusees: set[str] = set()
    for _ in range(10):
        restantes = [m for m in a.manques if m.champ not in refusees]
        if not restantes:
            return
        if not traiter(restantes[0], a, memoire, maintenant):
            refusees.add(restantes[0].champ)
        completer(a, memoire, connaissances)


# ============================================================================ apprentissage
def proposer_alias(mot: Optional[str], entite_id: Optional[str], memoire: Memoire) -> None:
    """Le mot dit ne désignait pas encore ce lieu / ce contact : on propose de le retenir."""
    entite = memoire.entite(entite_id) if entite_id else None
    if not mot or entite is None or entite_id == "domicile" or normaliser(mot) in Memoire.textes(entite):
        return
    if oui(f"Retenir que « {mot} » = {entite['nom']} ?"):
        memoire.ajouter_alias(entite_id, mot, source="choix")
        print(f"   [mémoire : « {mot} » ajouté pour {entite['nom']}]")


def enregistrer(a: Analyse, memoire: Memoire, connaissances) -> None:
    debut = datetime.fromisoformat(a.debut)
    fin = datetime.fromisoformat(a.fin) if a.fin else None
    corrige = "correction" in a.origines.values()
    obs = memoire.observer(a.activite, a.lieu_id, debut, fin, plage=connaissances.plage_de(a.heure_debut),
                           source="correction" if corrige else "confirmation")
    memoire.marquer_utilise(a.lieu_id)
    duree = f", {obs['duree_min']} min" if obs["duree_min"] else ""
    print(f"   [mémoire : {a.activite} le {obs['jour']} à {obs['debut']}{duree} ({obs['source']})]")
    if a.lieu_id and a.lieu_id.startswith("contact_"):
        proposer_alias(a.chez, a.lieu_id, memoire)
    else:
        proposer_alias(a.zone, a.lieu_id, memoire)


def valider(a: Analyse, memoire: Memoire, connaissances, maintenant: datetime) -> None:
    while True:
        if a.debut is None:
            print("   Je n'ai pas de quoi proposer un créneau (jour ou heure manquant).")
            return
        print(f"\n   → {resume(a, memoire)}")
        pourquoi = expliquer(a, memoire)
        if pourquoi:
            print(f"     ({pourquoi})")
        rep = texte("Entrée : valider · h : changer l'horaire · l : changer le lieu · n : annuler").lower()
        if rep in ("", "o", "oui"):
            enregistrer(a, memoire, connaissances)
            return
        if rep == "n":
            print("   Annulé, rien n'est enregistré.")
            return
        if rep == "h":
            heures = texte("Nouvel horaire (ex. 19h30, de 19h à 21h30)")
            if appliquer_horaire(a, heures, origine="correction"):
                completer(a, memoire, connaissances)
        elif rep == "l":
            a.origines.pop("lieu_id", None)
            categorie = [l["id"] for l in memoire.lieux(a.activite)]
            if traiter(Manque("lieu", "Où ça ?", categorie, a.zone), a, memoire, maintenant):
                a.origines["lieu_id"] = "correction"
            completer(a, memoire, connaissances)


def cinema(a: Analyse, source, memoire: Memoire, connaissances, maintenant: datetime, apprendre: bool) -> None:
    choisi = cine.resoudre_cinema(a, source, memoire, connaissances, maintenant, choix, JOURNAL_GEO)
    if choisi is None:
        return
    s = choisi.seance
    print(f"\n   → {s.film} ({s.version}) · {s.cinema.nom} · {JOURS[s.debut.weekday()]} {s.debut:%d/%m}"
          f" · {s.debut:%H:%M} → {choisi.fin:%H:%M}")
    if not apprendre or not oui("Valider ?"):
        return
    lieu = memoire.lieu_par_ref(f"allocine:{s.cinema.id}") or memoire.ajouter_lieu(
        s.cinema.nom, "cinema", s.cinema.lat, s.cinema.lon, adresse=s.cinema.adresse,
        ref=f"allocine:{s.cinema.id}", source="choix")
    nouvelle_zone = not memoire.chercher(choisi.zone, types=("zones",))
    if nouvelle_zone:
        memoire.ajouter_zone(choisi.zone, *choisi.zone_pos, source="confirmation")
    memoire.observer("cinema", lieu["id"], s.debut, choisi.fin, plage=connaissances.plage_de(f"{s.debut:%H:%M}"))
    memoire.marquer_utilise(lieu["id"])
    print(f"   [mémoire : séance à {lieu['nom']} enregistrée"
          + (f" ; zone « {choisi.zone} » retenue" if nouvelle_zone else "") + "]")


# ============================================================================ programme
def traiter_phrase(phrase: str, memoire: Memoire, connaissances, source, modele: str, apprendre: bool) -> None:
    maintenant = datetime.now()
    a = analyser(phrase, maintenant, memoire, connaissances, lambda p: comprendre(p, modele))
    compris = {k: v for k, v in a.en_dict().items()
               if v not in (None, [], {}, "") and k in ("activite", "zone", "chez", "avec", "film", "date", "plage", "lieu_id")}
    print(f"   compris : {compris}")
    if not apprendre:
        print(f"   → {resume(a, memoire)}" + (f"\n   ? {a.question}" if a.question else ""))
        if a.activite == "cinema" and not a.manques:
            cinema(a, source, memoire, connaissances, maintenant, apprendre=False)
        return
    poser_questions(a, memoire, connaissances, maintenant)
    if a.activite == "cinema":
        cinema(a, source, memoire, connaissances, maintenant, apprendre=True)
    else:
        valider(a, memoire, connaissances, maintenant)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--discuter", action="store_true", help="dialogue : questions, réponses, mémoire qui apprend")
    parser.add_argument("-p", "--phrase", action="append", help="phrase à analyser (répétable), sans rien enregistrer")
    parser.add_argument("--faux", action="store_true", help="séances de cinéma factices au lieu d'Allociné")
    parser.add_argument("--memoire", type=Path, help="fichier mémoire (défaut : data/memoire/memoire.json ; "
                                                      "avec --faux : data/memoire/memoire_test.json)")
    parser.add_argument("--modele", default=MODELE)
    args = parser.parse_args()
    if not args.discuter and not args.phrase:
        parser.error("indique --discuter ou -p \"une phrase\"")

    if args.memoire is None:  # les séances factices ne doivent pas polluer la vraie mémoire
        args.memoire = DATA / "memoire" / ("memoire_test.json" if args.faux else "memoire.json")
    memoire = Memoire(args.memoire, lecture_seule=not args.discuter)
    connaissances = charger_connaissances()
    source = cine.SourceFactice() if args.faux else cine.SourceAllocine(cache=DATA / "cinemas_cache.json")
    print(f"Mémoire : {args.memoire} — {memoire.resume()}")

    if args.phrase:
        for phrase in args.phrase:
            print(f"\n>>> {phrase}")
            traiter_phrase(phrase, memoire, connaissances, source, args.modele, apprendre=False)
        return

    if memoire.domicile is None:
        demander_domicile(memoire)
    print("\nDis-moi ce que tu vas faire (Entrée pour quitter, « mémoire » pour voir ce que je sais).")
    while True:
        try:
            phrase = input("\nToi > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not phrase:
            break
        if normaliser(phrase) == "memoire":
            print(f"   {memoire.resume()} — détail dans {args.memoire}")
            continue
        traiter_phrase(phrase, memoire, connaissances, source, args.modele, apprendre=True)
    print(f"\nÀ bientôt. Mémoire : {memoire.resume()}")


if __name__ == "__main__":
    main()
