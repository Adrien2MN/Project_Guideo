"""Prédiction pour l'instant le plus récent de la base du collecteur.

Usage : python predict.py velib.sqlite models_dir [station_id ...]
Sortie : pour chaque station et horizon, nb prévu de vélos méca / élec / places
         et probabilité d'en avoir au moins un.
"""
import sys, sqlite3
import numpy as np
import pandas as pd
import lightgbm as lgb
import features as F
from prepare import load, build_grid


def predict(db_path, models_dir, stations=None):
    polls, ev, info = load(db_path)
    grid = build_grid(polls, ev, info)
    df = F.build(grid, info[["station_id", "lat", "lon"]])
    last_t = df.t.max()
    df = F.add_profiles(df, (df.t < last_t).to_numpy())   # profil = tout l'historique
    now = df[df.t == last_t]
    if stations:
        now = now[now.station_id.isin(stations)]
    out = now[["station_id", "mechanical", "ebike", "docks", "ok"]].merge(
        info[["station_id", "name"]], on="station_id")
    rows = []
    for h in F.HORIZONS:
        X = now[F.feature_cols(h)]
        r = out.copy(); r["horizon_min"] = h
        for tgt in F.TARGETS:
            reg = lgb.Booster(model_file=f"{models_dir}/reg_{tgt}_{h}.txt")
            clf = lgb.Booster(model_file=f"{models_dir}/clf_{tgt}_{h}.txt")
            r[f"pred_{tgt}"] = np.clip(now[tgt].to_numpy() + reg.predict(X), 0, None).round(1)
            r[f"p_{tgt}_ge1"] = clf.predict(X).round(3)
        rows.append(r)
    res = pd.concat(rows, ignore_index=True)
    res["as_of"] = pd.to_datetime(last_t, unit="s", utc=True).tz_convert(F.TZ)
    return res


if __name__ == "__main__":
    db, mdir = sys.argv[1:3]
    st = [int(s) for s in sys.argv[3:]] or None
    pd.set_option("display.width", 200)
    print(predict(db, mdir, st).to_string(index=False))
