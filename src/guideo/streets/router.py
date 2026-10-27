"""Fastest path on a street graph, with slope-aware travel times (A* search)."""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
import numpy as np

from .speed import edge_seconds

EARTH_RADIUS_M = 6_371_000


def haversine_m(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))


@dataclass
class Route:
    profile: str
    duration_s: float
    distance_m: float
    ascent_m: float
    descent_m: float
    start_elevation_m: float
    end_elevation_m: float
    coords: list[tuple[float, float]] = field(repr=False)  # (lon, lat)

    def summary(self) -> str:
        return (f"{self.profile:<17} {self.duration_s / 60:5.1f} min  "
                f"{self.distance_m / 1000:4.2f} km  "
                f"+{self.ascent_m:3.0f} m / -{self.descent_m:3.0f} m")


class StreetRouter:
    def __init__(self, G: nx.MultiDiGraph):
        self.G = G
        self._ids = list(G.nodes)
        self._lon = np.array([G.nodes[n]["x"] for n in self._ids])
        self._lat = np.array([G.nodes[n]["y"] for n in self._ids])

    def nearest_node(self, lon: float, lat: float) -> tuple[int, float]:
        d = haversine_m(lon, lat, self._lon, self._lat)
        i = int(np.argmin(d))
        return self._ids[i], float(d[i])

    def _best_edge(self, u, v, profile) -> dict:
        return min(self.G[u][v].values(),
                   key=lambda d: edge_seconds(d["length"], d["grade"], profile))

    def route(self, start: tuple[float, float], end: tuple[float, float], profile) -> Route:
        """Fastest route between two (lon, lat) points for a speed profile."""
        s, snap_start = self.nearest_node(*start)
        t, snap_end = self.nearest_node(*end)
        vmax = profile.max_kmh / 3.6
        nodes = self.G.nodes

        def weight(u, v, edges):
            return min(edge_seconds(d["length"], d["grade"], profile) for d in edges.values())

        def heuristic(a, b):  # admissible: nobody beats a straight line at max speed
            return float(haversine_m(nodes[a]["x"], nodes[a]["y"],
                                     nodes[b]["x"], nodes[b]["y"])) / vmax

        path = nx.astar_path(self.G, s, t, heuristic=heuristic, weight=weight)

        # Getting on/off the network: straight line at flat speed
        snap = snap_start + snap_end
        duration = snap / (profile.speed_kmh(0.0) / 3.6)
        distance, ascent, descent = snap, 0.0, 0.0
        coords = [start, (nodes[s]["x"], nodes[s]["y"])]
        for u, v in zip(path[:-1], path[1:]):
            d = self._best_edge(u, v, profile)
            duration += edge_seconds(d["length"], d["grade"], profile)
            distance += d["length"]
            rise = nodes[v]["elevation"] - nodes[u]["elevation"]
            ascent += max(rise, 0.0)
            descent += max(-rise, 0.0)
            geom = d.get("geometry")
            if geom is not None:
                pts = list(geom.coords)
                if pts and pts[0] != (nodes[u]["x"], nodes[u]["y"]):
                    pts.reverse()  # reversed edge of a bidirectional street
                coords.extend(pts[1:])
            else:
                coords.append((nodes[v]["x"], nodes[v]["y"]))
        coords.append(end)
        return Route(profile.name, duration, distance, ascent, descent,
                     nodes[s]["elevation"], nodes[t]["elevation"], coords)
