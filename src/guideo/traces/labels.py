"""Ground-truth labels: which transport mode, from when to when, in each trace.

Labels are written by the labelling tool (scripts/make_label_tool.py) into a
single JSON file, by default data/labels/labels.json:

    {"version": 1,
     "traces": {
        "24871677504_ACTIVITY.fit": {
            "segments": [
                {"start": "2026-10-27T16:58:10Z", "end": "2026-10-27T17:09:30Z", "mode": "velib-meca"},
                ...],
            "note": "free text",
            "updated": "2026-11-04T13:30:00Z"}}}
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

MODES = {
    "walk": "Marche",
    "velib-meca": "Vélib mécanique",
    "velib-elec": "Vélib électrique",
    "bike": "Vélo perso",
    "metro": "Métro",
    "rer": "RER / Transilien",
    "tram": "Tramway",
    "bus": "Bus",
    "car": "Voiture / taxi",
    "still": "Immobile (bâtiment, attente…)",
    "unknown": "Je ne sais plus",
}
BIKE_MODES = {"velib-meca", "velib-elec", "bike"}
WALK_MODES = {"walk"}


def load_labels(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("traces", {})


def apply_labels(points: pd.DataFrame, segments: list[dict]) -> pd.DataFrame:
    """Set the `label` column from labelled time segments (unlabelled points: NaN)."""
    df = points.copy()
    df["label"] = pd.NA
    for seg in segments:
        start = pd.Timestamp(seg["start"]).tz_convert("UTC")
        end = pd.Timestamp(seg["end"]).tz_convert("UTC")
        mask = (df["time"] >= start) & (df["time"] <= end)
        df.loc[mask, "label"] = seg["mode"]
    return df


def labelled_parts(points: pd.DataFrame):
    """Yield (mode, sub-DataFrame) for each run of consecutive points with the same label."""
    if points.empty:
        return
    run = (points["label"].fillna("") != points["label"].fillna("").shift()).cumsum()
    for _, part in points.groupby(run, sort=True):
        mode = part["label"].iloc[0]
        if pd.notna(mode):
            yield mode, part
