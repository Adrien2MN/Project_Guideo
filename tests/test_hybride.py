"""Trajets hybrides sur de vraies réponses Navitia (enregistrées le 26/10/2026 vers 19 h)
et de vraies positions de stations Vélib, avec un faux routeur vélo qui tient compte du relief.

Les vitesses sont celles mesurées sur tes traces du 4/11 (médianes en Vélib mécanique :
~20 km/h en légère descente, 14 à plat, ~11 en légère montée ; marche 6,1 km/h).
"""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from guideo.streets.router import haversine_m
from guideo.streets.speed import BikeProfile
from guideo.trajets import Calculateur, Point
from guideo.trajets.cout import ParametresCout
from guideo.trajets.hybride import ParametresHybride
from guideo.velib.stations import Station, StationsVelib

FIX = Path(__file__).parent / "fixtures" / "hybride"
MAISON = Point("domicile", "chez toi", 48.86413, 2.27193)          # avenue Henri-Martin
ISSY = Point("escalade_arkose_issy", "Arkose Issy", 48.82650, 2.26407)
SOIR = datetime(2026, 10, 26, 19, 0)


def h(hhmm: str) -> datetime:
    return datetime.combine(SOIR.date(), datetime.strptime(hhmm, "%H:%M").time())


class PrimEnregistre:
    """Rejoue la réponse Navitia enregistrée pour le trajet demandé."""

    def __init__(self):
        self.appels = 0

    def journeys(self, depart, arrivee, quand, **params):
        self.appels += 1
        nom = "henri_martin_to_arkose_issy" if depart[1] > arrivee[1] else "arkose_issy_to_henri_martin"
        return json.loads((FIX / f"{nom}.json").read_text(encoding="utf-8"))


# Altitudes approximatives (m) de quelques repères : la colline de Passy / Henri-Martin
# domine la Seine (Javel, Issy) d'une vingtaine de mètres.
REPERES = [(2.2719, 48.8641, 53), (2.2868, 48.8631, 61), (2.2748, 48.8570, 45), (2.2797, 48.8535, 32),
           (2.2897, 48.8565, 34), (2.2780, 48.8472, 29), (2.2705, 48.8389, 30), (2.2641, 48.8265, 30)]
MESURE = BikeProfile("velib_mesure", flat_kmh=14.2, uphill_k=0.12, downhill_k=0.15, max_kmh=27.0)
PARAMS = ParametresHybride(profil_velo=MESURE, marche_kmh=6.1)


def altitude(lon, lat):
    poids = [(1 / max(float(haversine_m(lon, lat, x, y)), 1.0) ** 2, z) for x, y, z in REPERES]
    return sum(w * z for w, z in poids) / sum(w for w, _ in poids)


class VeloRelief:
    """Routeur vélo factice : 1,25 × la ligne droite, pente moyenne entre les deux bouts."""

    def __init__(self):
        self.appels = 0

    def route(self, a, b, profil):
        self.appels += 1
        m = float(haversine_m(*a, *b)) * 1.25
        pente = (altitude(*b) - altitude(*a)) / max(m, 1.0)
        return SimpleNamespace(duration_s=m / (profil.speed_kmh(pente) / 3.6), distance_m=m)


def stations(statut=None, maintenant=lambda: SOIR):
    data = json.loads((FIX / "stations.json").read_text(encoding="utf-8"))
    liste = [Station(s["station_id"], s["name"], s["lat"], s["lon"], s["capacity"]) for s in data]
    return StationsVelib(liste, statut=statut, maintenant=maintenant)


def calc(**kw):
    kw.setdefault("stations", stations())
    kw.setdefault("parametres_hybride", PARAMS)
    return Calculateur(prim=PrimEnregistre(), rues_velo=VeloRelief(), **kw)


# --- les deux cas qui ont motivé Guideo ------------------------------------------------------
def test_aller_velib_jusqu_a_javel_puis_rer_c():
    t = calc().arriver(MAISON, ISSY, h("19:50"))
    assert t.mode == "hybride" and t.source == "hybride"
    assert t.resume.startswith("Vélib") and "jusqu'à Javel" in t.resume and "RER C" in t.resume
    assert "Métro" not in t.resume and t.resume.count("RER C") == 1        # plus de changement à Champ de Mars
    assert t.arrivee <= h("19:50")
    assert any(a.startswith("transports") for a in t.alternatives)


def test_retour_rer_c_puis_velib_au_lieu_de_la_correspondance():
    t = calc().partir(ISSY, MAISON, h("19:10"))
    assert t.mode == "hybride" and t.depart >= h("19:10")
    assert "RER C jusqu'à" in t.resume and "→ Vélib" in t.resume and t.resume.count("RER C") == 1
    # Javel ou Champ de Mars selon le relief : c'est le vrai graphe qui tranchera (cf. note de passation)


# --- c'est la fonction de coût qui tranche ---------------------------------------------------
def test_sans_penalite_de_correspondance_le_rer_avec_changement_gagne():
    sans = ParametresCout(correspondance_min=0, velib_inconnu_min=0)
    t = calc(parametres_cout=sans).arriver(MAISON, ISSY, h("19:50"))
    assert t.mode == "transports" and t.resume.count("RER C") == 2         # C + C via Champ de Mars


def test_velib_risque_ecarte_quand_la_station_est_vide():
    vide = {s.id: (0, 0, 20) for s in stations().stations}                  # aucune station n'a de vélo
    t = calc(stations=stations(statut=lambda: vide)).arriver(MAISON, ISSY, h("19:15"))
    assert t is None or t.mode != "hybride"


def test_stations_verifiees_si_depart_imminent():
    plein = {s.id: (3, 2, 10) for s in stations().stations}
    st = stations(statut=lambda: plein, maintenant=lambda: h("19:10"))
    t = calc(stations=st).arriver(MAISON, ISSY, h("19:50"))
    assert t.mode == "hybride" and "non vérifiées" not in t.resume


# --- robustesse -----------------------------------------------------------------------------
def test_sans_stations_connues_temps_forfaitaire():
    t = Calculateur(prim=PrimEnregistre(), rues_velo=VeloRelief(), parametres_hybride=PARAMS).arriver(MAISON, ISSY, h("19:50"))
    assert t.mode in ("hybride", "velo") and "non vérifiées" in t.resume   # forfait de 4 min, signalé
    assert any(a.startswith(("hybride", "velo")) for a in t.alternatives + [t.mode])


def test_hybrides_desactivables():
    t = calc(hybrides=False).arriver(MAISON, ISSY, h("19:50"))
    assert t.mode != "hybride"


def test_un_seul_appel_prim_et_cache():
    c = calc()
    c.arriver(MAISON, ISSY, h("19:50"))
    velo = c.rues_velo.appels
    c.arriver(MAISON, ISSY, h("19:50"))
    assert c.prim.appels == 1 and c.rues_velo.appels == velo              # PRIM partagé avec les hybrides, tout en cache


def test_routeur_qui_echoue_n_empeche_pas_le_trajet():
    class Casse:
        def route(self, *a):
            raise RuntimeError("graphe absent ici")
    t = Calculateur(prim=PrimEnregistre(), rues_velo=Casse(), stations=stations()).arriver(MAISON, ISSY, h("19:50"))
    assert t is not None and t.mode == "transports"


def test_stations_proches():
    st = stations()
    proches = st.proches(MAISON.lon, MAISON.lat, 450, n=3)
    assert proches and all(d <= 450 for _, d in proches)
    assert [d for _, d in proches] == sorted(d for _, d in proches)
    assert st.disponible(proches[0][0], h("23:00"), "velo") is None       # trop loin pour l'état actuel
