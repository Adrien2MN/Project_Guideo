"""Build the labelling tool: one HTML page to mark the transport modes in your traces.

Usage:
    python scripts/make_label_tool.py "data/garmin/*.fit"

Then open data/traces/label_tool.html in Chrome or Edge, label, and click
"Exporter labels.json". Save the file in data/labels/ (keep the name labels.json).
Re-running this script embeds your existing labels, so you can continue later.
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

from guideo.config import load_settings
from guideo.traces.analysis import GAP_SECONDS
from guideo.traces.labels import MODES, load_labels
from guideo.traces.readers import read_trace

TEMPLATE = Path(__file__).with_name("label_tool_template.html")


def trace_payload(path: str) -> dict | None:
    trace = read_trace(path)
    p = trace.points.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    if len(p) < 2:
        return None
    # epoch seconds, independent of the datetime resolution pandas picked
    t = ((p["time"] - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()).round().astype("int64").to_numpy()
    lat, lon = p["lat"].to_numpy(), p["lon"].to_numpy()
    # speed: device value when present, else computed from positions
    from guideo.streets.router import haversine_m
    step = np.r_[0, haversine_m(lon[:-1], lat[:-1], lon[1:], lat[1:])]
    dt = np.r_[1, np.diff(t)].astype(float)
    calc = np.where(dt > 0, step / np.maximum(dt, 1), 0)
    speed = p["speed"].to_numpy(dtype=float)
    speed = np.where(np.isnan(speed), calc, speed) * 3.6

    def clean(a, nd=1):
        return [None if (x is None or (isinstance(x, float) and np.isnan(x))) else round(float(x), nd)
                for x in a]

    gaps = [[int(t[i - 1]), int(t[i])] for i in range(1, len(t)) if t[i] - t[i - 1] > GAP_SECONDS]
    return {
        "id": Path(path).name,
        "sport": trace.label or trace.sport,
        "name": trace.meta.get("activity_name"),
        "t": [int(x) for x in t],
        "lat": [round(float(x), 6) for x in lat],
        "lon": [round(float(x), 6) for x in lon],
        "v": clean(speed),
        "hr": clean(p["hr"].to_numpy(dtype=float), 0),
        "z": clean(p["alt"].to_numpy(dtype=float)),
        "gaps": gaps,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    parser.add_argument("--labels", help="labels.json to continue from (default data/labels/labels.json)")
    args = parser.parse_args()

    settings = load_settings()
    files = sorted({f for pattern in args.files for f in glob.glob(pattern)})
    traces = [p for p in (trace_payload(f) for f in files) if p]
    traces.sort(key=lambda tr: tr["t"][0])
    labels_path = Path(args.labels) if args.labels else settings.data_dir / "labels" / "labels.json"
    labels = load_labels(labels_path)

    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("/*__DATA__*/null", json.dumps(
        {"traces": traces, "labels": labels, "modes": MODES}, ensure_ascii=False,
        separators=(",", ":")).replace("</", "<\\/"))
    out = settings.data_dir / "traces" / "label_tool.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    done = sum(1 for tr in traces if labels.get(tr["id"], {}).get("segments"))
    print(f"{len(traces)} traces ({done} déjà étiquetées) -> {out}")
    print("Ouvre ce fichier dans Chrome ou Edge.")


if __name__ == "__main__":
    main()
