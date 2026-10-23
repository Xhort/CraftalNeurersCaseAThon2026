"""Run the full 4-condition by 2-mitigation experiment grid."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from config import ARTIFACTS_DIR, CONDITIONS, RESULTS_DIR, ExperimentConfig, ensure_output_dirs
from features import chronological_split, prepare_features
from mitigation import review_reason
from model import fit_eta_model, metric_row
from scenarios import apply_condition


def _timeline_rows(
    evaluation: pd.DataFrame,
    predictions: np.ndarray,
    baselines: np.ndarray,
    flagged: np.ndarray,
    condition: str,
    mitigated: bool,
    seed: int,
    late_threshold: float,
) -> list[dict[str, object]]:
    working = evaluation.copy()
    working["prediction"] = predictions
    working["baseline_prediction"] = baselines
    working["flagged"] = flagged
    rows: list[dict[str, object]] = []
    for date, group in working.groupby("service_date", sort=True):
        values = metric_row(
            group["duration_minutes"],
            group["prediction"].to_numpy(),
            group["baseline_prediction"].to_numpy(),
            group["flagged"].to_numpy(),
            late_threshold,
        )
        rows.append(
            {
                "service_date": pd.Timestamp(date).date().isoformat(),
                "condition": condition,
                "mitigated": mitigated,
                "seed": seed,
                "data_age_days": float(group["data_age_days"].mean()),
                "low_quality_share": float(group["low_quality_share"].mean()),
                "source_reliability": float(group["source_reliability"].mean()),
                **values,
            }
        )
    return rows


def run_suite(
    raw: pd.DataFrame,
    config: ExperimentConfig = ExperimentConfig(),
    save_outputs: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run fair comparisons on one fixed chronological test set."""
    prepared = prepare_features(raw)
    train, validation, test = chronological_split(
        prepared, config.train_fraction, config.validation_fraction
    )
    run_rows: list[dict[str, object]] = []
    timeline_rows: list[dict[str, object]] = []
    prediction_frames: list[pd.DataFrame] = []

    for condition in CONDITIONS:
        for seed in config.seeds:
            scenario_train, scenario_validation, scenario_test = apply_condition(
                train,
                validation,
                test,
                condition,
                config.noisy_feed_share,
                seed,
            )
            for mitigated in (False, True):
                fitted = fit_eta_model(
                    scenario_train,
                    scenario_validation,
                    seed,
                    mitigated,
                    config.stale_after_days,
                )
                prediction = fitted.predict(scenario_test)
                baseline = fitted.predict_baseline(scenario_test)
                half_width, flagged = fitted.intervals_and_flags(scenario_test)
                metrics = metric_row(
                    scenario_test["duration_minutes"],
                    prediction,
                    baseline,
                    flagged,
                    config.late_threshold_minutes,
                )
                run_rows.append(
                    {
                        "condition": condition,
                        "mitigated": mitigated,
                        "seed": seed,
                        "data_mode": str(scenario_test["data_mode"].iloc[0]),
                        **metrics,
                    }
                )
                timeline_rows.extend(
                    _timeline_rows(
                        scenario_test,
                        prediction,
                        baseline,
                        flagged,
                        condition,
                        mitigated,
                        seed,
                        config.late_threshold_minutes,
                    )
                )
                if seed == config.seeds[0]:
                    sample = scenario_test.copy()
                    sample["condition"] = condition
                    sample["mitigated"] = mitigated
                    sample["prediction_minutes"] = prediction
                    sample["interval_low"] = np.maximum(0, prediction - half_width)
                    sample["interval_high"] = prediction + half_width
                    sample["flagged_for_review"] = flagged
                    sample["review_reason"] = sample.apply(
                        review_reason, axis=1, stale_after_days=config.stale_after_days
                    )
                    prediction_frames.append(
                        sample[
                            [
                                "package_id",
                                "service_date",
                                "city",
                                "region_id",
                                "condition",
                                "mitigated",
                                "duration_minutes",
                                "prediction_minutes",
                                "interval_low",
                                "interval_high",
                                "flagged_for_review",
                                "review_reason",
                                "data_age_days",
                                "low_quality_share",
                                "source_reliability",
                                "data_mode",
                            ]
                        ]
                    )
                    if save_outputs:
                        ensure_output_dirs()
                        suffix = "mitigated" if mitigated else "unmitigated"
                        joblib.dump(fitted, ARTIFACTS_DIR / f"{condition}_{suffix}.joblib")

    runs = pd.DataFrame(run_rows)
    metric_columns = [
        "mae_minutes",
        "rmse_minutes",
        "over_15_minutes_rate",
        "baseline_mae_minutes",
        "deferred_rate",
    ]
    summary = (
        runs.groupby(["condition", "mitigated", "data_mode"], as_index=False)[metric_columns]
        .agg(["mean", "std"])
    )
    summary.columns = [
        "_".join(str(part) for part in column if part).rstrip("_")
        if isinstance(column, tuple)
        else str(column)
        for column in summary.columns
    ]
    timeline = pd.DataFrame(timeline_rows)
    predictions = pd.concat(prediction_frames, ignore_index=True)

    if save_outputs:
        ensure_output_dirs()
        runs.to_csv(RESULTS_DIR / "runs.csv", index=False)
        summary.to_csv(RESULTS_DIR / "summary.csv", index=False)
        timeline.to_csv(RESULTS_DIR / "timeline.csv", index=False)
        predictions.to_csv(RESULTS_DIR / "predictions.csv", index=False)
        pd.Series(asdict(config)).to_json(RESULTS_DIR / "experiment_config.json", indent=2)
    return summary, timeline, predictions

