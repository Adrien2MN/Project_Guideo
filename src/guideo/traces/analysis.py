"""From a raw trace to the modelling table: one row per street segment ridden.

Steps:
1. `add_motion`: distance, time step, speed and climb between consecutive points;
   flags signal gaps (metro/RER underground, watch paused...).
2. `match_to_graph`: attach each point to the nearest street segment, with the
   direction of travel. This is a simple nearest-segment matching, not a full
   HMM map matcher: good enough for a precise watch GPS, worth revisiting for
   noisy phone traces.
3. `segment_table`: aggregate consecutive points on the same segment.

The output columns are what the speed models will be trained on:
measured speed vs IGN grade (from the graph) and barometric grade (from the
watch), plus context (previous grade, recent climb, effort, elapsed time).
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd

from guideo.streets.router import haversine_m

GAP_SECONDS = 20          # no point for this long = signal lost or paused
STOP_SPEED = 1.0          # m/s, below this you are considered stopped
MAX_MATCH_DIST = 25.0     # m, points further from any street are left unmatched
MIN_SEGMENT_M = 30.0      # shorter matched runs are too noisy to learn from


def add_motion(points: pd.DataFrame) -> pd.DataFrame:
    df = points.dropna(subset=["lat", "lon"]).reset_index(drop=True).copy()
    lat, lon = df["lat"].to_numpy(), df["lon"].to_numpy()
    step = np.zeros(len(df))
    step[1:] = haversine_m(lon[:-1], lat[:-1], lon[1:], lat[1:])
    dt = df["time"].diff().dt.total_seconds().fillna(0).to_numpy()
    df["step_m"] = step
    df["dt_s"] = dt
    df["gap"] = dt > GAP_SECONDS
    with np.errstate(divide="ignore", invalid="ignore"):
        df["speed_calc"] = np.where(dt > 0, step / dt, np.nan)
    df["dz_m"] = df["alt"].diff().fillna(0)
    # A gap is not movement: do not count its distance/time/climb in segments
    df.loc[df["gap"], ["step_m", "dt_s", "dz_m"]] = 0.0
    return df


def summarize(df: pd.DataFrame) -> dict:
    moving = df["speed_calc"] >= STOP_SPEED
    dz = df["dz_m"]
    return {
        "debut": df["time"].iloc[0],
        "fin": df["time"].iloc[-1],
        "duree_min": (df["time"].iloc[-1] - df["time"].iloc[0]).total_seconds() / 60,
        "distance_km": df["step_m"].sum() / 1000,
        "vitesse_moy_kmh": df["step_m"].sum() / max(df["dt_s"].sum(), 1) * 3.6,
        "vitesse_en_mouvement_kmh":
            df.loc[moving, "step_m"].sum() / max(df.loc[moving, "dt_s"].sum(), 1) * 3.6,
        "temps_arret_min": df.loc[~moving, "dt_s"].sum() / 60,
        "denivele_plus_m": dz[dz > 0].sum(),
        "denivele_moins_m": -dz[dz < 0].sum(),
        "fc_moy": df["hr"].mean(),
        "coupures": int(df["gap"].sum()),
    }


def _edge_frame(G: nx.MultiDiGraph):
    """Undirected list of edges with their geometry, for spatial lookup."""
    from shapely.geometry import LineString

    seen, rows = set(), []
    for u, v, k, d in G.edges(keys=True, data=True):
        key = (min(u, v), max(u, v), k)
        if key in seen:
            continue
        seen.add(key)
        geom = d.get("geometry") or LineString(
            [(G.nodes[u]["x"], G.nodes[u]["y"]), (G.nodes[v]["x"], G.nodes[v]["y"])])
        rows.append((u, v, k, geom))
    return rows


class GraphMatcher:
    """Nearest-segment lookup on a street graph (built once, reused for all traces)."""

    def __init__(self, G: nx.MultiDiGraph):
        from shapely import STRtree

        self.G = G
        self.edges = _edge_frame(G)
        self.tree = STRtree([e[3] for e in self.edges])
        # rough degrees -> metres conversion around Paris
        self.m_per_deg_lat = 111_320.0
        self.m_per_deg_lon = 111_320.0 * np.cos(np.radians(48.86))

    def match(self, df: pd.DataFrame) -> pd.DataFrame:
        from shapely import points as make_points

        df = df.copy()
        pts = make_points(df["lon"].to_numpy(), df["lat"].to_numpy())
        idx, dist_deg = self.tree.query_nearest(pts, return_distance=True, all_matches=False)
        # query_nearest returns (input index, tree index) pairs
        order = np.argsort(idx[0])
        tree_idx = idx[1][order]
        dist_m = dist_deg[order] * self.m_per_deg_lon  # conservative (lon scale < lat scale)

        u = np.array([self.edges[i][0] for i in tree_idx])
        v = np.array([self.edges[i][1] for i in tree_idx])
        k = np.array([self.edges[i][2] for i in tree_idx])
        pos = np.array([self.edges[i][3].project(p, normalized=True)
                        for i, p in zip(tree_idx, pts)])
        df["edge_u"], df["edge_v"], df["edge_k"] = u, v, k
        df["edge_pos"] = pos
        df["match_dist_m"] = dist_m
        df.loc[df["match_dist_m"] > MAX_MATCH_DIST, ["edge_u", "edge_v"]] = -1
        return df


def segment_table(df: pd.DataFrame, G: nx.MultiDiGraph) -> pd.DataFrame:
    """One row per run of consecutive points on the same street segment."""
    edge_id = list(zip(df["edge_u"], df["edge_v"], df["edge_k"]))
    run = np.zeros(len(df), dtype=int)
    for i in range(1, len(df)):
        run[i] = run[i - 1] + (edge_id[i] != edge_id[i - 1] or bool(df["gap"].iloc[i]))
    df = df.assign(run=run)

    rows = []
    for _, g in df.groupby("run", sort=True):
        u, v, k = g["edge_u"].iloc[0], g["edge_v"].iloc[0], g["edge_k"].iloc[0]
        if u == -1 or len(g) < 3:
            continue
        dist = g["step_m"].iloc[1:].sum()
        dt = g["dt_s"].iloc[1:].sum()
        if dist < MIN_SEGMENT_M or dt <= 0:
            continue
        # direction of travel along the segment geometry
        forward = g["edge_pos"].iloc[-1] >= g["edge_pos"].iloc[0]
        a, b = (u, v) if forward else (v, u)
        data = G.get_edge_data(a, b, k) or G.get_edge_data(a, b, 0) or {}
        moving = g["speed_calc"].iloc[1:] >= STOP_SPEED
        dz = g["dz_m"].iloc[1:].sum()
        rows.append({
            "start": g["time"].iloc[0],
            "from_node": a, "to_node": b,
            "street": _name(data.get("name")),
            "highway": _name(data.get("highway")),
            "edge_length_m": data.get("length", np.nan),
            "grade_ign": data.get("grade", np.nan),
            "dist_m": dist,
            "duration_s": dt,
            "stopped_s": g["dt_s"].iloc[1:][~moving].sum(),
            "speed_kmh": dist / dt * 3.6,
            "moving_speed_kmh": (g["step_m"].iloc[1:][moving].sum()
                                 / max(g["dt_s"].iloc[1:][moving].sum(), 1e-9) * 3.6),
            "dz_baro_m": dz,
            "grade_baro": dz / dist,
            "hr_mean": g["hr"].mean(),
            "match_dist_m": g["match_dist_m"].mean(),
            "label": g["label"].iloc[0],
        })
    seg = pd.DataFrame(rows)
    if seg.empty:
        return seg
    # Context features: what happened just before this segment
    t0 = df["time"].iloc[0]
    seg["elapsed_s"] = (seg["start"] - t0).dt.total_seconds()
    seg["prev_grade_ign"] = seg["grade_ign"].shift(1)
    seg["prev_speed_kmh"] = seg["speed_kmh"].shift(1)
    climb = seg["dz_baro_m"].clip(lower=0)
    seg["climb_before_m"] = climb.cumsum().shift(1, fill_value=0)
    return seg


def _name(value):
    if isinstance(value, list):
        return " / ".join(map(str, value))
    return value
