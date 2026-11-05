"""Transforme le journal de changements Vélib (SQLite) en série régulière par station.

Principe : une valeur à l'instant t n'est valide que si le collecteur tournait à t
(table `polls`). L'état d'une station = dernier événement enregistré <= dernier poll <= t.
"""
import sqlite3
import numpy as np
import pandas as pd

STEP = 300          # pas de la grille (s)
MAX_POLL_AGE = 90   # le dernier poll doit dater de moins de 90 s pour que t soit "observé"


def load(db_path):
    con = sqlite3.connect(db_path)
    polls = pd.read_sql("SELECT observed_at FROM polls ORDER BY observed_at", con)
    ev = pd.read_sql(
        "SELECT station_id, observed_at, last_reported, mechanical, ebike, docks, "
        "is_installed, is_renting, is_returning FROM status_events", con)
    info = pd.read_sql("SELECT station_id, name, lat, lon, capacity FROM station_info", con)
    con.close()
    return polls, ev, info


def build_grid(polls, ev, info, step=STEP):
    p = polls.observed_at.to_numpy()
    t0 = (p[0] // step + 1) * step
    grid = np.arange(t0, p[-1] + 1, step)
    # dernier poll <= t
    idx = np.searchsorted(p, grid, side="right") - 1
    ok = (idx >= 0) & (grid - p[idx] <= MAX_POLL_AGE)
    g = pd.DataFrame({"t": grid[ok], "poll_t": p[idx[ok]]})

    stations = info.station_id.to_numpy()
    full = pd.DataFrame({
        "t": np.repeat(g.t.to_numpy(), len(stations)),
        "poll_t": np.repeat(g.poll_t.to_numpy(), len(stations)),
        "station_id": np.tile(stations, len(g)),
    }).sort_values("poll_t", kind="stable")

    ev = ev.sort_values("observed_at")
    out = pd.merge_asof(full, ev, left_on="poll_t", right_on="observed_at",
                        by="station_id", direction="backward")
    out = out.dropna(subset=["mechanical"])
    for c in ["mechanical", "ebike", "docks", "is_installed", "is_renting", "is_returning"]:
        out[c] = out[c].astype(np.int16)
    out["staleness"] = (out.poll_t - out.last_reported).clip(lower=0).astype(np.float32)
    out = out.drop(columns=["observed_at", "last_reported"])
    out = out.merge(info[["station_id", "lat", "lon", "capacity"]], on="station_id", how="left")
    return out.sort_values(["station_id", "t"]).reset_index(drop=True)


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "velib.sqlite"
    out = sys.argv[2] if len(sys.argv) > 2 else "grid.parquet"
    polls, ev, info = load(db)
    grid = build_grid(polls, ev, info)
    grid.to_parquet(out, index=False)
    print(f"{len(grid):,} lignes, {grid.station_id.nunique()} stations, "
          f"{grid.t.nunique()} instants observés")
