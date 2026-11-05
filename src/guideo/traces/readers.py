"""Read GPS traces into one common table.

Every reader returns a `Trace`: metadata + a pandas DataFrame with one row per
point and these columns (missing ones are NaN):

    time         timezone-aware UTC timestamp
    lat, lon     degrees
    alt          metres (barometric on a Garmin watch, GPS on a phone)
    speed        m/s as measured by the device
    hr           heart rate, bpm (watch only)
    cadence      rpm / steps per minute (if a sensor provides it)
    accuracy     horizontal accuracy in metres (phone only)
    source       "garmin" or "gpslogger"
    label        transport mode if known ("walk", "bike", "velib-meca"...)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["time", "lat", "lon", "alt", "speed", "hr", "cadence", "accuracy", "source", "label"]
SEMICIRCLE_TO_DEG = 180.0 / 2**31

# Garmin sport -> our label. sub_sport refines it (e.g. e_bike)
GARMIN_SPORTS = {
    "walking": "walk", "hiking": "walk", "running": "run",
    "cycling": "bike", "e_biking": "ebike", "training": None,
}


@dataclass
class Trace:
    path: Path
    source: str
    points: pd.DataFrame
    sport: str | None = None
    sub_sport: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def label(self) -> str | None:
        if self.sub_sport in ("e_bike_fitness", "e_bike_mountain"):
            return "ebike"
        return GARMIN_SPORTS.get(self.sport or "", self.sport)

    @property
    def start(self):
        return self.points["time"].iloc[0] if len(self.points) else None


def _finish(df: pd.DataFrame, source: str, label=None) -> pd.DataFrame:
    for col in COLUMNS:
        if col not in df:
            df[col] = np.nan
    df["source"] = source
    if label is not None:
        df["label"] = label
    df = df[COLUMNS].sort_values("time").reset_index(drop=True)
    return df


def read_fit(path: str | Path) -> Trace:
    """Read a Garmin .fit activity file."""
    import fitdecode

    rows, sport, sub_sport, meta = [], None, None, {}
    with fitdecode.FitReader(str(path)) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "record":
                def get(*names):
                    for n in names:
                        if frame.has_field(n):
                            v = frame.get_value(n)
                            if v is not None:
                                return v
                    return None
                lat, lon = get("position_lat"), get("position_long")
                rows.append({
                    "time": get("timestamp"),
                    "lat": lat * SEMICIRCLE_TO_DEG if lat is not None else np.nan,
                    "lon": lon * SEMICIRCLE_TO_DEG if lon is not None else np.nan,
                    "alt": get("enhanced_altitude", "altitude"),
                    "speed": get("enhanced_speed", "speed"),
                    "hr": get("heart_rate"),
                    "cadence": get("cadence"),
                })
            elif frame.name in ("sport", "session"):
                for key in ("sport", "sub_sport"):
                    if frame.has_field(key) and frame.get_value(key) is not None:
                        value = str(frame.get_value(key))
                        if key == "sport":
                            sport = sport or value
                        else:
                            sub_sport = sub_sport or value
                if frame.name == "sport" and frame.has_field("name"):
                    meta["activity_name"] = frame.get_value("name")
            elif frame.name == "device_info" and frame.has_field("product_name"):
                meta.setdefault("device", frame.get_value("product_name"))

    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=COLUMNS)
    else:
        df["time"] = pd.to_datetime(df["time"], utc=True)
        for c in ("lat", "lon", "alt", "speed", "hr", "cadence"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    trace = Trace(Path(path), "garmin", df, sport, sub_sport, meta)
    trace.points = _finish(df, "garmin", trace.label)
    return trace


def read_gpslogger_csv(path: str | Path) -> Trace:
    """Read a GPSLogger CSV file. Its annotations become labels.

    An annotation such as "velib-meca" applies to the following points, until
    the next annotation.
    """
    raw = pd.read_csv(path)
    raw.columns = [c.strip().lower() for c in raw.columns]
    df = pd.DataFrame({
        "time": pd.to_datetime(raw["time"], utc=True, errors="coerce"),
        "lat": pd.to_numeric(raw.get("lat"), errors="coerce"),
        "lon": pd.to_numeric(raw.get("lon"), errors="coerce"),
        "alt": pd.to_numeric(raw.get("elevation"), errors="coerce"),
        "speed": pd.to_numeric(raw.get("speed"), errors="coerce"),
        "accuracy": pd.to_numeric(raw.get("accuracy"), errors="coerce"),
    })
    if "annotation" in raw:
        df["label"] = raw["annotation"].where(raw["annotation"].notna()).ffill()
    df = df.dropna(subset=["time"])
    return Trace(Path(path), "gpslogger", _finish(df, "gpslogger"))


def read_trace(path: str | Path) -> Trace:
    suffix = Path(path).suffix.lower()
    if suffix == ".fit":
        return read_fit(path)
    if suffix in (".csv", ".txt"):
        return read_gpslogger_csv(path)
    raise ValueError(f"Format non géré : {path}")
