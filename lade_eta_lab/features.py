"""Leakage-aware feature preparation for ETA prediction."""

from __future__ import annotations

import numpy as np
import pandas as pd


MODEL_FEATURES = [
    "region_code",
    "aoi_type_code",
    "accept_hour",
    "hour_sin",
    "hour_cos",
    "day_of_week",
    "distance_km",
    "courier_recent_accepts",
    "area_historical_eta",
    "courier_historical_eta",
]


def _parse_event_time(values: pd.Series) -> pd.Series:
    text = values.astype("string").str.strip()
    has_year = text.str.match(r"^\d{4}-", na=False)
    parsed = pd.Series(pd.NaT, index=text.index, dtype="datetime64[ns]")
    parsed.loc[has_year] = pd.to_datetime(text.loc[has_year], errors="coerce")
    parsed.loc[~has_year] = pd.to_datetime(
        "2020-" + text.loc[~has_year],
        format="%Y-%m-%d %H:%M:%S",
        errors="coerce",
    )
    return parsed


def _distance_km(frame: pd.DataFrame) -> pd.Series:
    end_lng = pd.to_numeric(frame["lng"], errors="coerce")
    end_lat = pd.to_numeric(frame["lat"], errors="coerce")
    start_lng = pd.to_numeric(frame.get("accept_gps_lng", end_lng), errors="coerce")
    start_lat = pd.to_numeric(frame.get("accept_gps_lat", end_lat), errors="coerce")
    raw = np.sqrt((end_lng - start_lng) ** 2 + (end_lat - start_lat) ** 2)
    # LaDe contains both projected coordinates and longitude/latitude-like values.
    projected = pd.concat([end_lng.abs(), end_lat.abs()]).median() > 1_000
    return raw / 1_000 if projected else raw * 86.0


def _prior_day_mean(frame: pd.DataFrame, group_column: str) -> pd.Series:
    """Return group history from completed prior days, never from the current day."""
    daily = (
        frame.groupby([group_column, "service_date"], as_index=False)["duration_minutes"]
        .mean()
        .sort_values([group_column, "service_date"])
    )
    daily["prior_mean"] = daily.groupby(group_column, sort=False)["duration_minutes"].transform(
        lambda values: values.shift(1).expanding(min_periods=1).mean()
    )
    lookup = daily.set_index([group_column, "service_date"])["prior_mean"]
    keys = pd.MultiIndex.from_frame(frame[[group_column, "service_date"]])
    result = pd.Series(lookup.reindex(keys).to_numpy(), index=frame.index, dtype=float)

    global_daily = frame.groupby("service_date")["duration_minutes"].mean().sort_index()
    global_prior = global_daily.shift(1).expanding(min_periods=1).mean()
    fallback = frame["service_date"].map(global_prior).fillna(60.0)
    return result.fillna(fallback)


def prepare_features(raw: pd.DataFrame) -> pd.DataFrame:
    """Create features available at acceptance time plus a future label for training."""
    frame = raw.copy()
    if "package_id" not in frame:
        frame["package_id"] = [f"row-{i}" for i in range(len(frame))]
    frame["accept_ts"] = _parse_event_time(frame["accept_time"])
    frame["delivery_ts"] = _parse_event_time(frame["delivery_time"])
    crossed_midnight = frame["delivery_ts"] < frame["accept_ts"]
    frame.loc[crossed_midnight, "delivery_ts"] += pd.Timedelta(1, unit="D")
    frame["duration_minutes"] = (
        frame["delivery_ts"] - frame["accept_ts"]
    ).dt.total_seconds() / 60
    frame = frame.loc[
        frame["accept_ts"].notna()
        & frame["delivery_ts"].notna()
        & frame["duration_minutes"].between(1, 360)
    ].copy()
    frame = frame.sort_values("accept_ts", kind="stable").reset_index(drop=True)

    frame["service_date"] = frame["accept_ts"].dt.floor("D")
    frame["accept_hour"] = frame["accept_ts"].dt.hour + frame["accept_ts"].dt.minute / 60
    frame["hour_sin"] = np.sin(2 * np.pi * frame["accept_hour"] / 24)
    frame["hour_cos"] = np.cos(2 * np.pi * frame["accept_hour"] / 24)
    frame["day_of_week"] = frame["accept_ts"].dt.dayofweek
    frame["distance_km"] = _distance_km(frame).clip(0, 100)
    frame["region_code"], _ = pd.factorize(frame["region_id"].astype("string"), sort=True)
    frame["aoi_type_code"], _ = pd.factorize(
        frame.get("aoi_type", pd.Series("unknown", index=frame.index)).astype("string"),
        sort=True,
    )

    # Prior-day outcomes are guaranteed to be known at the current acceptance time.
    frame["area_historical_eta"] = _prior_day_mean(frame, "region_id")
    frame["courier_historical_eta"] = _prior_day_mean(frame, "courier_id")
    # A leakage-safe workload proxy: tasks already accepted by this courier today.
    frame["courier_recent_accepts"] = frame.groupby(
        ["courier_id", "service_date"], sort=False
    ).cumcount()
    frame["data_age_days"] = 0.0
    frame["source_reliability"] = 1.0
    frame["low_quality_share"] = 0.0
    frame["source_name"] = "primary"
    frame["data_mode"] = frame.get("data_mode", "unknown")
    return frame


def chronological_split(
    frame: pd.DataFrame,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split on whole dates so a courier-day cannot cross a boundary."""
    dates = np.array(sorted(frame["service_date"].dropna().unique()))
    if len(dates) < 5:
        raise ValueError("At least five distinct dates are required for chronological evaluation")
    train_end = max(1, int(len(dates) * train_fraction))
    validation_end = max(train_end + 1, int(len(dates) * (train_fraction + validation_fraction)))
    validation_end = min(validation_end, len(dates) - 1)
    train_dates = set(dates[:train_end])
    validation_dates = set(dates[train_end:validation_end])
    test_dates = set(dates[validation_end:])
    train = frame[frame["service_date"].isin(train_dates)].copy()
    validation = frame[frame["service_date"].isin(validation_dates)].copy()
    test = frame[frame["service_date"].isin(test_dates)].copy()
    return train, validation, test

