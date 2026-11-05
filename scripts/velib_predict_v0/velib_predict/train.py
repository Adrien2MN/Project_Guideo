"""Entraîne et évalue le prédicteur Vélib (découpage temporel strict).

Usage : python train.py grid.parquet velib.sqlite out_dir [--final]
  --final : après évaluation, réentraîne sur toutes les données et sauvegarde les modèles.
"""
import json, sys, sqlite3, time
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score
import features as F

grid_path, db_path, out_dir = sys.argv[1:4]
FINAL = "--final" in sys.argv

VAL_START = pd.Timestamp("2026-11-02 13:00", tz=F.TZ).timestamp()
TEST_START = pd.Timestamp("2026-11-03 12:00", tz=F.TZ).timestamp()

t0 = time.time()
grid = pd.read_parquet(grid_path)
info = pd.read_sql("SELECT station_id, lat, lon FROM station_info", sqlite3.connect(db_path))
df = F.build(grid, info)
df = F.add_profiles(df, (df.t < VAL_START).to_numpy())
print(f"features: {df.shape} en {time.time()-t0:.0f}s", flush=True)

split = np.where(df.t < VAL_START, "train", np.where(df.t < TEST_START, "val", "test"))
PARAMS = dict(learning_rate=0.05, num_leaves=63, min_data_in_leaf=200, feature_fraction=0.8,
              bagging_fraction=0.7, bagging_freq=1, lambda_l2=1.0, verbose=-1, num_threads=2)
results, models, best_iters = [], {}, {}
rng = np.random.default_rng(0)


def fit(X, y, Xv, yv, objective):
    p = dict(PARAMS, objective=objective)
    dtr, dv = lgb.Dataset(X, y), lgb.Dataset(Xv, yv)
    return lgb.train(p, dtr, 2000, valid_sets=[dv],
                     callbacks=[lgb.early_stopping(50, verbose=False)])


def hour_bucket(h):
    return pd.cut(h, [-0.1, 6, 10, 16, 20, 24], labels=["0-6h", "6-10h", "10-16h", "16-20h", "20-24h"])


for h in F.HORIZONS:
    cols = F.feature_cols(h)
    valid = (df.ok == 1) & (df[f"y_ok_{h}"] == 1) & df[f"y_mechanical_{h}"].notna()
    for tgt in F.TARGETS:
        y = df[f"y_{tgt}_{h}"]
        tr = valid & (split == "train"); va = valid & (split == "val"); te = valid & (split == "test")
        # --- régression sur la variation (futur - actuel)
        dy = y - df[tgt]
        m = fit(df.loc[tr, cols], dy[tr], df.loc[va, cols], dy[va], "regression_l1")
        best_iters[f"reg_{tgt}_{h}"] = m.best_iteration
        pred = (df.loc[te, tgt] + m.predict(df.loc[te, cols], num_iteration=m.best_iteration)).clip(lower=0)
        yt = y[te]; cur = df.loc[te, tgt]; prof = df.loc[te, f"prof_{tgt}_{h}"]
        hb = hour_bucket(df.loc[te, "hour"])
        low = (cur <= 3)
        for name, sub in [("tout", slice(None)), ("stock ≤3", low.to_numpy())] + \
                         [(f"heure {b}", (hb == b).to_numpy()) for b in hb.cat.categories]:
            idx = np.arange(len(yt))[sub]
            if len(idx) < 500: continue
            results.append(dict(kind="reg", horizon=h, target=tgt, slice=name, n=int(len(idx)),
                                mae_model=float(np.abs(pred.iloc[idx] - yt.iloc[idx]).mean()),
                                mae_persist=float(np.abs(cur.iloc[idx] - yt.iloc[idx]).mean()),
                                mae_profile=float(np.abs(prof.iloc[idx] - yt.iloc[idx]).mean())))
        # --- classification : au moins 1 disponible
        yb = (y >= 1).astype(int)
        c = fit(df.loc[tr, cols], yb[tr], df.loc[va, cols], yb[va], "binary")
        best_iters[f"clf_{tgt}_{h}"] = c.best_iteration
        pp = c.predict(df.loc[te, cols], num_iteration=c.best_iteration)
        ybt = yb[te].to_numpy(); pers = (cur >= 1).astype(float).to_numpy()
        for name, sub in [("tout", np.ones(len(ybt), bool)), ("stock ≤3", low.to_numpy())]:
            bins = np.clip((pp[sub] * 10).astype(int), 0, 9)
            calib = [dict(bin=int(b), pred=float(pp[sub][bins == b].mean()), obs=float(ybt[sub][bins == b].mean()),
                          n=int((bins == b).sum())) for b in range(10) if (bins == b).sum() > 0]
            results.append(dict(kind="clf", horizon=h, target=tgt, slice=name, n=int(sub.sum()),
                                base_rate=float(ybt[sub].mean()),
                                brier_model=float(((pp[sub] - ybt[sub]) ** 2).mean()),
                                brier_persist=float(((pers[sub] - ybt[sub]) ** 2).mean()),
                                auc_model=float(roc_auc_score(ybt[sub], pp[sub])) if 0 < ybt[sub].mean() < 1 else None,
                                calibration=calib))
        imp = dict(zip(cols, m.feature_importance("gain").round(0).tolist()))
        models[f"reg_{tgt}_{h}"] = imp
        print(f"h={h:>2} {tgt:<10} reg it={m.best_iteration:<4} clf it={c.best_iteration:<4} "
              f"({time.time()-t0:.0f}s)", flush=True)

json.dump(dict(results=results, importance=models, best_iters=best_iters,
               counts={s: int((split == s).sum()) for s in ["train", "val", "test"]}),
          open(f"{out_dir}/eval.json", "w"), ensure_ascii=False, indent=1)

if FINAL:
    # Profils et modèles réappris sur toutes les données pour la mise en service
    df = F.add_profiles(df.drop(columns=[c for c in df if c.startswith("prof_")]), np.ones(len(df), bool))
    for h in F.HORIZONS:
        cols = F.feature_cols(h)
        valid = (df.ok == 1) & (df[f"y_ok_{h}"] == 1) & df[f"y_mechanical_{h}"].notna()
        for tgt in F.TARGETS:
            y = df.loc[valid, f"y_{tgt}_{h}"]
            for kind, obj, yy in [("reg", "regression_l1", y - df.loc[valid, tgt]), ("clf", "binary", (y >= 1).astype(int))]:
                n = max(50, int(best_iters[f"{kind}_{tgt}_{h}"] * 1.1))
                b = lgb.train(dict(PARAMS, objective=obj), lgb.Dataset(df.loc[valid, cols], yy), n)
                b.save_model(f"{out_dir}/{kind}_{tgt}_{h}.txt")
    print("modèles finaux sauvegardés", flush=True)
print(f"terminé en {time.time()-t0:.0f}s")
