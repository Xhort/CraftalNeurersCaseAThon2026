"""Transparent countermeasures and review/defer policy."""

from __future__ import annotations

import numpy as np
import pandas as pd


def training_weights(frame: pd.DataFrame, stale_after_days: int = 7) -> np.ndarray:
    """Reduce, but do not eliminate, influence from stale or unreliable rows."""
    reliability = frame["source_reliability"].clip(0.10, 1.0).to_numpy(float)
    excess_age = (frame["data_age_days"] - stale_after_days).clip(lower=0).to_numpy(float)
    freshness = np.exp(-excess_age / 14.0)
    return np.clip(reliability * freshness, 0.08, 1.0)


def mitigate_inference_features(
    frame: pd.DataFrame,
    training_target_median: float,
    stale_after_days: int = 7,
) -> pd.DataFrame:
    """Back off suspect history and location values toward robust defaults."""
    safe = frame.copy()
    excess_age = (safe["data_age_days"] - stale_after_days).clip(lower=0)
    freshness = np.exp(-excess_age / 14.0)
    trust = np.minimum(freshness, safe["source_reliability"].clip(0.10, 1.0))
    safe["area_historical_eta"] = (
        trust * safe["area_historical_eta"]
        + (1 - trust) * training_target_median
    )
    distance_median = float(safe["distance_km"].median())
    safe["distance_km"] = (
        safe["source_reliability"] * safe["distance_km"]
        + (1 - safe["source_reliability"]) * distance_median
    )
    return safe


def review_risk(frame: pd.DataFrame, stale_after_days: int = 7) -> np.ndarray:
    age_risk = np.clip(frame["data_age_days"].to_numpy(float) / max(stale_after_days * 3, 1), 0, 1)
    source_risk = 1 - frame["source_reliability"].to_numpy(float)
    volume_risk = np.clip(frame["low_quality_share"].to_numpy(float), 0, 1)
    return np.clip(0.40 * age_risk + 0.40 * source_risk + 0.20 * volume_risk, 0, 1)


def review_reason(row: pd.Series, stale_after_days: int = 7) -> str:
    reasons: list[str] = []
    if float(row["data_age_days"]) > stale_after_days:
        reasons.append(f"area statistics are {int(row['data_age_days'])} days old")
    if float(row["source_reliability"]) < 0.5:
        reasons.append("location input came from a low-reliability feed")
    if float(row["low_quality_share"]) >= 0.5:
        reasons.append(f"unreliable feed supplies {row['low_quality_share']:.0%} of recent inputs")
    return "; ".join(reasons) if reasons else "inputs passed the configured reliability checks"

