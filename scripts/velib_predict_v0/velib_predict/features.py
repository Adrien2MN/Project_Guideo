"""Variables explicatives et cibles pour la prédiction Vélib à la station."""
import numpy as np
import pandas as pd

STEP = 300
HORIZONS = [15, 30, 60]          # minutes
LAGS = [15, 30, 60]              # minutes
TARGETS = ["mechanical", "ebike", "docks"]
TZ = "Europe/Paris"

# Jours fériés + vacances scolaires zone C (Paris). À compléter au fil de l'année.
HOLIDAYS = {"2026-11-01", "2026-11-11", "2026-12-25", "2027-01-01"}
SCHOOL_HOLIDAYS = [("2026-10-17", "2026-11-01"), ("2026-12-19", "2027-01-03")]
K_NEIGHBORS = 6


def calendar(t_epoch):
    t_epoch = pd.Series(t_epoch)
    u = np.unique(t_epoch.to_numpy())
    ts = pd.DatetimeIndex(pd.to_datetime(u, unit="s", utc=True)).tz_convert(TZ)
    date = np.asarray(ts.strftime("%Y-%m-%d"))
    off = (ts.dayofweek >= 5) | np.isin(date, list(HOLIDAYS))
    school = np.zeros(len(ts), bool)
    for a, b in SCHOOL_HOLIDAYS:
        school |= (date >= a) & (date <= b)
    cal = pd.DataFrame({"date": date, "hour": ts.hour + ts.minute / 60, "dow": ts.dayofweek,
                        "day_off": off.astype(np.int8), "school_hol": school.astype(np.int8)}, index=u)
    return cal.loc[t_epoch.to_numpy()].set_index(t_epoch.index)


def neighbor_matrix(info):
    """Matrice (S×S) de moyenne sur les K plus proches voisins."""
    lat = np.radians(info.lat.to_numpy()); lon = np.radians(info.lon.to_numpy())
    x = np.c_[lon * np.cos(lat.mean()), lat] * 6371.0
    d = np.sqrt(((x[:, None, :] - x[None, :, :]) ** 2).sum(-1))
    np.fill_diagonal(d, np.inf)
    nn = np.argsort(d, axis=1)[:, :K_NEIGHBORS]
    W = np.zeros_like(d)
    W[np.repeat(np.arange(len(x)), K_NEIGHBORS), nn.ravel()] = 1.0 / K_NEIGHBORS
    return W, d[np.arange(len(x))[:, None], nn].mean(1)


def _shift(df, cols, minutes):
    """Valeur de `cols` à t + minutes pour la même station (NaN si non observée)."""
    k = df[["station_id", "t"] + cols].copy()
    k["t"] = k["t"] - minutes * 60
    return df[["station_id", "t"]].merge(k, on=["station_id", "t"], how="left")[cols].to_numpy()


def build(grid, info):
    df = grid.copy()
    df["bikes"] = df.mechanical + df.ebike
    cap = df.capacity.where(df.capacity > 0, df.bikes + df.docks).clip(lower=1)
    df["fill"] = (df.bikes / cap).astype(np.float32)
    df["ok"] = ((df.is_installed == 1) & (df.is_renting == 1) & (df.is_returning == 1)).astype(np.int8)
    df = pd.concat([df, calendar(df.t)], axis=1)
    df["hour_sin"] = np.sin(2 * np.pi * df.hour / 24); df["hour_cos"] = np.cos(2 * np.pi * df.hour / 24)

    base = ["mechanical", "ebike", "docks", "fill"]
    for L in LAGS:
        past = _shift(df, base, -L)
        for j, c in enumerate(base):
            df[f"d{c}_{L}"] = (df[c] - past[:, j]).astype(np.float32)

    # voisinage : remplissage moyen et tendance des K plus proches stations
    info = info.set_index("station_id").loc[sorted(df.station_id.unique())].reset_index()
    W, nd = neighbor_matrix(info)
    sidx = {s: i for i, s in enumerate(info.station_id)}
    for c in ["fill", "dfill_15", "ebike"]:
        piv = df.pivot(index="t", columns="station_id", values=c).reindex(columns=info.station_id)
        m = piv.notna().to_numpy().astype(float)
        v = np.nan_to_num(piv.to_numpy())
        agg = (v @ W.T) / np.clip(m @ W.T, 1e-9, None)
        agg[(m @ W.T) == 0] = np.nan
        long = pd.DataFrame(agg, index=piv.index, columns=info.station_id).stack(future_stack=True)
        long.name = f"nb_{c}"
        df = df.join(long, on=["t", "station_id"])
    df["nb_dist"] = df.station_id.map(dict(zip(info.station_id, nd))).astype(np.float32)

    # cibles
    for h in HORIZONS:
        fut = _shift(df, TARGETS + ["ok"], h)
        for j, c in enumerate(TARGETS):
            df[f"y_{c}_{h}"] = fut[:, j].astype(np.float32)
        df[f"y_ok_{h}"] = fut[:, len(TARGETS)]
        df[f"hour_t{h}"] = (df.hour + h / 60) % 24
    return df


def add_profiles(df, train_mask):
    """Profil historique station × (jour ouvré / off) × heure, appris sur le train.

    Sur les lignes d'entraînement, le profil exclut le jour même (leave-one-day-out)
    pour que le modèle n'apprenne pas à trop s'y fier.
    Lissé vers la moyenne de la station (k jours fictifs).
    """
    K = 2.0
    tr = df.loc[train_mask, ["station_id", "day_off", "hour", "date"] + TARGETS].copy()
    tr["hb"] = tr.hour.astype(int)
    keys = ["station_id", "day_off", "hb"]
    st_mean = tr.groupby("station_id")[TARGETS].mean()
    g_day = tr.groupby(keys + ["date"])[TARGETS].agg(["sum", "count"])
    g_day.columns = [f"{a}__{b}" for a, b in g_day.columns]
    g_all = g_day.groupby(level=keys).sum()

    out = {}
    for h in HORIZONS:
        q = df[["station_id", "day_off", "date"]].copy()
        q["hb"] = ((df.hour + h / 60) % 24).astype(int)
        a = q.join(g_all, on=keys)
        b = q.join(g_day, on=keys + ["date"])
        sm = q[["station_id"]].join(st_mean, on="station_id")
        for c in TARGETS:
            s = a[f"{c}__sum"].fillna(0) - b[f"{c}__sum"].fillna(0) * train_mask
            n = a[f"{c}__count"].fillna(0) - b[f"{c}__count"].fillna(0) * train_mask
            nd = n / 12.0                              # ~ nombre de jours d'observation
            mean = s / n.where(n > 0)
            prof = (mean.fillna(0) * nd + sm[c] * K) / (nd + K)
            out[f"prof_{c}_{h}"] = prof.astype(np.float32)
            out[f"prof_n_{h}"] = nd.astype(np.float32)
    return df.assign(**out)


def feature_cols(h):
    cols = ["mechanical", "ebike", "docks", "fill", "capacity", "staleness",
            "hour_sin", "hour_cos", "dow", "day_off", "school_hol", "lat", "lon",
            "nb_fill", "nb_dfill_15", "nb_ebike", "nb_dist", f"hour_t{h}",
            f"prof_mechanical_{h}", f"prof_ebike_{h}", f"prof_docks_{h}", f"prof_n_{h}"]
    cols += [f"d{c}_{L}" for L in LAGS for c in ["mechanical", "ebike", "docks", "fill"]]
    return cols
