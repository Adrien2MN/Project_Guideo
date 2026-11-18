import hashlib
from datetime import date, datetime

import pytest

from guideo.agenda import Agenda, Evenement, Parametres, planifier_jour
from guideo.trajets import Calculateur, Point

# Sans PRIM ni graphes : estimations à vol d'oiseau, déterministes
MAISON = Point("domicile", "chez toi", 48.8641, 2.2719)           # avenue Henri-Martin
ISSY = Point("escalade_arkose_issy", "Arkose Issy", 48.8265, 2.2641)
UGC = Point("cinema_ugc", "UGC Montparnasse", 48.8436, 2.3245)
JOUR = date(2026, 11, 20)


def h(heure: str) -> datetime:
    return datetime.combine(JOUR, datetime.strptime(heure, "%H:%M").time())


@pytest.fixture
def agenda(tmp_path):
    return Agenda(tmp_path / "agenda.json")


def cinema(agenda, debut="17:30", fin="20:44"):
    return agenda.ajouter(Evenement("Les Misérables", "cinema", h(debut), h(fin), UGC))


def escalade(agenda, debut, fin, souple=True):
    """Activité souple : peut s'allonger d'1 h ou se raccourcir de 45 min."""
    return agenda.ajouter(Evenement("Escalade", "escalade", h(debut), h(fin), ISSY,
                                    souple=souple, prolonger_max_min=60 if souple else 0,
                                    ecourter_max_min=45 if souple else 0))


def rendez_vous(agenda, debut, fin, lieu=ISSY):
    """Activité fixe, au même endroit que l'escalade."""
    return agenda.ajouter(Evenement("Rendez-vous", "rendez-vous", h(debut), h(fin), lieu))


def test_niveau_1_depuis_et_vers_le_domicile(agenda):
    e = cinema(agenda)
    planifier_jour(agenda, JOUR, Calculateur(), MAISON)
    assert e.aller.origine == MAISON and e.aller.arrivee == h("17:25")   # 5 min de marge
    assert e.retour.destination == MAISON and e.retour.depart == h("20:44")
    assert e.alerte is None


def test_niveau_2_repasse_chez_soi(agenda):
    esc, cine = escalade(agenda, "14:00", "16:00"), cinema(agenda)
    assert planifier_jour(agenda, JOUR, Calculateur(), MAISON) == []
    assert esc.retour.destination == MAISON and cine.aller.origine == MAISON
    assert "repasser chez toi" in cine.aller.note and esc.fin_ajustee is None


# --- après une activité FIXE : on part dès la fin --------------------------------------------
def test_fixe_on_part_a_la_fin_quitte_a_arriver_en_avance(agenda):
    rdv, cine = rendez_vous(agenda, "14:00", "16:45"), cinema(agenda)
    planifier_jour(agenda, JOUR, Calculateur(), MAISON)
    assert cine.aller.origine == ISSY and rdv.retour is cine.aller       # un seul trajet, partagé
    assert cine.aller.depart == h("16:45") and cine.aller.arrivee < h("17:25")
    assert "en avance" in cine.aller.note and rdv.fin_ajustee is None and cine.alerte is None


def test_fixe_suivi_d_un_fixe_en_retard(agenda):
    rendez_vous(agenda, "14:00", "17:20")
    cine = cinema(agenda)
    planifier_jour(agenda, JOUR, Calculateur(), MAISON)
    assert cine.aller.depart == h("17:20") and "après le début" in cine.alerte


# --- après une activité SOUPLE : on ajuste sa durée -----------------------------------------
def test_souple_prolongee_au_lieu_d_attendre(agenda):
    esc, cine = escalade(agenda, "14:00", "16:45"), cinema(agenda)
    planifier_jour(agenda, JOUR, Calculateur(), MAISON)
    assert esc.fin_ajustee == cine.aller.depart > h("16:45")
    assert cine.aller.arrivee == h("17:25") and "prolongé" in cine.aller.note


def test_souple_ecourtee_pour_arriver_a_l_heure(agenda):
    esc, cine = escalade(agenda, "14:00", "17:15"), cinema(agenda)
    planifier_jour(agenda, JOUR, Calculateur(), MAISON)
    assert esc.fin_ajustee == cine.aller.depart < h("17:15")
    assert cine.aller.arrivee == h("17:25") and "écourté" in cine.aller.note and cine.alerte is None


def test_souple_ecourtee_au_maximum_puis_retard(agenda):
    esc, cine = escalade(agenda, "14:00", "17:50"), cinema(agenda)   # finit après le début du film
    planifier_jour(agenda, JOUR, Calculateur(), MAISON)
    assert esc.fin_ajustee == h("17:05")                               # 17:50 - 45 min au maximum
    assert "chevauche" in cine.alerte and "après le début" in cine.alerte


def test_arrivee_en_retard_a_une_activite_souple(agenda):
    cine = cinema(agenda, "14:00", "17:20")
    esc = agenda.ajouter(Evenement("Escalade", "escalade", h("17:30"), h("19:30"), ISSY,
                                   souple=True, prolonger_max_min=60, ecourter_max_min=45))
    planifier_jour(agenda, JOUR, Calculateur(), MAISON)
    assert cine.retour.depart == h("17:20")                            # le film est fixe
    assert esc.debut_ajuste == esc.aller.arrivee > h("17:30") and esc.alerte is None


# --- les trajets ne sont pas des événements -----------------------------------------------
def test_les_trajets_sont_remplaces_pas_figes(agenda, tmp_path):
    cine = cinema(agenda)
    planifier_jour(agenda, JOUR, Calculateur(), MAISON)
    ancien = cine.aller
    agenda.exporter_ics(tmp_path / "avant.ics")
    escalade(agenda, "14:00", "16:45")                                  # nouvel événement juste avant
    planifier_jour(agenda, JOUR, Calculateur(), MAISON)
    assert len(agenda.evenements) == 2                                  # les trajets ne sont pas dans la liste
    assert cine.aller.id != ancien.id and cine.aller.origine == ISSY    # l'ancien aller depuis chez toi a disparu
    ics = agenda.exporter_ics(tmp_path / "apres.ics").read_bytes().decode()
    assert hashlib.sha1(ancien.id.encode()).hexdigest()[:16] not in ics   # absent du nouvel export
    assert ics.count("X-GUIDEO-TYPE:trajet") == 3 and ics.count("X-GUIDEO-TYPE:evenement") == 2


def test_un_trajet_n_est_pas_un_obstacle(agenda):
    cine = cinema(agenda)
    planifier_jour(agenda, JOUR, Calculateur(), MAISON)
    debut_trajet = cine.aller.depart                                    # ~16:59, depuis chez toi
    appel = rendez_vous(agenda, "16:30", "17:00", lieu=MAISON)          # pendant ce trajet
    planifier_jour(agenda, JOUR, Calculateur(), MAISON)
    assert appel.alerte is None and cine.alerte is None                 # pas de conflit avec un trajet
    assert cine.aller.depart == h("17:00") > debut_trajet               # le trajet s'est décalé


def test_long_trou_on_part_de_chez_soi(agenda):
    esc, cine = escalade(agenda, "08:00", "10:00"), cinema(agenda)
    planifier_jour(agenda, JOUR, Calculateur(), MAISON, Parametres(fenetre_h=6))
    assert esc.retour.destination == MAISON and cine.aller.origine == MAISON


def test_persistance_et_ics(agenda, tmp_path):
    escalade(agenda, "14:00", "16:45")
    cinema(agenda)
    planifier_jour(agenda, JOUR, Calculateur(), MAISON)
    agenda.sauver()
    relu = Agenda(agenda.chemin)
    assert [e.titre for e in relu.du_jour(JOUR)] == ["Escalade", "Les Misérables"]
    assert relu.du_jour(JOUR)[1].aller.origine == ISSY
    ics = relu.exporter_ics(tmp_path / "guideo.ics").read_bytes().decode("utf-8")
    assert ics.count("BEGIN:VEVENT") == 5            # 2 événements + 3 trajets (le direct n'est pas compté 2 fois)
    assert "DTSTART:20261120T173000" in ics and "SUMMARY:Trajet → UGC Montparnasse" in ics
    assert all(len(l.encode()) <= 75 for l in ics.split("\r\n"))
    assert all(l.endswith("00") for l in ics.split("\r\n") if l.startswith(("DTSTART", "DTEND")))  # à la minute


class FauxPrim:
    """Réponse au format Navitia : un trajet RER C + marche."""

    def journeys(self, depart, arrivee, quand, **params):
        assert params["datetime_represents"] == "arrival"
        return {"journeys": [{
            "departure_date_time": "20261120T165200", "arrival_date_time": "20261120T172300",
            "sections": [
                {"type": "street_network", "mode": "walking", "duration": 300},
                {"type": "public_transport", "duration": 1200,
                 "display_informations": {"commercial_mode": "Métro", "code": "6"}},
                {"type": "street_network", "mode": "walking", "duration": 360},
            ]}]}


def test_trajet_prim():
    t = Calculateur(prim=FauxPrim()).arriver(MAISON, UGC, h("17:25"))
    assert (t.mode, t.source, t.depart, t.arrivee) == ("transports", "prim", h("16:52"), h("17:23"))
    assert t.resume == "marche 5 min → Métro 6 → marche 6 min"
