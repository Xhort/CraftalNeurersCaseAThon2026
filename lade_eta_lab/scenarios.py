"""Controlled stale-data and low-quality-data interventions."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import CONDITIONS


def _freeze_history(train: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    degraded = test.copy()
    snapshots = (
        train.sort_values("accept_ts")
        .groupby("region_id", sort=False)
        .tail(1)
        .set_index("region_id")["area_historical_eta"]
    )
    fallback = float(train["duration_minutes"].median())
    degraded["area_historical_eta"] = (
        degraded["region_id"].map(snapshots).fillna(fallback)
    )
    last_update = train["service_date"].max()
    degraded["data_age_days"] = (
        degraded["service_date"] - last_update
    ).dt.days.clip(lower=1).astype(float)
    return degraded


def _inject_low_quality_training_rows(
    train: pd.DataFrame,
    share: float,
    rng: np.random.Generator,
) -> pd.DataFrame:
    if not 0 < share < 0.85:
        raise ValueError("noisy feed share must be between 0 and 0.85")
    duplicate_count = int(len(train) * share / (1 - share))
    positions = rng.integers(0, len(train), duplicate_count)
    noisy = train.iloc[positions].copy()
    noise_scale = max(float(train["duration_minutes"].std()), 5.0)
    noisy["duration_minutes"] = (
        noisy["duration_minutes"] + rng.normal(18, noise_scale * 0.9, duplicate_count)
    ).clip(1, 360)
    noisy["distance_km"] = (
        noisy["distance_km"] * rng.lognormal(0.35, 0.55, duplicate_count)
    ).clip(0, 100)
    noisy["area_historical_eta"] += rng.normal(12, 14, duplicate_count)
    noisy["source_reliability"] = 0.25
    noisy["low_quality_share"] = share
    noisy["source_name"] = "high_volume_unreliable"
    noisy["package_id"] = noisy["package_id"].astype("string") + "-noisy-copy"
    clean = train.copy()
    clean["low_quality_share"] = share
    return pd.concat([clean, noisy], ignore_index=True)


def _corrupt_test_inputs(
    test: pd.DataFrame,
    max_share: float,
    rng: np.random.Generator,
) -> pd.DataFrame:
    degraded = test.copy()
    date_rank = degraded["service_date"].rank(method="dense", pct=True)
    row_probability = 0.10 + (max_share - 0.10) * date_rank
    affected = rng.random(len(degraded)) < row_probability.to_numpy()
    count = int(affected.sum())
    degraded["low_quality_share"] = row_probability
    degraded.loc[affected, "distance_km"] = (
        degraded.loc[affected, "distance_km"]
        * rng.lognormal(0.35, 0.5, count)
    ).clip(0, 100)
    degraded.loc[affected, "area_historical_eta"] += rng.normal(10, 12, count)
    degraded.loc[affected, "source_reliability"] = 0.25
    degraded.loc[affected, "source_name"] = "high_volume_unreliable"
    return degraded


def apply_condition(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    test: pd.DataFrame,
    condition: str,
    noisy_feed_share: float,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Apply one isolated condition while retaining identical evaluation rows."""
    if condition not in CONDITIONS:
        raise ValueError(f"Unknown condition '{condition}'")
    rng = np.random.default_rng(seed)
    train_out, validation_out, test_out = train.copy(), validation.copy(), test.copy()

    if condition in {"stale", "combined"}:
        validation_out = _freeze_history(train_out, validation_out)
        test_out = _freeze_history(train_out, test_out)

    if condition in {"low_quality", "combined"}:
        train_out = _inject_low_quality_training_rows(train_out, noisy_feed_share, rng)
        validation_out = _corrupt_test_inputs(validation_out, noisy_feed_share, rng)
        test_out = _corrupt_test_inputs(test_out, noisy_feed_share, rng)

    return train_out, validation_out, test_out

