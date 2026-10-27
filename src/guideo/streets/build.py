"""Build the street graphs: download from OpenStreetMap, add elevations and grades.

Two graphs are built, because walking and cycling do not use the same ways
(stairs and parks for one, cycle lanes and contraflows for the other).

Known simplifications, to revisit later:
- The bike graph is made bidirectional. In Paris most one-way streets in 30 km/h
  zones allow contraflow cycling, but not all of them.
- Elevations come from a terrain model, which ignores bridges: a node in the
  middle of a bridge gets the river's height. `fix_structure_elevations`
  re-interpolates those nodes from the riverbanks.
"""

from __future__ import annotations

import pickle
import time
from pathlib import Path
from statistics import mean

import networkx as nx

from .elevation import IgnElevationClient

# (west, south, east, north): Paris plus the edge of the inner suburbs
DEFAULT_BBOX = (2.20, 48.79, 2.47, 48.92)
MAX_GRADE = 0.20
EXTRA_TAGS = ["bridge", "tunnel", "oneway:bicycle", "cycleway", "surface", "highway"]


# Public Overpass servers (OpenStreetMap query API). The main one is often saturated,
# so we fall back to community mirrors.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
    "https://overpass.private.coffee/api",
]


def download(network_type: str, bbox=DEFAULT_BBOX, rounds: int = 4) -> nx.MultiDiGraph:
    """Download a street graph, retrying with backoff and switching servers."""
    import osmnx as ox  # heavy import, only needed when building

    ox.settings.useful_tags_way = list(dict.fromkeys(ox.settings.useful_tags_way + EXTRA_TAGS))
    ox.settings.bidirectional_network_types = ["walk", "bike"]
    ox.settings.requests_timeout = 300

    last_exc = None
    for attempt in range(rounds):
        for endpoint in OVERPASS_ENDPOINTS:
            ox.settings.overpass_url = endpoint
            try:
                return ox.graph_from_bbox(bbox, network_type=network_type, simplify=True)
            except Exception as exc:  # osmnx raises several error types for busy servers
                last_exc = exc
                first_line = str(exc).strip().splitlines()[0][:120] if str(exc).strip() else ""
                print(f"  {endpoint} indisponible ({type(exc).__name__}: {first_line})")
        wait = 30 * 2 ** attempt
        if attempt < rounds - 1:
            print(f"  Tous les serveurs ont échoué, nouvel essai dans {wait} s...")
            time.sleep(wait)
    raise RuntimeError("Téléchargement OpenStreetMap impossible, réessaie plus tard.") from last_exc


def _flag(value) -> bool:
    values = value if isinstance(value, list) else [value]
    return any(v not in (None, "no", False) for v in values)


def _is_structure(data: dict) -> bool:
    return _flag(data.get("bridge")) or _flag(data.get("tunnel"))


def fix_structure_elevations(G: nx.MultiDiGraph, max_passes: int = 100) -> int:
    """Re-interpolate nodes that sit only on bridges/tunnels, or have no elevation.

    Elevations spread inwards from the solid ground at each end. Returns how
    many nodes were fixed.
    """
    pending = set()
    for n in G.nodes:
        edges = list(G.in_edges(n, data=True)) + list(G.out_edges(n, data=True))
        if G.nodes[n].get("elevation") is None or (edges and all(_is_structure(d) for _, _, d in edges)):
            pending.add(n)
    known = set(G.nodes) - pending
    fixed = 0
    for _ in range(max_passes):
        updates = {}
        for n in pending:
            neighbours = set(G.predecessors(n)) | set(G.successors(n))
            values = [G.nodes[m]["elevation"] for m in neighbours if m in known]
            if values:
                updates[n] = mean(values)
        if not updates:
            break
        for n, z in updates.items():
            G.nodes[n]["elevation"] = z
        known |= set(updates)
        pending -= set(updates)
        fixed += len(updates)
    for n in pending:  # isolated leftovers: assume flat Paris average
        G.nodes[n]["elevation"] = 35.0
    return fixed


def add_grades(G: nx.MultiDiGraph) -> None:
    """Grade of each directed edge: positive when climbing from u to v."""
    for u, v, d in G.edges(data=True):
        rise = G.nodes[v]["elevation"] - G.nodes[u]["elevation"]
        grade = rise / max(d.get("length", 0.0), 1.0)
        d["grade"] = max(-MAX_GRADE, min(MAX_GRADE, grade))


def add_elevations(G: nx.MultiDiGraph, client: IgnElevationClient) -> int:
    points = {n: (d["x"], d["y"]) for n, d in G.nodes(data=True)}
    for n, z in client.elevations(points).items():
        G.nodes[n]["elevation"] = z
    fixed = fix_structure_elevations(G)
    add_grades(G)
    return fixed


def save_graph(G: nx.MultiDiGraph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_graph(path: Path) -> nx.MultiDiGraph:
    # Only load graph files you built yourself: pickle can run code when loading.
    with open(path, "rb") as f:
        return pickle.load(f)
