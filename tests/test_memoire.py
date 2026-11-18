import json
from datetime import datetime

import pytest

from guideo.memoire import Memoire, charger_connaissances


@pytest.fixture
def memoire(tmp_path):
    return Memoire(tmp_path / "memoire.json")


def test_demarre_vide(memoire):
    assert memoire.lieux() == [] and memoire.contacts() == [] and memoire.domicile is None
    assert not memoire.chemin.exists()  # rien n'est écrit tant que rien n'est appris


def test_lieu_alias_et_recherche(memoire):
    lieu = memoire.ajouter_lieu("Arkose Issy", "escalade", 48.82, 2.27, alias=["Issy"])
    assert memoire.chercher("issy", categorie="escalade") == [lieu]
    assert memoire.chercher("Issy", categorie="cinema") == []          # un alias vaut pour sa catégorie
    assert memoire.trouver_dans_phrase("je grimpe à Issy ce soir", categorie="escalade") == [lieu]
    assert memoire.ajouter_alias(lieu["id"], "la salle de voie", source="choix")
    assert not memoire.ajouter_alias(lieu["id"], "ISSY", source="choix")  # doublon ignoré


def test_persistance_et_journal(tmp_path):
    m = Memoire(tmp_path / "memoire.json")
    c = m.ajouter_contact("Nico")
    m.modifier(c["id"], adresse="1 rue X", lat=48.8, lon=2.3, source="reponse")
    relu = Memoire(tmp_path / "memoire.json")
    assert relu.entite(c["id"])["adresse"] == "1 rue X"
    actions = [json.loads(l)["action"] for l in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert actions == ["ajouter_contact", "modifier"]


def test_lecture_seule_n_ecrit_rien(tmp_path):
    m = Memoire(tmp_path / "memoire.json", lecture_seule=True)
    m.ajouter_contact("Nico")
    assert not (tmp_path / "memoire.json").exists() and not (tmp_path / "journal.jsonl").exists()


def test_source_inconnue_refusee(memoire):
    with pytest.raises(ValueError):
        memoire.ajouter_contact("Nico", source="llm")


def test_habitudes_et_lieu_habituel(memoire):
    a = memoire.ajouter_lieu("A", "escalade", 0, 0)
    b = memoire.ajouter_lieu("B", "escalade", 0, 0)
    assert memoire.horaires_habituels("escalade") is None
    for jour, debut, fin, lieu in [(3, 19, 21, a), (5, 19, 22, a), (10, 20, 22, a), (12, 19, 21, b)]:
        memoire.observer("escalade", lieu["id"], datetime(2026, 11, jour, debut), datetime(2026, 11, jour, fin), plage="soir")
    h = memoire.horaires_habituels("escalade", plage="soir")
    assert h == {"debut": "19:00", "duree_min": 120, "n": 4}
    assert memoire.horaires_habituels("escalade", plage="matin") is None
    assert memoire.lieu_habituel("escalade") is None                      # 75 % < 80 % : on demande
    memoire.observer("escalade", a["id"], datetime(2026, 11, 14, 19), datetime(2026, 11, 14, 21), plage="soir")
    assert memoire.lieu_habituel("escalade")["lieu"]["id"] == a["id"]     # 80 % : habitude


def test_supprimer_efface_observations(memoire):
    a = memoire.ajouter_lieu("A", "escalade", 0, 0)
    memoire.observer("escalade", a["id"], datetime(2026, 11, 3, 19), datetime(2026, 11, 3, 21))
    memoire.supprimer(a["id"])
    assert memoire.lieux() == [] and memoire.donnees["observations"] == []


def test_connaissances_generales():
    c = charger_connaissances()
    assert c.debut_type("soiree", "soir")[0] == "20:00"
    assert c.debut_type("repas", "midi")[0] == "12:00"
    assert c.debut_type("escalade", "soir")[0] == "19:00"
    assert c.debut_type("visite", None) is None                          # rien de raisonnable : question
    assert c.plage_de("12:00") == "midi" and c.plage_de("19:30") == "soir"
