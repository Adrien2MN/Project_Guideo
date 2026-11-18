"""Phrase -> événement : le LLM comprend, le code complète avec la mémoire et les connaissances générales.

Ordre de priorité, pour chaque information :
    ce que l'utilisateur a dit  >  sa réponse à une question  >  son habitude (mémoire)
    >  connaissance générale  >  question.
Chaque valeur retenue garde son origine (`origines`), pour pouvoir expliquer la proposition.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from guideo.memoire import Connaissances, Memoire, normaliser

from .comprendre import Comprehension, comprendre
from .texte import chez_dit, date_dite, heures_dites, mots_dits, plage_dite

MOI = {"moi", "nous", "la maison", "moi meme"}


@dataclass
class Manque:
    champ: str                  # date | heure | lieu | lieu_choix | contact | contact_adresse | contact_choix
                                # | domicile | film | zone
    question: str
    options: list[str] = field(default_factory=list)   # ids d'entités de la mémoire, pour les choix
    texte: Optional[str] = None                         # les mots dits (zone, prénom)


@dataclass
class Analyse:
    phrase: str
    activite: str
    titre: str
    raisonnement: str = ""
    zone: Optional[str] = None
    chez: Optional[str] = None
    avec: list[str] = field(default_factory=list)
    film: Optional[str] = None
    date: Optional[str] = None           # AAAA-MM-JJ
    plage: Optional[str] = None          # moment de la journée dit
    heure_debut: Optional[str] = None    # HH:MM
    heure_fin: Optional[str] = None
    debut: Optional[str] = None          # AAAA-MM-JJTHH:MM (assemblé par le code)
    fin: Optional[str] = None
    lieu_id: Optional[str] = None
    manques: list[Manque] = field(default_factory=list)
    origines: dict[str, str] = field(default_factory=dict)

    @property
    def question(self) -> Optional[str]:
        return self.manques[0].question if self.manques else None

    def fixer(self, champ: str, valeur, origine: str) -> None:
        """Valeur imposée (dite ou répondue) : `completer` ne la remplacera pas."""
        setattr(self, champ, valeur)
        self.origines[champ] = origine

    def en_dict(self) -> dict:
        d = asdict(self)
        d["question"] = self.question
        return d


# ---------------------------------------------------------------------------- étape 1 : lire la phrase
def analyser(phrase: str, maintenant: datetime, memoire: Memoire, connaissances: Connaissances,
             comprendre_fn: Callable[[str], Comprehension] = comprendre) -> Analyse:
    c = comprendre_fn(phrase)
    a = Analyse(phrase=phrase, activite=c.activite, titre=c.titre, raisonnement=c.raisonnement,
                zone=mots_dits(c.zone, phrase), chez=chez_dit(c.chez, phrase),
                avec=[p for p in (mots_dits(x, phrase) for x in c.avec) if p],
                film=mots_dits(c.film, phrase) if c.activite == "cinema" else None)

    debut, fin = heures_dites(phrase)
    jour = date_dite(phrase, maintenant)
    if jour is None and (debut or fin):
        jour = maintenant.date()  # une heure sans jour : aujourd'hui
    if jour:
        a.fixer("date", jour.isoformat(), "dit")
    if debut:
        a.fixer("heure_debut", debut, "dit")
    if fin:
        a.fixer("heure_fin", fin, "dit")
    a.plage = plage_dite(phrase)
    return completer(a, memoire, connaissances)


def appliquer_horaire(a: Analyse, texte: str, origine: str = "réponse") -> bool:
    """Applique une réponse ou une correction d'horaire ("19h30", "de 19h à 21h", "le soir")."""
    debut, fin = heures_dites(texte)
    if debut or fin:
        if debut:
            a.fixer("heure_debut", debut, origine)
        if fin:
            a.fixer("heure_fin", fin, origine)
        elif debut:
            a.origines.pop("heure_fin", None)  # nouvelle heure de début : la fin sera recalculée
        return True
    plage = plage_dite(texte)
    if plage:
        a.plage = plage
        a.origines.pop("heure_debut", None)
        a.origines.pop("heure_fin", None)
        return True
    return False


# ---------------------------------------------------------------------------- étape 2 : compléter
def completer(a: Analyse, memoire: Memoire, connaissances: Connaissances) -> Analyse:
    """(Re)calcule lieu, horaires et questions. Rappelé après chaque réponse de l'utilisateur."""
    a.manques = []
    if a.activite == "cinema":
        _completer_cinema(a)
    else:
        _resoudre_lieu(a, memoire)
        _resoudre_horaires(a, memoire, connaissances)
    _assembler(a)
    if a.date is None:
        a.manques.append(Manque("date", "Quel jour ?"))
    elif a.activite != "cinema" and a.heure_debut is None:
        a.manques.append(Manque("heure", "À quelle heure ?"))
    return a


def _completer_cinema(a: Analyse) -> None:
    # Le cinéma et la séance sont trouvés dans les vraies séances (résolveur cinéma) : pas de fin ici.
    if not a.film:
        a.manques.append(Manque("film", "Quel film ?"))
    if not a.zone:
        a.manques.append(Manque("zone", "Dans quel quartier ?"))


def _nom(memoire: Memoire, id_: str) -> str:
    e = memoire.entite(id_)
    return e["nom"] if e else id_


def _resoudre_lieu(a: Analyse, memoire: Memoire) -> None:
    a.origines.pop("lieu_auto", None)
    if "lieu_id" in a.origines:          # fixé par une réponse de l'utilisateur
        return
    a.lieu_id = None
    categorie = a.activite if a.activite != "autre" else None

    # 1. "chez quelqu'un"
    if a.chez:
        if normaliser(a.chez) in MOI:
            if memoire.domicile:
                a.lieu_id, a.origines["lieu_auto"] = "domicile", "dit"
            else:
                a.manques.append(Manque("domicile", "Quelle est ton adresse ?"))
            return
        contacts = memoire.chercher(a.chez, types=("contacts",))
        if len(contacts) == 1:
            a.lieu_id, a.origines["lieu_auto"] = contacts[0]["id"], "dit"
            if contacts[0].get("lat") is None:
                a.manques.append(Manque("contact_adresse", f"Où habite {contacts[0]['nom']} ?", [contacts[0]["id"]]))
        elif contacts:
            a.manques.append(Manque("contact_choix", f"Quel {a.chez} ?", [c["id"] for c in contacts], a.chez))
        else:
            a.manques.append(Manque("contact", f"Où habite {a.chez} ?", texte=a.chez))
        return

    # 2. un lieu connu nommé dans la phrase
    trouves = memoire.chercher(a.zone, types=("lieux",), categorie=categorie) if a.zone else []
    trouves = trouves or memoire.trouver_dans_phrase(a.phrase, ("lieux",), categorie=categorie)
    if len(trouves) == 1:
        a.lieu_id, a.origines["lieu_auto"] = trouves[0]["id"], "dit"
        if a.activite == "autre":
            a.activite = trouves[0]["categorie"]
        return
    if len(trouves) > 1:
        a.manques.append(Manque("lieu_choix", "Lequel ?", [l["id"] for l in trouves], a.zone))
        return

    # 3. un lieu dit mais inconnu de la mémoire
    connus = memoire.lieux(categorie) if categorie else []
    if a.zone:
        a.manques.append(Manque("lieu", f"C'est quoi, « {a.zone} » ?", [l["id"] for l in connus], a.zone))
        return

    # 4. rien de dit : l'habitude, ou le seul lieu connu pour cette activité
    habituel = memoire.lieu_habituel(a.activite) if categorie else None
    if habituel:
        a.lieu_id = habituel["lieu"]["id"]
        a.origines["lieu_auto"] = f"habitude ({round(100 * habituel['part'])} % des {habituel['n']} dernières fois)"
    elif len(connus) == 1:
        a.lieu_id, a.origines["lieu_auto"] = connus[0]["id"], "seul lieu connu"
    elif connus:
        noms = " ou ".join(l["nom"] for l in connus[:3])
        a.manques.append(Manque("lieu_choix", f"Où ça : {noms} ?", [l["id"] for l in connus]))
    else:
        a.manques.append(Manque("lieu", "Où ça ?"))


def _ajouter_minutes(hhmm: str, minutes: int) -> str:
    t = datetime(2000, 1, 1, int(hhmm[:2]), int(hhmm[3:])) + timedelta(minutes=minutes)
    return t.strftime("%H:%M")


def _resoudre_horaires(a: Analyse, memoire: Memoire, connaissances: Connaissances) -> None:
    lieu = a.lieu_id if a.lieu_id and a.lieu_id != "domicile" else None
    habitude = memoire.horaires_habituels(a.activite, plage=a.plage, lieu_id=lieu)
    a.origines.pop("heure_debut_auto", None)
    a.origines.pop("heure_fin_auto", None)

    if "heure_debut" not in a.origines:
        a.heure_debut = None
        if habitude:
            a.heure_debut = habitude["debut"]
            a.origines["heure_debut_auto"] = f"habitude ({habitude['n']} fois)"
        else:
            defaut = connaissances.debut_type(a.activite, a.plage)
            if defaut:
                a.heure_debut, a.origines["heure_debut_auto"] = defaut

    if "heure_fin" not in a.origines:
        a.heure_fin = None
        if a.heure_debut:
            if habitude and habitude.get("duree_min"):
                a.heure_fin = _ajouter_minutes(a.heure_debut, habitude["duree_min"])
                a.origines["heure_fin_auto"] = f"habitude ({habitude['duree_min']} min)"
            elif connaissances.fin_type(a.activite, a.plage):
                a.heure_fin = connaissances.fin_type(a.activite, a.plage)
                a.origines["heure_fin_auto"] = f"{a.activite} (général)"
            elif connaissances.duree_type(a.activite):
                duree = connaissances.duree_type(a.activite)
                a.heure_fin = _ajouter_minutes(a.heure_debut, duree)
                a.origines["heure_fin_auto"] = f"{a.activite} : {duree} min (général)"


def _assembler(a: Analyse) -> None:
    """Date + heures -> début et fin complets ; une fin avant le début tombe le lendemain."""
    if a.date is None or a.heure_debut is None:
        a.debut = a.fin = None
        return
    d = date.fromisoformat(a.date)
    a.debut = f"{d}T{a.heure_debut}"
    if a.heure_fin and a.activite != "cinema":
        jour_fin = d + timedelta(days=1) if a.heure_fin <= a.heure_debut else d
        a.fin = f"{jour_fin}T{a.heure_fin}"
    else:
        a.fin = None


def expliquer(a: Analyse, memoire: Memoire) -> str:
    """Pourquoi Guideo propose ça (affiché à l'utilisateur)."""
    morceaux = []
    if a.lieu_id:
        morceaux.append(f"lieu : {a.origines.get('lieu_id') or a.origines.get('lieu_auto', '?')}")
    o = a.origines
    debut = o.get("heure_debut") or o.get("heure_debut_auto")
    fin = o.get("heure_fin") or o.get("heure_fin_auto")
    if debut:
        morceaux.append(f"début : {debut}")
    if fin:
        morceaux.append(f"fin : {fin}")
    return " ; ".join(morceaux)
