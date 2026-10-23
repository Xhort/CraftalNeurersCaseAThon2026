"""
================================================================================
 Unintentional Data Poisoning — Last-Mile Dispatch Triage & 4x2 Experiment
 W&M AI Case-A-Thon 2026 (Case sponsor: HQ ACC/ST)
================================================================================

Run:   streamlit run app.py

WHAT THIS APP IS
----------------
A single-file Streamlit app that (1) replays a last-mile dispatch desk with a
human-in-the-loop triage queue and (2) runs the case's required 4 x 2
experiment: {Reference, Stale only, Low-quality only, Combined} x
{countermeasure OFF, ON}. Every number on screen is COMPUTED by the experiment
engine below; nothing in the results is hard-coded.

THE AI COMPONENT (what is being degraded)
-----------------------------------------
Task: predict delivery duration (minutes) at the moment a courier ACCEPTS a
package. Model: weighted ridge regression, re-trained every operating day in a
strict walk-forward loop (train only on records that had ARRIVED in the data
store before that day began; predict that day's packages).

  duration_hat = g(x) + ZoneBaseline(region, day)
  g(x) = b0 + b1*dist + b2*dist^2/10 + hour-bucket effects + AOI-type effects

g is a citywide model. ZoneBaseline is a context feed: the zone's shrunk
7-day mean residual (actual - g), recomputed at the start of each day. If a
zone has no records in the window, the feed keeps its last value (it freezes).
Information therefore enters the model at TWO stages, which is where the two
kinds of degradation act:
  (a) the TRAINING store (labels used at each daily re-train), and
  (b) the CONTEXT feed (ZoneBaseline used at prediction time).

DEGRADATIONS (controlled, offline, clearly labelled as simulated)
-----------------------------------------------------------------
STALE   - "records stop updating": verified records from the two
          highest-volume regions stop reaching the store from day 10
          (verification-sync backlog). Their ZoneBaseline is computed from
          ever-older records, then freezes, and its data age grows. On its own this is harmless; it hurts once operations
          change (day 15 operating shift: +12% citywide, +10 min in one stale
          region). Synthetic data contains this shift; for LaDe it can be
          injected (labelled) from the sidebar.
LOW-Q   - "a fast, low-quality feed out-produces a slow accurate one": a batch
          handheld feed reports the same deliveries 15 min after completion
          (vs. 6 h for the verified feed). 65% of its records carry a batch-sync
          timestamp delay (+Exponential(mean 22 min)), all carry GPS rounding
          noise (sd 0.4 km), and it re-uploads records, so its volume ramps from
          0.1x to 1.5x of the verified feed over the window (volume dominance).
COMBINED - both at once.

Evaluation truth is always the true delivery duration of every package of the
day (verified outcome, revealed offline only). Defect labels are NEVER used as
model inputs; the countermeasure only uses information an operator would have
(record provenance, package IDs, timestamps, arrival times).

COUNTERMEASURE: "Provenance-and-Freshness Guard" (PFG)
------------------------------------------------------
1. Supersede duplicates: when a verified record for the same package_id has
   arrived, the fast-feed copy is dropped (weight 0). Not data loss — the
   better record replaces it.
2. Provenance audit: on matched pairs (fast vs verified, same package) estimate
   the feed's bias  beta = trimmed mean(fast - verified)  and dispersion s^2.
   Unmatched fast records are bias-corrected (y - beta) and down-weighted by
   trust  tau = clip(100 / (100 + s^2), 0.05, 1)  (100 = (10 min)^2 noise scale).
3. Plausibility quarantine: any record with implied speed > 60 km/h or
   duration < 1 min is quarantined. No global outlier filter is used, so valid
   rare long deliveries (outer-industrial zone) are kept.
4. Freshness weighting: training weight *= 0.5 ** (age_days / 5).
5. Stale guard: if a region's newest VERIFIED record is > 48 h old:
     - if >= 15 trust-weighted fresh records exist -> use the audited feed;
     - otherwise -> "drift transfer": hold the zone's last VERIFIED offset
       while the citywide model keeps learning from fresh zones, so citywide
       change still flows through; zone-specific change cannot be recovered.
6. Deferral (human review required) if: drift transfer has lasted beyond
   7 days of verified-data age, OR the unverified feed is >= 50% of the zone's
   recent records AND the guarded and unguarded models disagree by >= 15 min.

Parameters are fixed constants below. Disclosure: DISAGREE_MIN (15 min) was set
from a review-capacity budget after inspecting deferral rates (10 min deferred
~25% of Combined-condition packages). The final week (days 21-27) is reported
separately as a held-out period.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

# =============================================================================
# CONSTANTS (experiment design — change here, everything else follows)
# =============================================================================
DATA_PATH = "delivery_jl.csv"        # LaDe city slice; optional
WINDOW_DAYS = 28                       # 4 operating weeks
WARMUP_DAYS = 3                        # days 0-2: history only, no evaluation
SYNTH_ROWS = 5000
LADE_MAX_ROWS = 15000                  # keep live demo fast
LADE_ASSUMED_YEAR = 2022               # LaDe timestamps omit the year

VERIFIED_LAG_H = 6.0                   # verified feed: slow but accurate
FAST_LAG_H = 0.25                      # fast feed: 15 minutes
FREEZE_DAY = 10                        # stale: verified sync stops (2 regions)
SHIFT_DAY = 15                         # operating shift (synthetic / injected)
SHIFT_CITYWIDE_MULT = 1.12
SHIFT_REGION_ADD_MIN = 10.0
FAST_RATIO_START, FAST_RATIO_END = 0.10, 1.50
FAST_DEFECT_RATE = 0.65
FAST_DEFECT_MEAN_MIN = 22.0
FAST_GPS_NOISE_KM = 0.4

BASELINE_WINDOW_H = 168.0              # 7-day ZoneBaseline
SHRINK_K = 5.0                         # pseudo-count shrink of zone offset toward 0
RIDGE = 1.0
HOUR_EDGES = [9, 12, 15, 18]

# Countermeasure parameters (fixed a priori)
HALF_LIFE_DAYS = 5.0
STALE_AGE_H = 48.0
MIN_FRESH_EVIDENCE = 15.0
LQ_DOMINANCE = 0.50
DISAGREE_MIN = 15.0                    # set from a review-capacity budget
MAX_SPEED_KMH = 60.0
NOISE_SCALE_SQ = 100.0
MIN_MATCHED_PAIRS = 30
MAX_TRANSFER_AGE_H = 168.0             # held offset trusted <= 7 days, then defer

LATE_DAY = SHIFT_DAY                   # "late period" = after operating shift
HOLDOUT_DAY = 21                       # final week = held-out period

CONDITIONS = {
    "Reference / Nominal": (False, False),
    "Stale Data Only": (True, False),
    "Low-Quality Data Only": (False, True),
    "Combined Degradation": (True, True),
}
COND_KEYS = list(CONDITIONS)
COND_SHORT = {
    "Reference / Nominal": "Reference",
    "Stale Data Only": "Stale",
    "Low-Quality Data Only": "Low-quality",
    "Combined Degradation": "Combined",
}

# =============================================================================
# DATA ENGINE
# =============================================================================


def data_signature(path: str = DATA_PATH) -> tuple:
    """Cache key that changes if the CSV appears/changes."""
    if os.path.exists(path):
        s = os.stat(path)
        return (True, s.st_mtime, s.st_size)
    return (False, 0.0, 0)


def _haversine_km(lat1, lng1, lat2, lng2):
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2)
    return 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def generate_synthetic(n: int = SYNTH_ROWS, days: int = WINDOW_DAYS,
                       seed: int = 7) -> pd.DataFrame:
    """Synthetic chronological delivery records (documented, labelled synthetic).

    duration = 10 + 6.5*dist + AOI effect + region effect + rush-hour effect
               + Gamma noise (mean 0, sd ~6 min)
    From SHIFT_DAY: x1.12 citywide (new sort schedule) and +10 min in
    R2-Riverside (road works) — the genuine operating change that makes
    stale information harmful. R6-Outer Industrial is a rare (2%) zone with
    legitimately long deliveries, used to test that the fix keeps valid rare
    events.
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-06-01")  # a Monday
    regions = ["R1-Central", "R2-Riverside", "R3-University",
               "R4-Harbor", "R5-Northgate", "R6-Outer Industrial"]
    r_prob = np.array([0.27, 0.23, 0.19, 0.16, 0.13, 0.02])
    aoi_names = ["residential", "commercial", "office",
                 "school", "industrial", "other"]
    aoi_prob = np.array([
        [.35, .30, .25, .03, .02, .05],
        [.55, .20, .10, .05, .05, .05],
        [.40, .15, .10, .28, .02, .05],
        [.30, .20, .10, .02, .33, .05],
        [.60, .15, .10, .08, .02, .05],
        [.05, .05, .05, .00, .80, .05],
    ])
    aoi_eff = np.array([6, 3, 8, 10, 12, 5], dtype=float)
    region_eff = np.array([0, 3, -2, 5, 1, 22], dtype=float)

    dow_w = np.array([1, 1, 1, 1, 1, 0.8, 0.6])
    day_w = dow_w[np.arange(days) % 7]
    day = rng.choice(days, size=n, p=day_w / day_w.sum())
    hours = np.arange(7, 21)
    h_w = np.array([3, 6, 9, 9, 8, 7, 7, 7, 6, 6, 7, 7, 5, 3], dtype=float)
    hour = rng.choice(hours, size=n, p=h_w / h_w.sum())
    minute = rng.integers(0, 60, size=n)
    region = rng.choice(len(regions), size=n, p=r_prob)
    aoi = np.zeros(n, dtype=int)
    for r in range(len(regions)):
        m = region == r
        aoi[m] = rng.choice(len(aoi_names), size=m.sum(), p=aoi_prob[r])
    dist = rng.lognormal(np.log(2.2), 0.55, size=n)
    rare = region == 5
    dist[rare] = rng.lognormal(np.log(8.5), 0.35, size=rare.sum())
    hour_eff = np.where((hour >= 11) & (hour <= 13), 6.0, 0.0) + \
        np.where((hour >= 17) & (hour <= 19), 9.0, 0.0)
    base = 10 + 6.5 * dist + aoi_eff[aoi] + region_eff[region] + hour_eff
    shifted = day >= SHIFT_DAY
    base = np.where(shifted, base * SHIFT_CITYWIDE_MULT, base)
    base = base + np.where(shifted & (region == 1), SHIFT_REGION_ADD_MIN, 0.0)
    noise = rng.gamma(4.0, 3.0, size=n) - 12.0
    dur = np.clip(base + noise, 3, 400)

    accept = start + pd.to_timedelta(day, unit="D") + \
        pd.to_timedelta(hour, unit="h") + pd.to_timedelta(minute, unit="m")
    courier = np.array([f"C{r + 1}{k:02d}" for r, k in
                        zip(region, rng.integers(0, 10, size=n))])
    df = pd.DataFrame({
        "accept_time": accept,
        "region_id": np.array(regions)[region],
        "aoi_type": np.array(aoi_names)[aoi],
        "courier_id": courier,
        "distance_km": np.round(dist, 3),
        "actual_duration_min": np.round(dur, 2),
        # "as-received" snapshot fields; the experiment recomputes these:
        "predicted_duration_min": np.round(10 + 7.0 * dist, 1),  # legacy ETA
        "data_age_hours": np.round(rng.uniform(0.5, 6.0, size=n), 1),
        "source_type": "verified_scan",
    })
    df = df.sort_values("accept_time").reset_index(drop=True)
    df.insert(0, "package_id", [f"PKG{100000 + i}" for i in range(len(df))])
    return df


_MD_TIME = re.compile(r"^\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$")


def _parse_lade_time(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    sample = s.dropna().head(50)
    if len(sample) and sample.map(lambda v: bool(_MD_TIME.match(v))).mean() > 0.8:
        s = f"{LADE_ASSUMED_YEAR}-" + s
        return pd.to_datetime(s, errors="coerce", format="mixed")
    return pd.to_datetime(s, errors="coerce", format="mixed")


def _load_lade(path: str) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(path, low_memory=False)
    cols = set(raw.columns)
    need = {"accept_time", "delivery_time", "region_id"}
    if not need <= cols:
        raise ValueError(f"missing columns {sorted(need - cols)}")
    log = []
    df = pd.DataFrame()
    df["package_id"] = (raw["order_id"].astype(str) if "order_id" in cols
                        else pd.Series([f"ROW{i}" for i in range(len(raw))]))
    df["courier_id"] = raw["courier_id"].astype(str) if "courier_id" in cols else "unknown"
    df["accept_time"] = _parse_lade_time(raw["accept_time"])
    deliv = _parse_lade_time(raw["delivery_time"])
    df["actual_duration_min"] = (deliv - df["accept_time"]).dt.total_seconds() / 60
    df["region_id"] = raw["region_id"].astype(str)
    df["aoi_type"] = (raw["aoi_type"].astype(str) if "aoi_type" in cols else "unknown")
    if {"accept_gps_lat", "accept_gps_lng", "lat", "lng"} <= cols:
        d = _haversine_km(raw["accept_gps_lat"].astype(float), raw["accept_gps_lng"].astype(float),
                          raw["lat"].astype(float), raw["lng"].astype(float))
        df["distance_km"] = d
        log.append("distance_km = haversine(accept GPS -> package location); "
                   "delivery GPS NOT used (would leak completion info)")
    else:
        df["distance_km"] = np.nan
    n0 = len(df)
    df = df.dropna(subset=["accept_time", "actual_duration_min"])
    df = df[(df["actual_duration_min"] > 1) & (df["actual_duration_min"] <= 720)]
    log.append(f"kept 1 < duration <= 720 min and parseable times: {len(df):,} of {n0:,} rows")
    med = df["distance_km"].median()
    med = 2.0 if not np.isfinite(med) else med
    n_missing = int(df["distance_km"].isna().sum())
    df["distance_km"] = df["distance_km"].fillna(med).clip(upper=50)
    if n_missing:
        log.append(f"{n_missing:,} missing distances filled with median {med:.2f} km")
    t0 = df["accept_time"].min().normalize()
    df = df[df["accept_time"] < t0 + pd.Timedelta(days=WINDOW_DAYS)]
    log.append(f"chronological window: first {WINDOW_DAYS} days from {t0.date()}")
    if len(df) > LADE_MAX_ROWS:
        df = df.sample(LADE_MAX_ROWS, random_state=42)
        log.append(f"uniform random subsample to {LADE_MAX_ROWS:,} rows (seed 42), "
                   "then re-sorted by time (order preserved)")
    df = df.sort_values("accept_time").reset_index(drop=True)
    if len(df) < 1500 or df["accept_time"].dt.normalize().nunique() < 14:
        raise ValueError("too few usable rows/days after filtering")
    # collapse long tails of categories so the model stays small
    top_r = df["region_id"].value_counts().index[:10]
    df["region_id"] = np.where(df["region_id"].isin(top_r),
                               "Region " + df["region_id"], "Other regions")
    top_a = df["aoi_type"].value_counts().index[:7]
    df["aoi_type"] = np.where(df["aoi_type"].isin(top_a),
                              "AOI type " + df["aoi_type"], "AOI other")
    df["predicted_duration_min"] = np.round(10 + 7.0 * df["distance_km"], 1)
    df["data_age_hours"] = np.nan
    df["source_type"] = "verified_scan (LaDe)"
    log.append("regions: top 10 by volume kept, rest pooled as 'Other regions'; "
               "AOI types: top 7 kept")
    return df, {"subset_log": log, "raw_rows": int(len(raw))}


@st.cache_data(show_spinner=False)
def load_dataset(sig: tuple) -> tuple[pd.DataFrame, dict]:
    """Try LaDe CSV; on ANY problem fall back to synthetic so the demo never dies."""
    meta = {"path": DATA_PATH}
    if sig[0]:
        try:
            df, m = _load_lade(DATA_PATH)
            meta.update(m)
            meta.update({
                "source": "LaDe (Cainiao-AI) delivery slice — delivery_jl.csv",
                "is_synthetic": False,
                "source_url": "https://huggingface.co/datasets/Cainiao-AI/LaDe",
            })
            return df, meta
        except Exception as e:  # noqa: BLE001 — never crash the live demo
            meta["fallback_reason"] = f"delivery_jl.csv present but unusable ({e})"
    else:
        meta["fallback_reason"] = "delivery_jl.csv not found in the app folder"
    df = generate_synthetic()
    meta.update({
        "source": "Synthetic chronological last-mile records (generated in-app, seed 7)",
        "is_synthetic": True,
        "source_url": "n/a — generated by generate_synthetic() in app.py",
        "subset_log": [f"{len(df):,} records over {WINDOW_DAYS} days; built-in operating "
                       f"shift on day {SHIFT_DAY} (x{SHIFT_CITYWIDE_MULT} citywide, "
                       f"+{SHIFT_REGION_ADD_MIN:.0f} min in R2-Riverside)"],
        "raw_rows": len(df),
    })
    return df, meta


@st.cache_data(show_spinner=False)
def prepare(sig: tuple, inject_shift: bool) -> dict:
    """Convert to numpy arrays used by the experiment engine."""
    df, meta = load_dataset(sig)
    df = df.copy()
    t0 = df["accept_time"].min().normalize()
    t_acc = (df["accept_time"] - t0).dt.total_seconds().to_numpy() / 3600.0
    day = np.floor(t_acc / 24).astype(int)
    r_codes, r_names = pd.factorize(df["region_id"], sort=True)
    a_counts = df["aoi_type"].value_counts()
    a_names = list(a_counts.index)  # most common AOI = reference category
    a_codes = df["aoi_type"].map({a: i for i, a in enumerate(a_names)}).to_numpy()
    r_names = list(r_names)
    vol = np.bincount(r_codes, minlength=len(r_names))
    named = [i for i in np.argsort(-vol) if r_names[i] != "Other regions"]
    stale_regions = np.array(named[:2])
    rare_region = int(named[-1])
    dur = df["actual_duration_min"].to_numpy(dtype=float).copy()
    mods = []
    if inject_shift and not meta["is_synthetic"]:
        m = day >= SHIFT_DAY
        dur[m] *= SHIFT_CITYWIDE_MULT
        dur[m & (r_codes == stale_regions[1])] += SHIFT_REGION_ADD_MIN
        mods.append({
            "change": "Simulated operating shift injected into outcomes",
            "kind": "introduced (labelled simulation)",
            "fields": "actual_duration_min",
            "timing": f"day >= {SHIFT_DAY}",
            "severity": f"x{SHIFT_CITYWIDE_MULT} all regions; +{SHIFT_REGION_ADD_MIN:.0f} min "
                        f"in {r_names[stale_regions[1]]}",
        })
    hb = np.digitize(df["accept_time"].dt.hour.to_numpy(), HOUR_EDGES)
    n_days = int(min(WINDOW_DAYS, day.max() + 1))
    return {
        "n": len(df), "n_days": n_days, "t0": t0,
        "t_acc": t_acc, "day": day, "dur": dur,
        "dist": df["distance_km"].to_numpy(dtype=float),
        "hb": hb, "aoi": a_codes.astype(int), "region": r_codes.astype(int),
        "region_names": r_names, "aoi_names": a_names,
        "stale_regions": stale_regions, "rare_region": rare_region,
        "day_rows": [np.where(day == d)[0] for d in range(n_days)],
        "package_id": df["package_id"].astype(str).to_numpy(),
        "courier_id": df["courier_id"].astype(str).to_numpy(),
        "accept_time": df["accept_time"].to_numpy(),
        "meta": meta, "mods": mods, "frame": df,
    }

# =============================================================================
# MODEL
# =============================================================================


def design(dist, hb, aoi, n_aoi):
    """Citywide model features g(x). Zone level enters as an additive offset (ZoneBaseline)."""
    d = np.minimum(dist, 30.0)
    cols = [np.ones(len(d)), d, d * d / 10.0]
    cols += [(hb == k).astype(float) for k in range(1, len(HOUR_EDGES) + 1)]
    cols += [(aoi == a).astype(float) for a in range(1, n_aoi)]
    return np.column_stack(cols)


def wls(X, y, w):
    sw = np.sqrt(w)
    Xw = X * sw[:, None]
    A = Xw.T @ Xw
    reg = RIDGE * np.eye(A.shape[0])
    reg[0, 0] = 0.0
    try:
        return np.linalg.solve(A + reg, Xw.T @ (y * sw))
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(Xw, y * sw, rcond=None)[0]

# =============================================================================
# EXPERIMENT ENGINE
# =============================================================================


def run_config(P: dict, cond: str, cm: bool, seed: int, scenario: dict | None = None) -> dict:
    """One cell of the matrix for one seed: walk-forward over operating days.

    The SAME rng seed is used with countermeasure OFF and ON, so both see the
    identical degraded data and are scored on identical evaluation cases.
    """
    freeze, lowq = CONDITIONS[cond]
    scenario = scenario or {}
    rng = np.random.default_rng(1000 + seed)
    n, D, R = P["n"], P["n_days"], len(P["region_names"])
    n_aoi = len(P["aoi_names"])
    day, reg = P["day"], P["region"]

    # ---- ground truth (possibly with a genuine weather surge) ----
    dur = P["dur"].copy()
    if scenario.get("weather"):
        dur[(day >= 22) & (day <= 24)] *= 1.45
    t_comp = P["t_acc"] + dur / 60.0

    # ---- verified feed ----
    v_label = dur.copy()                     # label the store receives
    if scenario.get("anchor_fault"):         # trusted feed itself goes bad
        v_label[day >= 18] += 15.0
    v_arr = t_comp + VERIFIED_LAG_H
    v_keep = np.ones(n, bool)
    if freeze:                               # STALE: sync stops for 2 regions
        v_keep &= ~(np.isin(reg, P["stale_regions"]) & (v_arr >= FREEZE_DAY * 24))
    v_idx = np.where(v_keep)[0]

    # ---- fast, low-quality feed (LOW-Q) ----
    if lowq:
        refs = []
        for d in range(D):
            rows = P["day_rows"][d]
            ratio = FAST_RATIO_START + (FAST_RATIO_END - FAST_RATIO_START) * d / max(D - 1, 1)
            k = int(round(ratio * len(rows)))
            if k and len(rows):
                refs.append(rng.choice(rows, size=k, replace=True))
        f_ref = np.concatenate(refs) if refs else np.zeros(0, int)
        nf = len(f_ref)
        defect = rng.random(nf) < FAST_DEFECT_RATE
        f_label = dur[f_ref] + defect * rng.exponential(FAST_DEFECT_MEAN_MIN, nf)
        f_dist = np.clip(P["dist"][f_ref] + rng.normal(0, FAST_GPS_NOISE_KM, nf), 0.05, None)
        f_arr = t_comp[f_ref] + FAST_LAG_H
    else:
        f_ref = np.zeros(0, int)
        f_label = f_dist = f_arr = np.zeros(0)

    # ---- assemble the data store ----
    S_ref = np.concatenate([v_idx, f_ref]).astype(int)
    S_src = np.concatenate([np.zeros(len(v_idx), int), np.ones(len(f_ref), int)])
    S_label = np.concatenate([v_label[v_idx], f_label])
    S_dist = np.concatenate([P["dist"][v_idx], f_dist])
    S_arr = np.concatenate([v_arr[v_idx], f_arr])
    S_acc = P["t_acc"][S_ref]
    S_day = day[S_ref]
    S_reg = reg[S_ref]
    S_hb = P["hb"][S_ref]
    S_aoi = P["aoi"][S_ref]
    fast = S_src == 1
    is_rare_v = (S_reg == P["rare_region"]) & ~fast
    implaus = (S_label < 1) | (S_dist / (np.maximum(S_label, 1e-6) / 60) > MAX_SPEED_KMH)

    zb_table = np.zeros((D, R))
    vage_t = np.full((D, R), np.nan)
    lq_t = np.zeros((D, R))
    eff_t = np.zeros((D, R))
    guard_t = np.zeros((D, R), int)       # 0 normal, 1 audited-fast, 2 drift-transfer
    last_fresh = np.full(R, -1)
    preds = np.full(n, np.nan)
    coefs, daily_extra = {}, []
    retention = {}

    for d in range(D):
        ds = d * 24.0
        avail = S_arr < ds
        win = avail & (S_acc >= ds - BASELINE_WINDOW_H)

        vm = avail & ~fast
        vmax = np.full(R, -np.inf)
        if vm.any():
            np.maximum.at(vmax, S_reg[vm], S_arr[vm])
        vage = np.where(np.isfinite(vmax), ds - vmax, np.nan)
        cnt_all = np.bincount(S_reg[win], minlength=R)
        cnt_fast = np.bincount(S_reg[win & fast], minlength=R)
        lq = cnt_fast / np.maximum(cnt_all, 1)

        # ---------- weights & labels ----------
        y = S_label.copy()
        if not cm:
            wt = np.ones(len(S_label))
            w_train = avail.astype(float)
        else:
            v_avail_base = np.zeros(n, bool)
            v_avail_base[S_ref[vm]] = True
            matched = fast & v_avail_base[S_ref]
            m = matched & avail
            if m.sum() >= MIN_MATCHED_PAIRS:
                diff = S_label[m] - v_label[S_ref[m]]
                lo, hi = np.quantile(diff, [0.01, 0.99])
                dd = diff[(diff >= lo) & (diff <= hi)]
                beta, s2 = float(dd.mean()), float(dd.var())
                tau = float(np.clip(NOISE_SCALE_SQ / (NOISE_SCALE_SQ + s2), 0.05, 1.0))
            else:
                beta, tau = 0.0, 0.3
            y[fast] -= beta
            wt = np.ones(len(S_label))
            wt[fast] = tau
            wt[matched] = 0.0
            wt[implaus] = 0.0
            rec = 0.5 ** ((ds - S_acc) / (HALF_LIFE_DAYS * 24.0))
            w_train = wt * rec * avail

        # ---------- daily re-train of the citywide model g(x) ----------
        guard = np.zeros(R, int)
        eff = np.bincount(S_reg, weights=(wt * (win & fast)), minlength=R)
        zb = zb_table[d - 1].copy() if d > 0 else np.zeros(R)
        tr = w_train > 0
        if d >= WARMUP_DAYS and tr.sum() >= 50:
            coef = wls(design(S_dist[tr], S_hb[tr], S_aoi[tr], n_aoi), y[tr], w_train[tr])
            coefs[d] = coef
            # ---------- ZoneBaseline context feed: shrunk 7-day mean residual per zone ----------
            wi = np.where(win)[0]
            res = y[wi] - design(S_dist[wi], S_hb[wi], S_aoi[wi], n_aoi) @ coef
            sw = np.bincount(S_reg[wi], weights=wt[wi], minlength=R)
            sr = np.bincount(S_reg[wi], weights=wt[wi] * res, minlength=R)
            has = sw > 0
            zb[has] = sr[has] / (sw[has] + SHRINK_K)   # no records -> feed keeps last value (frozen)
            if cm:
                stale = (vage > STALE_AGE_H) | np.isnan(vage)
                for r in np.where(stale)[0]:
                    if eff[r] >= MIN_FRESH_EVIDENCE:
                        guard[r] = 1                     # audited fast feed carries the zone
                    else:
                        guard[r] = 2                     # hold last verified offset ("drift transfer")
                        if last_fresh[r] >= 0:
                            zb[r] = zb_table[last_fresh[r], r]
                last_fresh[~stale] = d
            ev = P["day_rows"][d]
            if len(ev):
                preds[ev] = design(P["dist"][ev], P["hb"][ev], P["aoi"][ev], n_aoi) @ coef + zb[reg[ev]]
        zb_table[d] = zb
        vage_t[d], lq_t[d], eff_t[d], guard_t[d] = vage, lq, eff, guard

        arr24 = (S_arr >= ds - 24) & (S_arr < ds)
        daily_extra.append({
            "day": d,
            "arr_verified": int((arr24 & ~fast).sum()),
            "arr_fast": int((arr24 & fast).sum()),
            "age_stale_regions_h": float(np.nanmean(vage[P["stale_regions"]]))
            if np.isfinite(vage[P["stale_regions"]]).any() else np.nan,
            "age_other_regions_h": float(np.nanmean(np.delete(vage, P["stale_regions"])))
            if np.isfinite(np.delete(vage, P["stale_regions"])).any() else np.nan,
        })

        if d == D - 1:  # coverage / non-evasion accounting at end of window
            used = (wt > 0) & avail
            rec_v = avail & ~fast
            rec_f = avail & fast
            z = (S_label - S_label[avail].mean()) / (S_label[avail].std() + 1e-9)
            naive_keep = np.abs(z) <= 3
            retention = {
                "verified_received": int(rec_v.sum()),
                "verified_retained_pct": 100 * used[rec_v].mean() if rec_v.any() else np.nan,
                "rare_verified_retained_pct": 100 * used[rec_v & is_rare_v].mean()
                if (rec_v & is_rare_v).any() else np.nan,
                "naive3sd_rare_retained_pct": 100 * naive_keep[rec_v & is_rare_v].mean()
                if (rec_v & is_rare_v).any() else np.nan,
                "naive3sd_all_retained_pct": 100 * naive_keep[avail].mean(),
                "fast_received": int(rec_f.sum()),
                "fast_used_pct": 100 * ((wt > 0) & rec_f).mean() / max(rec_f.mean(), 1e-12)
                if rec_f.any() else np.nan,
            }
            if rec_f.any() and cm:
                retention["fast_superseded_pct"] = 100 * (matched & rec_f).sum() / rec_f.sum()
                retention["fast_quarantined_pct"] = 100 * (implaus & rec_f & ~matched).sum() / rec_f.sum()
                retention["fast_bias_est_min"] = beta
                retention["fast_trust"] = tau

    ev_idx = np.where((day >= WARMUP_DAYS) & np.isfinite(preds))[0]
    ed, er = day[ev_idx], reg[ev_idx]
    rows = pd.DataFrame({
        "idx": ev_idx, "day": ed, "region": er,
        "pred": preds[ev_idx], "actual": dur[ev_idx],
        "vage": vage_t[ed, er], "lq": lq_t[ed, er],
        "eff_fast": eff_t[ed, er], "guard": guard_t[ed, er],
        "baseline": zb_table[ed, er],
    })
    rows["err"] = rows["pred"] - rows["actual"]
    return {"rows": rows, "extra": pd.DataFrame(daily_extra),
            "coefs": coefs, "retention": retention}


def _attach_deferral(cm_rows: pd.DataFrame, un_rows: pd.DataFrame) -> pd.DataFrame:
    out = cm_rows.merge(un_rows[["idx", "pred"]].rename(columns={"pred": "pred_unmit"}),
                        on="idx", how="left")
    out["gap"] = (out["pred"] - out["pred_unmit"]).abs()
    out["defer"] = (((out["guard"] == 2) & (out["vage"].fillna(np.inf) > MAX_TRANSFER_AGE_H))
                    | ((out["lq"] >= LQ_DOMINANCE) & (out["gap"] >= DISAGREE_MIN)))
    return out


def _daily(rows: pd.DataFrame, P: dict) -> pd.DataFrame:
    g = rows.assign(ae=rows["err"].abs(),
                    stale_r=rows["region"].isin(P["stale_regions"]),
                    rare=rows["region"] == P["rare_region"])
    agg = g.groupby("day").agg(
        mae=("ae", "mean"), bias=("err", "mean"),
        p90=("ae", lambda s: float(np.quantile(s, 0.9))),
        n=("ae", "size"),
        defer_rate=("defer", "mean") if "defer" in g else ("ae", lambda s: 0.0),
    ).reset_index()
    return agg


def _summary_one(rows: pd.DataFrame, P: dict) -> dict:
    ae = rows["err"].abs()
    late = rows["day"] >= LATE_DAY
    hold = rows["day"] >= HOLDOUT_DAY
    rare = rows["region"] == P["rare_region"]
    stale_r = rows["region"].isin(P["stale_regions"])
    return {
        "MAE_all": ae.mean(),
        "MAE_late": ae[late].mean(),
        "MAE_holdout": ae[hold].mean(),
        "Bias_late": rows.loc[late, "err"].mean(),
        "P90_all": float(np.quantile(ae, 0.9)),
        "MAE_rare": ae[rare].mean() if rare.any() else np.nan,
        "MAE_stale_regions_late": ae[late & stale_r].mean() if (late & stale_r).any() else np.nan,
        "defer_pct": 100 * rows["defer"].mean() if "defer" in rows else 0.0,
        "n_eval": int(len(rows)),
    }


@st.cache_data(show_spinner=False)
def run_matrix(sig: tuple, inject_shift: bool, n_seeds: int, scenario_key: str = "") -> dict:
    """Full 4 x 2 x seeds experiment (or a stress scenario on Combined)."""
    P = prepare(sig, inject_shift)
    scenario = {scenario_key: True} if scenario_key else {}
    conds = COND_KEYS if not scenario_key else ["Reference / Nominal", "Combined Degradation"]
    runs, summ, daily = {}, [], []
    for cond in conds:
        for seed in range(n_seeds):
            un = run_config(P, cond, False, seed, scenario)
            cmr = run_config(P, cond, True, seed, scenario)
            un_rows = un["rows"].assign(defer=False, gap=0.0, pred_unmit=un["rows"]["pred"])
            cm_rows = _attach_deferral(cmr["rows"], un["rows"])
            for flag, res, rows in ((False, un, un_rows), (True, cmr, cm_rows)):
                s = _summary_one(rows, P)
                s.update({"condition": cond, "countermeasure": "ON" if flag else "OFF", "seed": seed})
                s.update({k: v for k, v in res["retention"].items() if not isinstance(v, str)})
                summ.append(s)
                dly = _daily(rows, P).merge(res["extra"], on="day", how="right")
                dly["condition"], dly["countermeasure"], dly["seed"] = cond, ("ON" if flag else "OFF"), seed
                daily.append(dly)
                if seed == 0:
                    runs[(cond, flag)] = {"rows": rows, "coefs": res["coefs"],
                                          "retention": res["retention"]}
    return {"runs": runs, "summary": pd.DataFrame(summ),
            "daily": pd.concat(daily, ignore_index=True)}


def summarize(summary: pd.DataFrame) -> pd.DataFrame:
    num = summary.drop(columns=["seed"]).select_dtypes("number").columns
    g = summary.groupby(["condition", "countermeasure"])[list(num)]
    out = g.mean().join(g.std(ddof=0), rsuffix="_sd").reset_index()
    out["condition"] = pd.Categorical(out["condition"], COND_KEYS, ordered=True)
    return out.sort_values(["condition", "countermeasure"]).reset_index(drop=True)

# =============================================================================
# UI HELPERS
# =============================================================================


def _st_ver() -> tuple:
    try:
        return tuple(int(x) for x in st.__version__.split(".")[:2])
    except Exception:  # noqa: BLE001
        return (1, 0)


FULL = {"width": "stretch"} if _st_ver() >= (1, 49) else {"use_container_width": True}

CSS = """
<style>
.card {border:1px solid rgba(128,128,128,.28); border-radius:12px; padding:14px 16px;
       background:rgba(128,128,128,.06); margin-bottom:10px;}
.card h4 {margin:0 0 6px 0; font-size:0.95rem;}
.card .big {font-size:1.6rem; font-weight:700; line-height:1.2;}
.card .sub {font-size:0.82rem; opacity:.75;}
.pill {display:inline-block; padding:2px 9px; border-radius:999px; font-size:.78rem;
       border:1px solid rgba(128,128,128,.35); margin-right:4px;}
.warn {border-left:4px solid #d9822b;}
.ok   {border-left:4px solid #2e9e5b;}
.bad  {border-left:4px solid #d64545;}
.info {border-left:4px solid #3b7dd8;}
.kv td {padding:2px 10px 2px 0; vertical-align:top; font-size:.88rem;}
</style>
"""


def card(title: str, big: str, sub: str = "", tone: str = "info") -> None:
    st.markdown(f'<div class="card {tone}"><h4>{title}</h4><div class="big">{big}</div>'
                f'<div class="sub">{sub}</div></div>', unsafe_allow_html=True)


def day_label(P: dict, d: int) -> str:
    ts = P["t0"] + pd.Timedelta(days=int(d))
    return f"Day {d} · {ts:%a %b %d}"


def badge_for(r) -> str:
    if r["defer"]:
        if r["guard"] == 2 and (r["vage"] > MAX_TRANSFER_AGE_H if pd.notna(r["vage"]) else True):
            return "🔴 Stale > 7 d – No Fresh Evidence"
        return "🔴 Poisoning Alert / Volume Dominance"
    if (r["vage"] > STALE_AGE_H) if pd.notna(r["vage"]) else False:
        return "🟡 Stale Zone Baseline"
    if r["lq"] >= LQ_DOMINANCE:
        return "🟡 Unverified-Feed Majority"
    return "🟢 Fresh & Verified"


def explain_row(r, P: dict) -> str:
    reg = P["region_names"][int(r["region"])]
    parts = []
    if r["guard"] == 2:
        parts.append(f"No verified record from **{reg}** has arrived for "
                     f"**{r['vage']:.0f} h** and there is not enough audited fresh data "
                     f"(effective n = {r['eff_fast']:.1f} < {MIN_FRESH_EVIDENCE:.0f}). "
                     "The guarded ETA holds the zone's last verified offset on top of the citywide "
                     "model (drift transfer) — citywide change flows through, zone-specific change "
                     "does not. Beyond 7 days this is no longer trusted.")
    elif r["guard"] == 1:
        parts.append(f"Verified data for **{reg}** is **{r['vage']:.0f} h** old; the guarded ETA "
                     "uses bias-corrected, down-weighted fast-feed records instead.")
    if r["lq"] >= LQ_DOMINANCE:
        parts.append(f"**{100 * r['lq']:.0f}%** of this zone's last-7-day records come from the "
                     "unverified fast feed (volume dominance).")
    if r["gap"] >= DISAGREE_MIN:
        parts.append(f"Guarded and unguarded models disagree by **{r['gap']:.1f} min** — "
                     "the unguarded model is being pulled by the degraded data.")
    return " ".join(parts) or "No data-health concerns detected."

# =============================================================================
# APP
# =============================================================================


def main() -> None:
    st.set_page_config(page_title="Data Poisoning Triage · W&M Case-A-Thon",
                       page_icon="🚚", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    ss = st.session_state
    ss.setdefault("review_log", [])
    ss.setdefault("review_start", {})
    ss.setdefault("clock_min", 10 * 60)
    ss.setdefault("clock_key", None)

    sig = data_signature()
    _, meta0 = load_dataset(sig)

    # ---------------- sidebar ----------------
    with st.sidebar:
        st.header("⚙️ Experiment setup")
        if meta0["is_synthetic"]:
            st.warning(f"**Fallback data in use.** {meta0.get('fallback_reason', '')}. "
                       "Synthetic records are clearly labelled as such everywhere.")
        else:
            st.success("**LaDe slice loaded** from `delivery_jl.csv`.")
        inject = st.checkbox("Inject labelled operating shift (LaDe only)",
                             value=not meta0["is_synthetic"],
                             disabled=meta0["is_synthetic"],
                             help="Synthetic data already contains the day-15 shift. "
                                  "For LaDe this adds the same shift to outcomes and logs it.")
        n_seeds = st.slider("Repeat runs (seeds)", 1, 10, 3,
                            help="Low-quality defects are random; repeats show stability.")
        st.caption(f"Walk-forward: daily re-train, {WARMUP_DAYS}-day warm-up, "
                   f"evaluation days {WARMUP_DAYS}–{WINDOW_DAYS - 1}. "
                   f"Held-out final week: days {HOLDOUT_DAY}+.")
        st.divider()
        st.caption("All experiments run offline on public/synthetic data. "
                   "No live vehicles or services.")

    P = prepare(sig, inject)
    with st.spinner("Running the 4 × 2 experiment (walk-forward, all seeds)…"):
        t_start = time.perf_counter()
        R = run_matrix(sig, inject, n_seeds)
        run_secs = time.perf_counter() - t_start
    summ = summarize(R["summary"])

    st.title("🚚 Unintentional Data Poisoning — Dispatch Triage & Experiment")
    st.caption(f"Delivery-duration prediction at acceptance · {P['meta']['source']} · "
               f"{P['n']:,} packages · {P['n_days']} days · "
               f"seeds = {n_seeds}")

    tab1, tab2, tab3 = st.tabs(["🛰️ Live Dispatch Triage",
                                "🧪 Drift & Experimental Matrix",
                                "🏛️ Governance, Manifest & Pilot"])

    # =========================================================================
    # TAB 1 — LIVE DISPATCH TRIAGE
    # =========================================================================
    with tab1:
        st.subheader("Live dispatch queue (offline replay of an operating day)")
        c1, c2, c3 = st.columns([2, 2, 3])
        cond1 = c1.selectbox("Operating scenario", COND_KEYS, index=3, key="t1_cond")
        eval_days = list(range(WARMUP_DAYS, P["n_days"]))
        d1 = c2.selectbox("Operating day", eval_days,
                          index=eval_days.index(min(24, eval_days[-1])),
                          format_func=lambda d: day_label(P, d), key="t1_day")
        key = (cond1, d1)
        if ss.clock_key != key:
            ss.clock_key, ss.clock_min = key, 10 * 60
        with c3:
            st.write("")
            b1, b2, b3 = st.columns(3)
            if b1.button("⏩ +30 min"):
                ss.clock_min = min(ss.clock_min + 30, 24 * 60)
            if b2.button("⏭ End of day"):
                ss.clock_min = 24 * 60
            if b3.button("↺ 10:00"):
                ss.clock_min = 10 * 60
        cmr = R["runs"][(cond1, True)]["rows"]
        day_rows = cmr[cmr["day"] == d1].copy()
        acc = pd.to_datetime(P["accept_time"][day_rows["idx"].to_numpy()])
        day_rows["accepted"] = acc
        mins = acc.hour * 60 + acc.minute
        q = day_rows[np.asarray(mins) <= ss.clock_min].copy()
        q["badge"] = q.apply(badge_for, axis=1)
        q["package"] = P["package_id"][q["idx"].to_numpy()]
        q["zone"] = [P["region_names"][r] for r in q["region"]]
        q["aoi_t"] = [P["aoi_names"][a] for a in P["aoi"][q["idx"].to_numpy()]]
        q["dist"] = P["dist"][q["idx"].to_numpy()]
        q["status"] = np.where(q["defer"], "⛔ Review required", "✅ Auto-dispatch")
        reviewed = {e["package"] for e in ss.review_log
                    if e["condition"] == cond1 and e["day"] == d1}
        q.loc[q["package"].isin(reviewed), "status"] = "👤 Reviewed"

        clock_txt = f"{ss.clock_min // 60:02d}:{ss.clock_min % 60:02d}"
        n_def_open = int((q["defer"] & ~q["package"].isin(reviewed)).sum())
        if n_def_open:
            st.error(f"⚠️ **System Deferral: Review Required** — {n_def_open} package(s) in the "
                     f"queue are high-risk and are held for a dispatcher decision (clock {clock_txt}).")
        else:
            st.success(f"No open deferrals at {clock_txt}. "
                       "All released packages auto-dispatched on guarded ETAs.")

        # ---- dual clock ----
        coef = R["runs"][(cond1, True)]["coefs"].get(d1)
        infer_ms = np.nan
        if coef is not None and len(q):
            ii = q["idx"].to_numpy()
            reps = 200
            t0 = time.perf_counter()
            for _ in range(reps):
                X = design(P["dist"][ii], P["hb"][ii], P["aoi"][ii], len(P["aoi_names"]))
                _ = X @ coef + q["baseline"].to_numpy()
            infer_ms = (time.perf_counter() - t0) / reps * 1000 / len(ii)
        log_df = pd.DataFrame(ss.review_log)
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            card("⚡ Model inference (measured)",
                 f"{infer_ms:.4f} ms/pkg" if np.isfinite(infer_ms) else "—",
                 "feature build + ridge predict on this machine; excludes network/serving",
                 "info")
        with m2:
            if len(log_df):
                card("👤 Human review (measured)", f"{log_df['review_sec'].mean():.1f} s/pkg",
                     f"{len(log_df)} review(s) logged this session · median "
                     f"{log_df['review_sec'].median():.1f} s", "warn")
            else:
                card("👤 Human review (measured)", "awaiting 1st review",
                     "timed from opening a case to the decision click", "warn")
        all_day = cmr[cmr["day"] == d1]
        n_def_day = int(all_day["defer"].sum())
        with m3:
            card("📦 Deferred today (full day)", f"{n_def_day} / {len(all_day)}",
                 f"{100 * n_def_day / max(len(all_day), 1):.1f}% of packages need a person", "bad"
                 if n_def_day else "ok")
        with m4:
            if len(log_df):
                load = n_def_day * log_df["review_sec"].mean() / 60
                card("⏱️ Projected review load", f"{load:.1f} min/day",
                     "deferred packages × measured review time", "warn")
            else:
                card("⏱️ Projected review load", "—", "needs ≥1 measured review", "warn")

        show = q.sort_values("accepted", ascending=False)[
            ["badge", "status", "package", "accepted", "zone", "aoi_t", "dist",
             "pred", "pred_unmit", "vage", "lq"]].rename(columns={
                 "badge": "Data health", "status": "Status", "package": "Package",
                 "accepted": "Accepted", "zone": "Zone", "aoi_t": "AOI",
                 "dist": "Dist (km)", "pred": "Guarded ETA (min)",
                 "pred_unmit": "Unguarded ETA (min)", "vage": "Verified data age (h)",
                 "lq": "Unverified share"})
        show["Unverified share"] = 100 * show["Unverified share"]
        st.dataframe(show, height=380, hide_index=True, **FULL, column_config={
            "Accepted": st.column_config.DatetimeColumn(format="HH:mm"),
            "Dist (km)": st.column_config.NumberColumn(format="%.1f"),
            "Guarded ETA (min)": st.column_config.NumberColumn(format="%.1f"),
            "Unguarded ETA (min)": st.column_config.NumberColumn(format="%.1f"),
            "Verified data age (h)": st.column_config.NumberColumn(format="%.0f"),
            "Unverified share": st.column_config.ProgressColumn(
                format="%.0f%%", min_value=0, max_value=100),
        })
        st.caption(f"{len(q)} packages released up to {clock_txt}. Badges: 🟢 fresh verified data · "
                   f"🟡 verified data > {STALE_AGE_H:.0f} h old or unverified feed ≥ "
                   f"{LQ_DOMINANCE:.0%} · 🔴 deferred (see rules in the oversight panel).")

        # ---- review / correction panel ----
        st.markdown("#### 🧑‍✈️ Dispatcher review & correction")
        open_cases = q[q["defer"] & ~q["package"].isin(reviewed)].sort_values("accepted")
        if open_cases.empty:
            st.info("No open cases. Use **⏩ +30 min** to release more packages, or pick the "
                    "Combined / Stale scenario to see deferrals.")
        else:
            pick = st.selectbox("Open case", ["— select a deferred package —"] +
                                open_cases["package"].tolist(), key="t1_pick")
            if pick.startswith("—"):
                st.caption("Opening a case starts the human-review timer.")
            else:
                r = open_cases[open_cases["package"] == pick].iloc[0]
                ss.review_start.setdefault((cond1, d1, pick), time.time())
                e1, e2, e3, e4 = st.columns(4)
                e1.metric("Guarded ETA", f"{r['pred']:.1f} min")
                e2.metric("Unguarded ETA", f"{r['pred_unmit']:.1f} min",
                          f"{r['pred_unmit'] - r['pred']:+.1f} vs guarded", delta_color="off")
                e3.metric("Verified data age", f"{r['vage']:.0f} h" if pd.notna(r["vage"]) else "none")
                e4.metric("Unverified share (7 d)", f"{100 * r['lq']:.0f}%")
                st.markdown(f"**{r['badge']}** · {r['zone']} · {r['aoi_t']} · "
                            f"{r['dist']:.1f} km · accepted {r['accepted']:%H:%M}")
                st.markdown("**Why the system deferred:** " + explain_row(r, P))
                a1, a2, a3 = st.columns([1.2, 1.6, 1.2])
                action, final = None, None
                if a1.button("✅ Accept guarded ETA", key=f"acc_{pick}"):
                    action, final = "accept_guarded", float(r["pred"])
                with a2:
                    manual = st.number_input("Manual ETA (min)", 1.0, 600.0,
                                             float(round(r["pred"])), 1.0, key=f"man_{pick}")
                    if st.button("✏️ Apply override", key=f"ovr_{pick}"):
                        action, final = "override", float(manual)
                if a3.button("📣 Escalate to data-quality owner", key=f"esc_{pick}"):
                    action, final = "escalate", float(r["pred"])
                if action:
                    secs = time.time() - ss.review_start.pop((cond1, d1, pick), time.time())
                    ss.review_log.append({
                        "logged_at": datetime.now().strftime("%H:%M:%S"),
                        "condition": cond1, "day": d1, "package": pick,
                        "zone": r["zone"], "badge": r["badge"], "action": action,
                        "guarded_eta": round(float(r["pred"]), 1),
                        "unguarded_eta": round(float(r["pred_unmit"]), 1),
                        "final_eta": round(final, 1), "review_sec": round(secs, 1),
                        "actual_offline": round(float(r["actual"]), 1),
                        "final_abs_err": round(abs(final - float(r["actual"])), 1),
                        "guarded_abs_err": round(abs(float(r["pred"]) - float(r["actual"])), 1),
                        "unguarded_abs_err": round(abs(float(r["pred_unmit"]) - float(r["actual"])), 1),
                    })
                    st.rerun()
        if ss.review_log:
            st.markdown("##### Review & correction log")
            st.dataframe(pd.DataFrame(ss.review_log), hide_index=True, **FULL)
            st.caption("`actual_offline` is revealed only because this is an offline replay — "
                       "it lets us score human decisions. Overrides are logged for the "
                       "data-quality owner and are **not** fed back into training automatically "
                       "(that would open a new poisoning path).")
            st.download_button("⬇️ Download review log (CSV)",
                               pd.DataFrame(ss.review_log).to_csv(index=False),
                               "review_log.csv", "text/csv")

        with st.expander("👥 Human oversight: who does what", expanded=False):
            st.markdown(f"""
| Role | Responsibility |
|---|---|
| **Dispatcher (user)** | Sees guarded ETA + data-health badge for every accepted package; auto-dispatches 🟢/🟡. |
| **Shift lead (reviewer)** | Resolves ⛔ deferred cases: accept, override with a manual ETA, or escalate. |
| **Data-quality owner** | Receives escalations; owns feed health (sync backlog, fast-feed audit); approves any use of overrides as training labels. |
| **Model owner** | Owns thresholds, weekly matrix re-run, stop conditions. |

**System must defer when:** (1) a zone has had no verified record for > {MAX_TRANSFER_AGE_H/24:.0f} days
**and** fewer than {MIN_FRESH_EVIDENCE:.0f} trust-weighted fresh records, or (2) the unverified feed is
≥ {LQ_DOMINANCE:.0%} of the zone's last-7-day records **and** guarded vs. unguarded ETAs
differ by ≥ {DISAGREE_MIN:.0f} min.

**Correction path:** accept / override / escalate → logged with reviewer time → reviewed weekly.
Human review time is reported separately from model compute time.
""")

    # =========================================================================
    # TAB 2 — DRIFT & EXPERIMENTAL MATRIX
    # =========================================================================
    with tab2:
        st.subheader("4 × 2 experimental matrix")
        cc1, cc2, cc3, cc4 = st.columns([3.2, 1.2, 1.6, 1.2])
        cond2 = cc1.radio("Data condition", COND_KEYS, index=3, horizontal=True, key="t2_cond")
        cm_on = cc2.toggle("Countermeasure ON", value=True, key="t2_cm")
        metric = cc3.radio("Metric", ["MAE", "Signed bias", "P90 abs error"],
                           horizontal=True, key="t2_metric")
        gran = cc4.radio("Resolution", ["Day", "Week"], horizontal=True, key="t2_gran")
        mcol = {"MAE": "mae", "Signed bias": "bias", "P90 abs error": "p90"}[metric]

        D_ = R["daily"].copy()
        D_["week"] = D_["day"] // 7 + 1

        def series(cond, cmflag):
            s = D_[(D_["condition"] == cond) & (D_["countermeasure"] == cmflag) &
                   (D_["day"] >= WARMUP_DAYS)]
            if gran == "Week":
                # package-weighted within week, per seed, then mean/sd over seeds
                s = s.assign(w=s[mcol] * s["n"]).groupby(["seed", "week"]).agg(
                    w=("w", "sum"), n=("n", "sum"),
                    age=("age_stale_regions_h", "mean"),
                    av=("arr_verified", "sum"), af=("arr_fast", "sum"),
                    dfr=("defer_rate", "mean")).reset_index()
                s["v"] = s["w"] / s["n"]
                g = s.groupby("week")
                x = [f"Week {int(w)}" for w in g.size().index]
            else:
                s = s.rename(columns={mcol: "v", "age_stale_regions_h": "age",
                                      "arr_verified": "av", "arr_fast": "af",
                                      "defer_rate": "dfr"})
                g = s.groupby("day")
                x = [P["t0"] + pd.Timedelta(days=int(d)) for d in g.size().index]
            return x, g["v"].mean().to_numpy(), g["v"].std(ddof=0).fillna(0).to_numpy(), \
                g["age"].mean().to_numpy(), g["av"].mean().to_numpy(), g["af"].mean().to_numpy(), \
                g["dfr"].mean().to_numpy()

        cmflag = "ON" if cm_on else "OFF"
        x, v, sd, age, av, af, dfr = series(cond2, cmflag)
        _, v_alt, _, _, _, _, _ = series(cond2, "OFF" if cm_on else "ON")
        _, v_ref, _, _, _, _, _ = series(COND_KEYS[0], "OFF")

        fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                            row_heights=[0.52, 0.2, 0.28],
                            subplot_titles=(f"{metric} (min) — evaluated on verified outcomes",
                                            "Verified data age, stale-candidate zones (h)",
                                            "Records arriving in the store (per day / week)"))
        col_main = "#2e9e5b" if cm_on else "#d64545"
        col_alt = "#d64545" if cm_on else "#2e9e5b"
        xs = list(x)
        fig.add_trace(go.Scatter(x=xs + xs[::-1], y=list(v + sd) + list((v - sd)[::-1]),
                                 fill="toself", fillcolor="rgba(128,128,128,0.18)",
                                 line=dict(width=0), hoverinfo="skip", showlegend=False), 1, 1)
        fig.add_trace(go.Scatter(x=xs, y=v, name=f"{COND_SHORT[cond2]} · CM {cmflag}",
                                 line=dict(color=col_main, width=3), mode="lines+markers"), 1, 1)
        fig.add_trace(go.Scatter(x=xs, y=v_alt, name=f"{COND_SHORT[cond2]} · CM {'OFF' if cm_on else 'ON'}",
                                 line=dict(color=col_alt, width=1.6, dash="dot")), 1, 1)
        fig.add_trace(go.Scatter(x=xs, y=v_ref, name="Reference · CM OFF",
                                 line=dict(color="#888", width=1.6, dash="dash")), 1, 1)
        fig.add_trace(go.Scatter(x=xs, y=age, name="Data age (h)", showlegend=False,
                                 line=dict(color="#d9822b", width=2), fill="tozeroy",
                                 fillcolor="rgba(217,130,43,0.15)"), 2, 1)
        fig.add_hline(y=STALE_AGE_H, line_dash="dot", line_color="#d9822b", row=2, col=1,
                      annotation_text=f"stale guard {STALE_AGE_H:.0f} h",
                      annotation_position="top left")
        fig.add_trace(go.Bar(x=xs, y=av, name="Verified feed", marker_color="#3b7dd8"), 3, 1)
        fig.add_trace(go.Bar(x=xs, y=af, name="Fast low-quality feed", marker_color="#d9822b"), 3, 1)
        fig.update_layout(barmode="stack", height=640, margin=dict(l=10, r=10, t=40, b=10),
                          legend=dict(orientation="h", y=1.08, x=0),
                          hovermode="x unified")
        if gran == "Day":
            for dd, lab, show_it in ((FREEZE_DAY, "sync freeze (stale)", CONDITIONS[cond2][0]),
                                     (SHIFT_DAY, "operating shift (simulated)", True),
                                     (HOLDOUT_DAY, "held-out week", True)):
                if show_it:
                    xv = P["t0"] + pd.Timedelta(days=dd)
                    fig.add_vline(x=xv, line_dash="dot", line_color="#999")
                    fig.add_annotation(x=xv, y=1.0, yref="y domain", text=lab, showarrow=False,
                                       xanchor="left", font=dict(size=10), bgcolor="rgba(128,128,128,.12)")
        st.plotly_chart(fig, **FULL)

        # ---- stat cards ----
        row = summ[(summ["condition"] == cond2) & (summ["countermeasure"] == cmflag)].iloc[0]
        off = summ[(summ["condition"] == cond2) & (summ["countermeasure"] == "OFF")].iloc[0]
        ref = summ[(summ["condition"] == COND_KEYS[0]) & (summ["countermeasure"] == "OFF")].iloc[0]
        ret = R["runs"][(cond2, cm_on)]["retention"]
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            card("MAE, late period (day ≥ 15)", f"{row['MAE_late']:.2f} min",
                 f"± {row['MAE_late_sd']:.2f} over seeds · reference {ref['MAE_late']:.2f}",
                 "ok" if row["MAE_late"] <= ref["MAE_late"] * 1.1 else "bad")
        with k2:
            gapd = off["MAE_late"] - ref["MAE_late"]
            if cm_on and gapd > 0.1:
                rec_pct = 100 * (off["MAE_late"] - row["MAE_late"]) / gapd
                card("Degradation recovered", f"{rec_pct:.0f}%",
                     f"of the {gapd:.2f}-min late-period gap vs. reference", "ok" if rec_pct > 50 else "warn")
            elif cm_on:
                delta = row["MAE_late"] - off["MAE_late"]
                card("Change vs. CM OFF", f"{delta:+.2f} min",
                     "no degradation gap to recover in this condition", "info")
            else:
                card("Degradation vs. reference", f"{gapd:+.2f} min",
                     "late-period MAE gap (countermeasure OFF)", "bad" if gapd > 0.5 else "info")
        with k3:
            if cm_on:
                rv = ret.get("rare_verified_retained_pct", np.nan)
                nv = ret.get("naive3sd_rare_retained_pct", np.nan)
                card("🛡️ Proof of non-evasion",
                     f"{ret.get('verified_retained_pct', np.nan):.1f}% kept",
                     f"verified records retained · rare zone {rv:.0f}% "
                     f"(a naive 3σ filter would keep {nv:.0f}%) · 100% of packages get an ETA",
                     "ok")
            else:
                card("🛡️ Proof of non-evasion", "100% kept",
                     "unmitigated system uses every record it receives", "info")
        with k4:
            card("Deferred to a human", f"{row['defer_pct']:.1f}%",
                 "of evaluated packages (ETA still issued, review required)",
                 "warn" if row["defer_pct"] > 5 else "ok")
        if cm_on and ret.get("fast_received", 0):
            st.caption(
                f"Fast-feed accounting (end of window): {ret['fast_received']:,} records received → "
                f"{ret.get('fast_superseded_pct', 0):.0f}% superseded by the verified copy, "
                f"{ret.get('fast_quarantined_pct', 0):.1f}% quarantined as physically implausible, "
                f"remainder used with bias correction {ret.get('fast_bias_est_min', 0):+.1f} min and "
                f"trust weight {ret.get('fast_trust', 0):.2f}.")

        # ---- full matrix ----
        st.markdown("#### Full matrix — same evaluation packages in every cell")
        tbl = summ.copy()
        tbl["Condition"] = tbl["condition"].astype(str)
        tbl = tbl.rename(columns={"countermeasure": "Countermeasure"})
        view = pd.DataFrame({
            "Condition": tbl["Condition"], "CM": tbl["Countermeasure"],
            "MAE all (min)": tbl["MAE_all"].map("{:.2f}".format) + " ± " + tbl["MAE_all_sd"].map("{:.2f}".format),
            "MAE late": tbl["MAE_late"].round(2),
            "MAE held-out wk": tbl["MAE_holdout"].round(2),
            "Bias late": tbl["Bias_late"].round(2),
            "P90 abs err": tbl["P90_all"].round(1),
            "MAE stale zones (late)": tbl["MAE_stale_regions_late"].round(2),
            "MAE rare zone": tbl["MAE_rare"].round(2),
            "Deferred %": tbl["defer_pct"].round(1),
            "Verified kept %": tbl["verified_retained_pct"].round(1),
            "Rare kept %": tbl["rare_verified_retained_pct"].round(1),
        })
        st.dataframe(view, hide_index=True, **FULL)
        st.caption(f"n = {int(summ['n_eval'].iloc[0]):,} evaluation packages per cell per seed · "
                   "mean over seeds (± sd) · late = days ≥ 15 · held-out = days ≥ 21 · "
                   "bias > 0 means over-prediction.")

        # ---- mechanism ----
        st.markdown("#### Mechanism: where the error goes (late period, signed bias by zone)")
        rows_off = R["runs"][(cond2, False)]["rows"]
        rows_on = R["runs"][(cond2, True)]["rows"]

        def zone_bias(rows):
            late = rows[rows["day"] >= LATE_DAY]
            return late.groupby("region")["err"].mean()

        zb_off, zb_on = zone_bias(rows_off), zone_bias(rows_on)
        names = [P["region_names"][i] + (" (stale-candidate)" if i in P["stale_regions"] else "")
                 + (" (rare)" if i == P["rare_region"] else "") for i in zb_off.index]
        mf = go.Figure()
        mf.add_trace(go.Bar(x=names, y=zb_off.to_numpy(), name="CM OFF", marker_color="#d64545"))
        mf.add_trace(go.Bar(x=names, y=zb_on.reindex(zb_off.index).to_numpy(), name="CM ON",
                            marker_color="#2e9e5b"))
        mf.add_hline(y=0, line_color="#888")
        mf.update_layout(barmode="group", height=320, margin=dict(l=10, r=10, t=10, b=10),
                         yaxis_title="mean signed error (min)", legend=dict(orientation="h"))
        st.plotly_chart(mf, **FULL)
        st.caption("Stale data shows up as **under-prediction concentrated in the frozen zones** "
                   "after the operating shift; the low-quality feed shows up as **citywide "
                   "over-prediction** that grows with its volume share. That localisation is the "
                   "diagnostic evidence that separates the two mechanisms.")

        with st.expander("📐 How the matrix states and the countermeasure are modelled"):
            st.markdown(f"""
**Walk-forward protocol.** For each operating day *d*, the store contains only records whose
*arrival* time < start of day *d*. The model is re-trained on that store and predicts every package
accepted on day *d*. Truth = actual duration of each package (offline only).

**Model.** Citywide weighted ridge regression plus a zone offset:
$\\hat y = g(x) + ZB_{{r,d}}$, $\\; g(x) = b_0 + b_1 \\,dist + b_2\\, dist^2/10 + \\sum hour + \\sum AOI$,
where $ZB_{{r,d}} = \\dfrac{{\\sum_{{i\\in r,\\,7d}} w_i\\,(y_i - g(x_i))}}{{\\sum w_i + k}}$, $k={SHRINK_K:.0f}$
(zone with no records → feed keeps its last value).

| Condition | Training store | Context feed (ZB) |
|---|---|---|
| Reference | verified records, {VERIFIED_LAG_H:.0f} h lag | fresh |
| Stale | verified records of top-2 zones stop arriving at day {FREEZE_DAY} | frozen for those zones; age grows |
| Low-quality | + fast feed: ratio {FAST_RATIO_START}→{FAST_RATIO_END}× verified, {FAST_DEFECT_RATE:.0%} with +Exp({FAST_DEFECT_MEAN_MIN:.0f}) min, GPS sd {FAST_GPS_NOISE_KM} km | inflated |
| Combined | both | both |

**Countermeasure (PFG).** weight $w_i = \\tau_{{src}} \\cdot 0.5^{{age_i/{HALF_LIFE_DAYS:.0f}d}} \\cdot \\mathbb 1[\\text{{plausible}}] \\cdot \\mathbb 1[\\text{{not superseded}}]$;
fast labels corrected $y_i - \\hat\\beta$ with $\\hat\\beta$ = trimmed mean of (fast − verified) on
matched package IDs and $\\tau = 100/(100+s^2)$. Stale guard at {STALE_AGE_H:.0f} h: audited fresh data
if ≥ {MIN_FRESH_EVIDENCE:.0f} effective records, else drift transfer
$ZB_{{r,d}} = ZB_{{r,d_0}}$ (last verified offset) while $g$ keeps re-training on fresh zones;
defer once verified age > {MAX_TRANSFER_AGE_H/24:.0f} days. Poisoning alert: unverified share ≥ {LQ_DOMINANCE:.0%}
and $|\\hat y_{{guarded}} - \\hat y_{{unguarded}}| \\ge {DISAGREE_MIN:.0f}$ min.

**Repeatability.** Seeds 0…{n_seeds - 1}; the seed controls fast-feed sampling and defects and is
shared by OFF/ON. Reference and Stale contain no randomness (sd = 0 is expected).
Engine time this run: {run_secs:.1f} s (cached after first run).
""")

    # =========================================================================
    # TAB 3 — GOVERNANCE, MANIFEST & PILOT
    # =========================================================================
    with tab3:
        meta = P["meta"]
        frame = P["frame"]
        st.subheader("Data manifest")
        g1, g2 = st.columns([1.3, 1])
        manifest = {
            "dataset_source": meta["source"],
            "source_url": meta["source_url"],
            "is_synthetic": meta["is_synthetic"],
            "fallback_reason": meta.get("fallback_reason"),
            "retrieval_date_and_version": ("TEAM TO FILL: download date + HF commit/revision"
                                           if not meta["is_synthetic"] else
                                           f"generated {datetime.now():%Y-%m-%d} by app.py (seed 7)"),
            "selected_files": [DATA_PATH] if not meta["is_synthetic"] else [],
            "raw_rows": meta.get("raw_rows"),
            "rows_used": int(P["n"]),
            "independent_packages": int(frame["package_id"].nunique()),
            "independent_couriers": int(frame["courier_id"].nunique()),
            "zones": len(P["region_names"]),
            "date_range": f"{frame['accept_time'].min():%Y-%m-%d} → {frame['accept_time'].max():%Y-%m-%d}"
                          + (f" (year assumed {LADE_ASSUMED_YEAR}; LaDe omits it)" if not meta["is_synthetic"] else
                             " (synthetic calendar)"),
            "fields_units": {"accept_time": "timestamp", "distance_km": "km (accept GPS→package)",
                             "actual_duration_min": "minutes (delivery − accept)",
                             "region_id": "zone id", "aoi_type": "category"},
            "subset_and_filter_rules": meta.get("subset_log", []),
            "split_rule": (f"walk-forward by day: train on records ARRIVED before day start, predict that "
                           f"day; warm-up days 0–{WARMUP_DAYS - 1}; no shuffling; held-out week days "
                           f"{HOLDOUT_DAY}+"),
            "retraining": "daily re-train (disclosed); ZoneBaseline refreshed daily",
            "leakage_controls": ["delivery GPS/time never used as features",
                                 "labels enter store only after arrival lag",
                                 "defect flags never visible to the model"],
            "stale_candidate_zones": [P["region_names"][i] for i in P["stale_regions"]],
            "rare_zone": P["region_names"][P["rare_region"]],
            "seeds": list(range(n_seeds)),
            "ai_component": "walk-forward weighted ridge regression (numpy); no LLM in the decision loop",
            "ai_and_prompt_disclosure": "TEAM TO FILL: AI tools used to write code/text and how outputs were checked",
            "rights_statement": "LaDe: HF metadata Apache-2.0 vs. author paper CC BY-NC 4.0 — data not redistributed",
        }
        with g1:
            rows_html = "".join(
                f"<tr><td><b>{k}</b></td><td>{(', '.join(map(str, v)) if isinstance(v, list) else v)}</td></tr>"
                for k, v in manifest.items() if not isinstance(v, dict) and v not in (None, []))
            st.markdown(f'<div class="card info"><table class="kv">{rows_html}</table></div>',
                        unsafe_allow_html=True)
        with g2:
            st.markdown("**Modification log** (introduced vs. observed kept separate)")
            mods = P["mods"] + [
                {"change": "Verified-feed sync freeze (stale)", "kind": "introduced (experiment)",
                 "fields": "store arrivals, ZoneBaseline",
                 "timing": f"day ≥ {FREEZE_DAY}",
                 "severity": f"top-2 zones: {', '.join(P['region_names'][i] for i in P['stale_regions'])}"},
                {"change": "Fast batch feed (low-quality, volume ramp)", "kind": "introduced (experiment)",
                 "fields": "duration label, distance",
                 "timing": "all days, ramping",
                 "severity": f"{FAST_RATIO_START}→{FAST_RATIO_END}× volume; {FAST_DEFECT_RATE:.0%} +Exp("
                             f"{FAST_DEFECT_MEAN_MIN:.0f}) min; GPS sd {FAST_GPS_NOISE_KM} km"},
                {"change": "Source filtering (see subset rules)", "kind": "observed in source",
                 "fields": "accept/delivery time", "timing": "load", "severity": "rows dropped as logged"},
            ]
            if meta["is_synthetic"]:
                mods.append({"change": "Operating shift built into synthetic outcomes",
                             "kind": "synthetic design", "fields": "actual_duration_min",
                             "timing": f"day ≥ {SHIFT_DAY}",
                             "severity": f"x{SHIFT_CITYWIDE_MULT}; +{SHIFT_REGION_ADD_MIN:.0f} min R2"})
            st.dataframe(pd.DataFrame(mods), hide_index=True, **FULL)
            dl1, dl2 = st.columns(2)
            dl1.download_button("⬇️ Manifest (JSON)", json.dumps(manifest, indent=2, default=str),
                                "data_manifest.json", "application/json")
            res_buf = io.StringIO()
            R["summary"].to_csv(res_buf, index=False)
            dl2.download_button("⬇️ Results per seed (CSV)", res_buf.getvalue(),
                                "results_matrix_by_seed.csv", "text/csv")
            dl3, dl4 = st.columns(2)
            dl3.download_button("⬇️ Daily timeline (CSV)", R["daily"].to_csv(index=False),
                                "results_daily.csv", "text/csv")
            if meta["is_synthetic"]:
                dl4.download_button("⬇️ Synthetic data (CSV)", frame.to_csv(index=False),
                                    "synthetic_deliveries.csv", "text/csv")

        st.markdown(
            '<div class="card warn"><h4>⚖️ License callout — LaDe</h4>'
            '<div class="sub" style="font-size:.9rem;opacity:1">The Hugging Face repository metadata lists '
            '<b>Apache-2.0</b>, while the author paper (Wu et al.) states <b>CC BY-NC 4.0</b>. We keep both '
            'references visible, treat the stricter non-commercial terms as binding until resolved, and do '
            '<b>not</b> redistribute the data — this app ships no LaDe rows and reads a locally downloaded '
            'CSV. Synthetic fallback data is generated in-app.</div></div>', unsafe_allow_html=True)

        # ---- boundary / failure cases (computed) ----
        st.subheader("Known boundary / failure cases (computed stress tests)")
        st.caption("Run on the Reference and Combined conditions with the same seeds. These are "
                   "previously unseen conditions: no countermeasure parameter was changed for them.")
        with st.spinner("Running stress tests…"):
            W = run_matrix(sig, inject, n_seeds, "weather")
            A = run_matrix(sig, inject, n_seeds, "anchor_fault")
        base_c = R["summary"][R["summary"]["condition"] == "Combined Degradation"]

        def window_mae(rr, lo, hi, cond="Combined Degradation"):
            s = rr["daily"]
            s = s[(s["day"] >= lo) & (s["day"] <= hi) & (s["condition"] == cond)]
            s = s.assign(w=s["mae"] * s["n"]).groupby("countermeasure")[["w", "n"]].sum()
            return (s["w"] / s["n"]).to_dict()

        wb, wn = window_mae(W, 22, 24), window_mae(R, 22, 24)
        wr = window_mae(W, 22, 24, "Reference / Nominal")
        ab, an = window_mae(A, 18, 27), window_mae(R, 18, 27)
        s1, s2 = st.columns(2)
        with s1:
            st.markdown(
                f'<div class="card bad"><h4>🌧️ Severe weather surge (days 22–24, all durations ×1.45)</h4>'
                f'<div class="sub" style="opacity:1;font-size:.9rem">MAE during surge — Reference: '
                f'<b>OFF {wr["OFF"]:.1f} / ON {wr["ON"]:.1f}</b>; Combined: <b>OFF {wb["OFF"]:.1f} / '
                f'ON {wb["ON"]:.1f} min</b> (same days, no surge: {wn["OFF"]:.1f} / {wn["ON"]:.1f}). '
                f'A sudden genuine slowdown is real data, not poisoning: the 7-day zone baseline and '
                f'{VERIFIED_LAG_H:.0f} h verification lag mean no configuration adapts within the surge. '
                f'Where CM OFF looks better, it is because poisoning-driven over-prediction happens to '
                f'cancel part of the slowdown — an accident, not resilience. <b>Boundary:</b> the guard '
                f'does not detect sudden genuine change; it needs an external signal (weather/traffic '
                f'feed) to trigger deferral.</div></div>', unsafe_allow_html=True)
        with s2:
            st.markdown(
                f'<div class="card bad"><h4>🧭 Trusted feed fails (verified labels +15 min from day 18)</h4>'
                f'<div class="sub" style="opacity:1;font-size:.9rem">MAE days 18–27: '
                f'<b>CM OFF {ab["OFF"]:.1f}</b> vs <b>CM ON {ab["ON"]:.1f} min</b> '
                f'(without the fault: {an["OFF"]:.1f} / {an["ON"]:.1f}). The audit treats the verified feed '
                f'as the anchor; when the anchor itself drifts, the bias correction is computed against a '
                f'wrong reference. <b>Boundary:</b> the countermeasure cannot protect against a degraded '
                f'reference — it needs periodic ground-truth spot checks (e.g. sampled proof-of-delivery '
                f'audits) owned by the data-quality owner.</div></div>', unsafe_allow_html=True)
        sf = go.Figure()
        for lab, rr, colr in (("Weather surge", W, "#3b7dd8"), ("Trusted-feed fault", A, "#d9822b")):
            for flag, dash in (("OFF", "dot"), ("ON", "solid")):
                s = rr["daily"][(rr["daily"]["countermeasure"] == flag) & (rr["daily"]["day"] >= WARMUP_DAYS)]
                s = s.groupby("day")["mae"].mean()
                sf.add_trace(go.Scatter(x=s.index, y=s.values, name=f"{lab} · CM {flag}",
                                        line=dict(color=colr, dash=dash, width=2)))
        sf.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="operating day",
                         yaxis_title="MAE (min)", legend=dict(orientation="h"))
        st.plotly_chart(sf, **FULL)

        # ---- pilot sign-off ----
        st.subheader("Pilot readiness & sign-off (mock)")
        comb_on = summ[(summ["condition"] == "Combined Degradation") & (summ["countermeasure"] == "ON")].iloc[0]
        comb_off = summ[(summ["condition"] == "Combined Degradation") & (summ["countermeasure"] == "OFF")].iloc[0]
        p1, p2 = st.columns([1.2, 1])
        with p1:
            st.markdown(f"""
<div class="card ok"><h4>📝 Controlled pilot — proposed bounded next action</h4>
<table class="kv">
<tr><td><b>Scope</b></td><td>Shadow mode, 1 depot, 4 weeks: guarded ETAs shown to dispatchers next to current ETAs; no automatic routing changes.</td></tr>
<tr><td><b>Operational owner</b></td><td>Depot operations manager (accountable) · shift leads (deferral review)</td></tr>
<tr><td><b>Data-quality owner</b></td><td>Logistics data engineering lead — feed health, weekly fast-feed audit, ground-truth spot checks</td></tr>
<tr><td><b>Model owner</b></td><td>Analytics team — thresholds, weekly 4×2 re-run, change log</td></tr>
<tr><td><b>Evidence so far</b></td><td>Combined condition, late MAE {comb_off['MAE_late']:.2f} → {comb_on['MAE_late']:.2f} min; deferral {comb_on['defer_pct']:.1f}%; verified records kept {comb_on['verified_retained_pct']:.1f}%.</td></tr>
<tr><td><b>Approvals needed</b></td><td>Ops manager sign-off; data-sharing approval for feed metadata; privacy review (courier IDs)</td></tr>
<tr><td><b>Logging</b></td><td>Every prediction: model version, data-health badge, data age, source mix; every review: action, time, reviewer</td></tr>
<tr><td><b>Decision checkpoint</b></td><td>End of week 2 and week 4: continue / iterate / stop</td></tr>
</table></div>""", unsafe_allow_html=True)
        with p2:
            st.markdown(f"""
<div class="card bad"><h4>🛑 Stop / do-not-use conditions</h4>
<ul style="margin:0;padding-left:18px;font-size:.9rem">
<li>Deferral rate > 20% of packages for 2 consecutive days (review load unsustainable)</li>
<li>Guarded-model MAE worse than current ETA system for a full week</li>
<li>Any zone with verified data older than 7 days (beyond drift-transfer validity)</li>
<li>Ground-truth spot check shows verified-feed bias > 5 min (anchor failure — see above)</li>
<li>Known external disruption (weather, road closure): switch to manual ETAs</li>
<li>Never for safety-critical or autonomous-vehicle control; ETA advisory only</li>
</ul></div>""", unsafe_allow_html=True)
            st.checkbox("Ops manager: approve shadow-mode pilot (mock)", key="so1")
            st.checkbox("Data-quality owner: accept feed-health responsibilities (mock)", key="so2")
            if ss.get("so1") and ss.get("so2"):
                st.success("Mock sign-off recorded for this session. Pilot remains advisory-only.")
        st.caption("NIST AI RMF is used only as an optional organising reference "
                   "(Govern · Map · Measure · Manage); it is not deployment authorization. "
                   "No Human Readiness Level is self-awarded.")


if __name__ == "__main__":
    main()
