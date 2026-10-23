"""ETA model, honest baseline, uncertainty estimate, and metrics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline

from features import MODEL_FEATURES
from mitigation import mitigate_inference_features, review_risk, training_weights


@dataclass
class FittedETA:
    pipeline: Pipeline
    baseline_by_region: pd.Series
    global_baseline: float
    residual_q90: float
    mitigated: bool
    stale_after_days: int

    def _model_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        if not self.mitigated:
            return frame
        return mitigate_inference_features(
            frame, self.global_baseline, self.stale_after_days
        )

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return self.pipeline.predict(self._model_frame(frame)[MODEL_FEATURES])

    def predict_baseline(self, frame: pd.DataFrame) -> np.ndarray:
        return (
            frame["region_id"]
            .map(self.baseline_by_region)
            .fillna(self.global_baseline)
            .to_numpy(float)
        )

    def intervals_and_flags(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        risk = review_risk(frame, self.stale_after_days)
        half_width = self.residual_q90 * (1 + risk)
        flagged = risk >= 0.35
        return half_width, flagged


def fit_eta_model(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    seed: int,
    mitigated: bool,
    stale_after_days: int = 7,
) -> FittedETA:
    estimator = HistGradientBoostingRegressor(
        learning_rate=0.07,
        max_iter=100,
        max_leaf_nodes=31,
        min_samples_leaf=25,
        l2_regularization=0.5,
        random_state=seed,
        loss="absolute_error" if mitigated else "squared_error",
    )
    pipeline = Pipeline(
        [("impute", SimpleImputer(strategy="median")), ("model", estimator)]
    )
    weights = training_weights(train, stale_after_days) if mitigated else None
    pipeline.fit(train[MODEL_FEATURES], train["duration_minutes"], model__sample_weight=weights)
    region_baseline = train.groupby("region_id")["duration_minutes"].median()
    global_baseline = float(train["duration_minutes"].median())

    validation_input = (
        mitigate_inference_features(validation, global_baseline, stale_after_days)
        if mitigated
        else validation
    )
    validation_prediction = pipeline.predict(validation_input[MODEL_FEATURES])
    residual_q90 = float(
        np.quantile(np.abs(validation["duration_minutes"] - validation_prediction), 0.90)
    )
    return FittedETA(
        pipeline=pipeline,
        baseline_by_region=region_baseline,
        global_baseline=global_baseline,
        residual_q90=max(residual_q90, 1.0),
        mitigated=mitigated,
        stale_after_days=stale_after_days,
    )


def metric_row(
    actual: pd.Series | np.ndarray,
    prediction: np.ndarray,
    baseline_prediction: np.ndarray,
    flagged: np.ndarray,
    late_threshold_minutes: float = 15.0,
) -> dict[str, float]:
    actual_array = np.asarray(actual, dtype=float)
    absolute_error = np.abs(actual_array - prediction)
    return {
        "mae_minutes": float(mean_absolute_error(actual_array, prediction)),
        "rmse_minutes": float(mean_squared_error(actual_array, prediction) ** 0.5),
        "over_15_minutes_rate": float(np.mean(absolute_error > late_threshold_minutes)),
        "baseline_mae_minutes": float(mean_absolute_error(actual_array, baseline_prediction)),
        "deferred_rate": float(np.mean(flagged)),
        "n_test": int(len(actual_array)),
    }

