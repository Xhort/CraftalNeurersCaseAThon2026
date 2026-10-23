from __future__ import annotations

import numpy as np

from data import make_synthetic_delivery_data
from features import MODEL_FEATURES, chronological_split, prepare_features
from model import fit_eta_model, metric_row
from scenarios import apply_condition


def _splits():
    prepared = prepare_features(make_synthetic_delivery_data(1_500, seed=4))
    return chronological_split(prepared)


def test_features_are_finite_and_target_is_plausible():
    prepared = prepare_features(make_synthetic_delivery_data(500, seed=2))
    assert prepared[MODEL_FEATURES].replace([np.inf, -np.inf], np.nan).notna().all().all()
    assert prepared["duration_minutes"].between(1, 360).all()


def test_split_dates_never_overlap():
    train, validation, test = _splits()
    assert set(train["service_date"]).isdisjoint(validation["service_date"])
    assert set(train["service_date"]).isdisjoint(test["service_date"])
    assert set(validation["service_date"]).isdisjoint(test["service_date"])


def test_conditions_keep_the_same_test_orders():
    train, validation, test = _splits()
    reference = apply_condition(train, validation, test, "reference", 0.55, 17)[2]
    combined = apply_condition(train, validation, test, "combined", 0.55, 17)[2]
    assert reference["package_id"].tolist() == combined["package_id"].tolist()
    assert reference["duration_minutes"].tolist() == combined["duration_minutes"].tolist()


def test_model_fits_and_reports_metrics():
    train, validation, test = _splits()
    fitted = fit_eta_model(train, validation, seed=17, mitigated=False)
    prediction = fitted.predict(test)
    baseline = fitted.predict_baseline(test)
    _, flagged = fitted.intervals_and_flags(test)
    metrics = metric_row(test["duration_minutes"], prediction, baseline, flagged)
    assert len(prediction) == len(test)
    assert metrics["mae_minutes"] >= 0
    assert 0 <= metrics["over_15_minutes_rate"] <= 1

