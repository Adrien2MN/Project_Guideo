import networkx as nx
import pytest

from guideo.streets.build import add_grades, fix_structure_elevations
from guideo.streets.elevation import _parse_elevations
from guideo.streets.router import StreetRouter
from guideo.streets.speed import EBIKE, MECHANICAL, WALK


def test_speed_profiles_behave():
    for p in (MECHANICAL, EBIKE):
        assert p.speed_kmh(0.05) < p.speed_kmh(0.0) < p.speed_kmh(-0.05) <= p.max_kmh
    # electric assistance suffers much less on a climb
    assert EBIKE.speed_kmh(0.06) / EBIKE.speed_kmh(0) > MECHANICAL.speed_kmh(0.06) / MECHANICAL.speed_kmh(0)
    assert WALK.speed_kmh(0.0) == pytest.approx(4.8)
    assert WALK.speed_kmh(0.10) < WALK.speed_kmh(0.0)


def test_parse_both_answer_shapes():
    assert _parse_elevations({"elevations": [45.2, -99999]}) == [45.2, None]
    assert _parse_elevations({"elevations": [{"lon": 2, "lat": 48, "z": 31.0}]}) == [31.0]


def hill_graph():
    """A (top, 60 m) -- B (bottom, 30 m), 1 km apart, both directions."""
    G = nx.MultiDiGraph()
    G.add_node("A", x=2.2800, y=48.8650, elevation=60.0)
    G.add_node("B", x=2.2800, y=48.8560, elevation=30.0)
    G.add_edge("A", "B", length=1000.0)
    G.add_edge("B", "A", length=1000.0)
    add_grades(G)
    return G


def test_downhill_is_faster_than_uphill():
    router = StreetRouter(hill_graph())
    top, bottom = (2.28, 48.865), (2.28, 48.856)
    down = router.route(top, bottom, MECHANICAL)
    up = router.route(bottom, top, MECHANICAL)
    assert down.descent_m == 30 and up.ascent_m == 30
    assert down.duration_s < up.duration_s / 1.5  # 3 % over 1 km: ~3.6 vs ~6.9 min


def test_mechanical_goes_around_a_hill_electric_goes_over():
    # S and T at river level; short way over a 30 m hill, or a flat 900 m detour.
    G = nx.MultiDiGraph()
    G.add_node("S", x=2.000, y=48.000, elevation=30.0)
    G.add_node("H", x=2.000, y=48.0027, elevation=60.0)
    G.add_node("T", x=2.000, y=48.0054, elevation=30.0)
    G.add_node("F", x=2.006, y=48.0027, elevation=30.0)
    for u, v, length in (("S", "H", 300), ("H", "T", 300), ("S", "F", 450), ("F", "T", 450)):
        G.add_edge(u, v, length=float(length))
    add_grades(G)
    r = StreetRouter(G)
    start, end = (2.0, 48.0), (2.0, 48.0054)
    assert r.route(start, end, MECHANICAL).distance_m == pytest.approx(900)  # detour
    assert r.route(start, end, EBIKE).distance_m == pytest.approx(600)       # over the hill


def test_bridge_nodes_get_bank_elevation():
    # Bank (35 m) -- bridge middle (DEM says 27 m: the river) -- bank (35 m)
    G = nx.MultiDiGraph()
    G.add_node("L", x=0, y=0, elevation=35.0)
    G.add_node("M", x=0, y=0, elevation=27.0)
    G.add_node("R", x=0, y=0, elevation=35.0)
    G.add_node("X", x=0, y=0, elevation=35.0)
    for u, v in (("L", "M"), ("M", "R")):
        G.add_edge(u, v, length=100.0, bridge="yes")
        G.add_edge(v, u, length=100.0, bridge="yes")
    G.add_edge("X", "L", length=50.0)  # L is on solid ground
    G.add_edge("R", "X", length=50.0)
    fix_structure_elevations(G)
    assert G.nodes["M"]["elevation"] == 35.0
