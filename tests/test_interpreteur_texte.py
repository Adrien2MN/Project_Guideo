from datetime import date, datetime

import pytest

from guideo.interpreteur.texte import chez_dit, date_dite, heures_dites, mots_dits, plage_dite

JEUDI = datetime(2026, 11, 5, 15, 0)


@pytest.mark.parametrize("phrase, attendu", [
    ("demain soir", date(2026, 11, 6)),
    ("après-demain", date(2026, 11, 7)),
    ("lundi", date(2026, 11, 9)),
    ("jeudi", date(2026, 11, 12)),             # dit un jeudi : le jeudi suivant
    ("vendredi en huit", date(2026, 11, 13)),
    ("dans trois jours", date(2026, 11, 8)),
    ("ce week-end", date(2026, 11, 7)),
    ("le 12 novembre", date(2026, 11, 12)),
    ("le 3", date(2026, 12, 3)),               # le 3 est passé : le mois prochain
    ("12/11", date(2026, 11, 12)),
    ("tout à l'heure", date(2026, 11, 5)),
    ("je vais au resto avec Nico", None),
])
def test_date_dite(phrase, attendu):
    assert date_dite(phrase, JEUDI) == attendu


@pytest.mark.parametrize("phrase, attendu", [
    ("à 22h", ("22:00", None)),
    ("22 h 30", ("22:30", None)),
    ("19:30", ("19:30", None)),
    ("de 19h à 21h", ("19:00", "21:00")),
    ("19h-21h", ("19:00", "21:00")),
    ("entre 18h et 20h30", ("18:00", "20:30")),
    ("soirée jusqu'à minuit", (None, "00:00")),
    ("à midi", ("12:00", None)),
    ("cet après-midi", (None, None)),
    ("le 12/11 à 20h", ("20:00", None)),
    ("Dune 3 à beaugrenelle", (None, None)),
    ("dans 3 jours", (None, None)),
])
def test_heures_dites(phrase, attendu):
    assert heures_dites(phrase) == attendu


@pytest.mark.parametrize("phrase, attendu", [
    ("ce soir", "soir"), ("je déjeune chez Nico", "midi"), ("cet aprem", "apres-midi"),
    ("en sortant du boulot", "soir"), ("je dîne demain", "soir"), ("samedi", None),
])
def test_plage_dite(phrase, attendu):
    assert plage_dite(phrase) == attendu


def test_mots_dits_corrige_ou_ignore():
    assert mots_dits("Beaubourg", "je compte mater Dune 3 à beaugrenelle") == "beaugrenelle"
    assert mots_dits("Dune III", "je compte mater Dune 3 à beaugrenelle") == "Dune 3"
    assert mots_dits("Montparnasse", "soirée chez Nico samedi") is None   # jamais dit


def test_chez_dit():
    assert chez_dit("Nico", "je vais au resto avec Nico") is None
    assert chez_dit("chez mes parents", "je passe chez mes parents") == "mes parents"
