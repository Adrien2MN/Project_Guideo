"""Analyse GPS traces (Garmin .fit or GPSLogger .csv), using your labels.

Usage:
    python scripts/analyze_trace.py "data/garmin/*.fit"

Labels come from data/labels/labels.json (made with scripts/make_label_tool.py).
Only the parts labelled as bike or walk are matched to the streets and compared
with Guideo's speed model; the other parts are summarised. Unlabelled traces are
summarised only, since a "Bike" activity often also contains walking or transit.

Outputs in data/traces/:
    <name>_segments.csv   one row per street segment (the modelling table)
    all_segments.csv      the same, for all analysed traces together
    <name>_map.html       map of the trace coloured by speed
"""

from __future__ import annotations

import argparse
import glob

import pandas as pd

from guideo.config import load_settings
from guideo.streets.build import load_graph
from guideo.streets.speed import EBIKE, MECHANICAL, WALK, edge_seconds
from guideo.traces.analysis import GraphMatcher, add_motion, segment_table, summarize
from guideo.traces.labels import BIKE_MODES, MODES, WALK_MODES, apply_labels, labelled_parts, load_labels
from guideo.traces.readers import read_trace

PROFILES = {"walk": WALK, "bike": MECHANICAL, "velib-meca": MECHANICAL, "velib-elec": EBIKE}


def local(t) -> str:
    return t.tz_convert("Europe/Paris").strftime("%H:%M")


def write_map(df: pd.DataFrame, path, title: str) -> None:
    try:
        import folium
    except ImportError:
        return
    m = folium.Map(location=[df["lat"].mean(), df["lon"].mean()], zoom_start=15)
    kmh = df["speed_calc"].fillna(0).clip(0, 60) * 3.6

    def colour(v):
        return ("#c0392b" if v < 3 else "#e67e22" if v < 6 else "#f1c40f" if v < 12
                else "#7fb800" if v < 18 else "#1e8449")

    for i in range(1, len(df)):
        if not df["gap"].iloc[i]:
            folium.PolyLine([(df["lat"].iloc[i - 1], df["lon"].iloc[i - 1]),
                             (df["lat"].iloc[i], df["lon"].iloc[i])],
                            color=colour(kmh.iloc[i]), weight=5, opacity=0.9).add_to(m)
    m.get_root().html.add_child(folium.Element(
        f"<div style='position:fixed;top:10px;left:50px;z-index:9999;background:white;padding:6px 10px;"
        f"font:14px sans-serif;border-radius:4px'>{title} — rouge &lt;3, orange &lt;6, jaune &lt;12, "
        "vert clair &lt;18, vert foncé ≥18 km/h</div>"))
    m.save(str(path))


def print_summary(df: pd.DataFrame, indent: str = "  ") -> None:
    s = summarize(df)
    print(f"{indent}{s['duree_min']:.1f} min, {s['distance_km']:.2f} km, "
          f"en mouvement {s['vitesse_en_mouvement_kmh']:.1f} km/h, arrêts {s['temps_arret_min']:.1f} min, "
          f"+{s['denivele_plus_m']:.0f}/-{s['denivele_moins_m']:.0f} m"
          + (f", FC {s['fc_moy']:.0f}" if pd.notna(s["fc_moy"]) else ""))


class Graphs:
    def __init__(self, streets_dir):
        self.dir, self.graphs, self.matchers = streets_dir, {}, {}

    def get(self, net):
        if net not in self.graphs:
            path = self.dir / f"{net}.pkl"
            if not path.exists():
                return None, None
            print(f"  (chargement du graphe {net}...)")
            self.graphs[net] = load_graph(path)
            self.matchers[net] = GraphMatcher(self.graphs[net])
        return self.graphs[net], self.matchers[net]


def analyse_part(part: pd.DataFrame, mode: str, graphs: Graphs) -> pd.DataFrame | None:
    net = "bike" if mode in BIKE_MODES else "walk"
    G, matcher = graphs.get(net)
    if G is None:
        print(f"    graphe {net} absent : pas d'appariement")
        return None
    seg = segment_table(matcher.match(part), G)
    if seg.empty:
        print("    aucun tronçon apparié")
        return None
    profile = PROFILES[mode]
    seg["model_s"] = [edge_seconds(d, g, profile) for d, g in zip(seg["dist_m"], seg["grade_ign"])]
    seg["label"] = mode
    real, model = seg["duration_s"].sum(), seg["model_s"].sum()
    share = seg["dist_m"].sum() / max(part["step_m"].sum(), 1)
    print(f"    {len(seg)} tronçons appariés ({share:.0%} de la distance) : "
          f"réel {real / 60:.1f} min, modèle {model / 60:.1f} min ({(model - real) / real:+.0%})")
    return seg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    parser.add_argument("--labels", help="labels file (default data/labels/labels.json)")
    args = parser.parse_args()

    settings = load_settings()
    out_dir = settings.data_dir / "traces"
    out_dir.mkdir(parents=True, exist_ok=True)
    labels = load_labels(args.labels or settings.data_dir / "labels" / "labels.json")
    files = sorted({f for pattern in args.files for f in glob.glob(pattern)} or set(args.files))
    traces = sorted((read_trace(f) for f in files), key=lambda t: t.start or pd.Timestamp.max)
    graphs = Graphs(settings.streets_dir)
    all_segments = []

    for trace in traces:
        name = trace.path.name
        df = add_motion(trace.points)
        if len(df) < 2:
            continue
        print(f"\n=== {name}  {df['time'].iloc[0].tz_convert('Europe/Paris'):%a %d/%m %H:%M}"
              f"  ({trace.meta.get('activity_name') or trace.sport})")
        write_map(df, out_dir / f"{trace.path.stem}_map.html", name)
        segments = labels.get(name, {}).get("segments")
        if not segments:
            print_summary(df)
            print("  non étiquetée : pas de comparaison au modèle")
            continue

        df = apply_labels(df, segments)
        trace_segments = []
        for mode, part in labelled_parts(df):
            if len(part) < 2:
                continue
            print(f"  [{MODES.get(mode, mode)}] {local(part['time'].iloc[0])}–{local(part['time'].iloc[-1])}")
            part = part.copy()
            part.iloc[0, part.columns.get_loc("step_m")] = 0.0  # no distance from the previous part
            part.iloc[0, part.columns.get_loc("dt_s")] = 0.0
            print_summary(part, "    ")
            if mode in BIKE_MODES | WALK_MODES:
                seg = analyse_part(part, mode, graphs)
                if seg is not None:
                    seg.insert(0, "trace", name)
                    trace_segments.append(seg)
        if trace_segments:
            out = pd.concat(trace_segments, ignore_index=True)
            out.to_csv(out_dir / f"{trace.path.stem}_segments.csv", index=False)
            all_segments.append(out)

    if all_segments:
        seg = pd.concat(all_segments, ignore_index=True)
        seg.to_csv(out_dir / "all_segments.csv", index=False)
        print(f"\n=== Bilan sur {seg['trace'].nunique()} trace(s), {len(seg)} tronçons")
        for mode, g in seg.groupby("label"):
            real, model = g["duration_s"].sum(), g["model_s"].sum()
            print(f"  {MODES.get(mode, mode):<18} réel {real / 60:6.1f} min, modèle {model / 60:6.1f} min "
                  f"({(model - real) / real:+.0%})")
            bins = pd.cut(g["grade_ign"] * 100, [-20, -4, -1.5, 1.5, 4, 20])
            table = g.groupby(bins, observed=True).agg(
                tronçons=("dist_m", "size"), km=("dist_m", lambda d: d.sum() / 1000),
                vitesse_mouvement=("moving_speed_kmh", "median"))
            print("    vitesse en mouvement (médiane, km/h) par classe de pente IGN (%) :")
            print("    " + table.round(1).to_string().replace("\n", "\n    "))
        corr = seg[["grade_ign", "grade_baro"]].dropna()
        if len(corr) > 10:
            print(f"  corrélation pente IGN / pente baromètre : {corr.corr().iloc[0, 1]:.2f}")
    print(f"\nFichiers écrits dans {out_dir}")


if __name__ == "__main__":
    main()
