"""FastAPI application for the LaDe ETA Reliability Lab.

Run from this directory with:
    uvicorn app:app --reload
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from config import (
    ARTIFACTS_DIR,
    CONDITIONS,
    DATA_DIR,
    DEFAULT_CONFIG,
    PROJECT_DIR,
    RESULTS_DIR,
    ExperimentConfig,
)
from data import load_delivery_csv, make_synthetic_delivery_data
from evaluate import run_suite
from features import MODEL_FEATURES, chronological_split, prepare_features
from mitigation import review_reason
from scenarios import apply_condition


ROOT_DIR = PROJECT_DIR.parent
STATIC_DIR = PROJECT_DIR / "static"
DEMO_DIR = PROJECT_DIR / "demo"
OVERRIDES_PATH = RESULTS_DIR / "overrides.csv"
DOCS = {
    "setup": PROJECT_DIR / "README.md",
    "deployment": PROJECT_DIR / "CLOUDFLARE_DEPLOYMENT.md",
    "method": PROJECT_DIR / "EXPERIMENT_CARD.md",
    "data": PROJECT_DIR / "DATA_MANIFEST.md",
    "governance": PROJECT_DIR / "GOVERNANCE.md",
    "ai": PROJECT_DIR / "AI_USE_DISCLOSURE.md",
    "architecture": PROJECT_DIR / "ARCHITECTURE.md",
    "demo": PROJECT_DIR / "DEMO_SCRIPT.md",
    "api": PROJECT_DIR / "API.md",
}
DOWNLOADS = {
    "summary.csv": RESULTS_DIR / "summary.csv",
    "runs.csv": RESULTS_DIR / "runs.csv",
    "timeline.csv": RESULTS_DIR / "timeline.csv",
    "predictions.csv": RESULTS_DIR / "predictions.csv",
    "experiment_config.json": RESULTS_DIR / "experiment_config.json",
    "data_manifest.json": RESULTS_DIR / "data_manifest.json",
    "modification_log.csv": RESULTS_DIR / "modification_log.csv",
    "metrics_dictionary.json": RESULTS_DIR / "metrics_dictionary.json",
    "overrides.csv": OVERRIDES_PATH,
    "fallback_demo.mp4": DEMO_DIR / "fallback_demo.mp4",
}
_decision_lock = threading.Lock()


class DecisionRequest(BaseModel):
    package_id: str
    condition: Literal["reference", "stale", "low_quality", "combined"]
    mitigated: bool
    action: Literal["accept", "override", "escalate"]
    override_minutes: float | None = Field(default=None, ge=1, le=360)
    reviewer: str = Field(default="Demo dispatcher", min_length=1, max_length=80)
    review_seconds: float | None = Field(default=None, ge=0, le=3600)
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_override(self) -> "DecisionRequest":
        if self.action == "override" and self.override_minutes is None:
            raise ValueError("override_minutes is required for an override")
        if self.action != "override" and self.override_minutes is not None:
            raise ValueError("override_minutes is only valid for an override")
        return self


class PreviewRequest(BaseModel):
    rows: int = Field(default=2500, ge=1000, le=6000)
    seed: int = Field(default=17, ge=0, le=1_000_000)


class LivePredictionRequest(BaseModel):
    package_id: str = Field(min_length=1, max_length=100)
    scenario: Literal["clean", "poisoned", "safeguarded"]


class UploadedPredictionRow(BaseModel):
    package_id: str | int = Field(default="uploaded-row")
    city: str | None = Field(default=None, max_length=100)
    region_id: str | int | None = None
    region_code: float = Field(ge=0, le=100_000)
    aoi_type_code: float = Field(ge=0, le=100_000)
    accept_hour: float = Field(ge=0, lt=24)
    day_of_week: float = Field(ge=0, le=6)
    distance_km: float = Field(ge=0, le=100)
    courier_recent_accepts: float = Field(ge=0, le=10_000)
    area_historical_eta: float = Field(ge=0, le=360)
    courier_historical_eta: float = Field(ge=0, le=360)
    duration_minutes: float | None = Field(default=None, ge=1, le=360)


class UploadedPredictionRequest(BaseModel):
    rows: list[UploadedPredictionRow] = Field(min_length=1, max_length=50)


app = FastAPI(
    title="LaDe ETA Reliability Lab API",
    version="1.0.0",
    description="Evidence API for the 2026 Case-A-Thon unintentional data poisoning demonstration.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/demo", StaticFiles(directory=DEMO_DIR), name="demo")
app.mount("/lade_eta_lab/static", StaticFiles(directory=STATIC_DIR), name="preview-static")
app.mount("/lade_eta_lab/demo", StaticFiles(directory=DEMO_DIR), name="preview-demo")


def _json_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def _read_csv(name: str) -> pd.DataFrame:
    path = RESULTS_DIR / name
    if not path.exists():
        raise HTTPException(503, f"Missing {name}. Run the experiment first.")
    frame = pd.read_csv(path)
    if "mitigated" in frame:
        frame["mitigated"] = frame["mitigated"].astype(str).str.lower().eq("true")
    return frame


@lru_cache(maxsize=1)
def _data_profile() -> dict[str, object]:
    path = DATA_DIR / "delivery_jl.csv"
    if not path.exists():
        manifest_path = RESULTS_DIR / "data_manifest.json"
        if not manifest_path.exists():
            return {"available": False}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        date_range = manifest.get("date_range", {})
        return {
            "available": True,
            "dataset": manifest.get("dataset", "LaDe delivery data"),
            "city": manifest.get("city", "Jilin"),
            "source_rows": int(manifest.get("source_rows", 0)),
            "independent_cases": int(manifest.get("independent_cases", 0)),
            "rows_after_filters": int(manifest.get("rows_after_filters", 0)),
            "date_start": date_range.get("start"),
            "date_end": date_range.get("end"),
            "missing_accept_gps_rate": manifest.get("missing_accept_gps_rate"),
            "raw_data_redistributed": bool(
                manifest.get("rights", {}).get("raw_data_redistributed", False)
            ),
            "profile_source": "published experiment manifest",
        }
    frame = load_delivery_csv(path)
    prepared = prepare_features(frame)
    return {
        "available": True,
        "dataset": "LaDe delivery data",
        "city": str(frame["city"].mode().iloc[0]),
        "source_rows": int(len(frame)),
        "independent_cases": int(frame["package_id"].nunique()),
        "rows_after_filters": int(len(prepared)),
        "date_start": prepared["service_date"].min().date().isoformat(),
        "date_end": prepared["service_date"].max().date().isoformat(),
        "missing_accept_gps_rate": round(float(frame["accept_gps_lng"].isna().mean()), 4),
        "raw_data_redistributed": False,
    }


def _summary_lookup() -> dict[tuple[str, bool], dict[str, object]]:
    summary = _read_csv("summary.csv")
    return {
        (str(row["condition"]), bool(row["mitigated"])): row.to_dict()
        for _, row in summary.iterrows()
    }


def _finding_payload() -> dict[str, object]:
    values = _summary_lookup()
    reference = float(values[("reference", False)]["mae_minutes_mean"])
    low = float(values[("low_quality", False)]["mae_minutes_mean"])
    low_fixed = float(values[("low_quality", True)]["mae_minutes_mean"])
    combined = float(values[("combined", False)]["mae_minutes_mean"])
    combined_fixed = float(values[("combined", True)]["mae_minutes_mean"])
    stale = float(values[("stale", False)]["mae_minutes_mean"])
    return {
        "reference_mae": reference,
        "low_quality_delta": low - reference,
        "low_quality_recovery": low - low_fixed,
        "combined_delta": combined - reference,
        "combined_mitigation_delta": combined_fixed - combined,
        "stale_delta": stale - reference,
        "headline": (
            f"The high-volume unreliable feed raised ETA error by {low-reference:.1f} minutes. "
            f"The tested fix recovered {low-low_fixed:.1f} minutes, but did not restore the reference result."
        ),
        "boundary": (
            "The stale-only injection did not worsen MAE, while the review rule deferred every stale and "
            "combined case. That is a measured failure boundary, not a hidden result."
        ),
    }


_LIVE_ARTIFACTS = {
    "clean": "reference_unmitigated.joblib",
    "poisoned": "low_quality_unmitigated.joblib",
    "safeguarded": "low_quality_mitigated.joblib",
}
_LIVE_SCENARIO_LABELS = {
    "clean": "Trusted pipeline",
    "poisoned": "Full degraded pipeline",
    "safeguarded": "Degraded pipeline + safeguard",
}
# These are deliberately disclosed teaching examples, not a random sample.  The
# first three show distinct failure/safeguard behaviors; the last is a
# counterexample so the live walkthrough cannot imply that every row gets worse.
_LIVE_DEMO_CASES = (
    ("482116", "Delivery A · recommended walkthrough", "clear harm with partial recovery"),
    ("3962272", "Delivery B · second affected case", "large harm with partial recovery"),
    ("2747792", "Delivery C · safeguard stress test", "harm that the safeguard does not recover"),
    ("2754711", "Delivery D · counterexample", "an affected row that improves"),
)
_LIVE_SIGNALS = (
    ("distance_km", "Distance signal", "km", 1.0),
    ("area_historical_eta", "Area history signal", "minutes", 1.0),
    ("source_reliability", "Source reliability", "%", 100.0),
    ("low_quality_share", "Unreliable-feed exposure", "%", 100.0),
    ("data_age_days", "Age of area history", "days", 1.0),
)


@lru_cache(maxsize=1)
def _live_demo_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Recreate the fixed clean and noisy held-out inputs used by the artifacts."""
    runtime_dir = PROJECT_DIR / "runtime"
    clean_path = runtime_dir / "live_clean.csv.gz"
    poisoned_path = runtime_dir / "live_poisoned.csv.gz"
    if clean_path.exists() and poisoned_path.exists():
        parse_dates = ["service_date", "accept_ts"]
        clean = pd.read_csv(clean_path, parse_dates=parse_dates)
        poisoned = pd.read_csv(poisoned_path, parse_dates=parse_dates)
        return clean, poisoned

    path = DATA_DIR / "delivery_jl.csv"
    if not path.exists():
        raise HTTPException(
            503,
            "The deployment bundle for measured LaDe live inference is unavailable.",
        )
    prepared = prepare_features(load_delivery_csv(path))
    train, validation, clean_test = chronological_split(
        prepared,
        DEFAULT_CONFIG.train_fraction,
        DEFAULT_CONFIG.validation_fraction,
    )
    _, _, poisoned_test = apply_condition(
        train,
        validation,
        clean_test,
        "low_quality",
        DEFAULT_CONFIG.noisy_feed_share,
        DEFAULT_CONFIG.seeds[0],
    )
    return clean_test.reset_index(drop=True), poisoned_test.reset_index(drop=True)


@lru_cache(maxsize=3)
def _load_live_model(scenario: str):
    filename = _LIVE_ARTIFACTS.get(scenario)
    if filename is None:
        raise HTTPException(422, "Unknown live-demo scenario.")
    path = ARTIFACTS_DIR / filename
    if not path.exists():
        raise HTTPException(503, f"Missing fitted model artifact: {filename}")
    try:
        fitted = joblib.load(path)
    except Exception as exc:  # pragma: no cover - protects the public API from corrupt artifacts
        raise HTTPException(503, f"Could not load fitted model artifact: {filename}") from exc
    if not all(hasattr(fitted, name) for name in ("predict", "intervals_and_flags", "pipeline")):
        raise HTTPException(503, f"Invalid fitted model artifact: {filename}")
    return fitted


@lru_cache(maxsize=1)
def _live_demo_case_indices() -> tuple[int, ...]:
    """Resolve the four disclosed teaching examples in their presentation order."""
    clean, poisoned = _live_demo_frames()
    package_ids = clean["package_id"].astype(str)
    indices: list[int] = []
    for package_id, _, _ in _LIVE_DEMO_CASES:
        matches = np.flatnonzero(package_ids.to_numpy() == package_id)
        if len(matches) != 1:
            raise HTTPException(503, f"Live-demo delivery {package_id} is unavailable.")
        index = int(matches[0])
        if np.isclose(
            float(clean.iloc[index]["source_reliability"]),
            float(poisoned.iloc[index]["source_reliability"]),
        ):
            raise HTTPException(503, f"Live-demo delivery {package_id} was not affected by the scenario.")
        indices.append(index)
    return tuple(indices)


def _live_demo_case_index(package_id: str) -> int:
    clean, _ = _live_demo_frames()
    for index in _live_demo_case_indices():
        if str(clean.iloc[index]["package_id"]) == package_id:
            return index
    raise HTTPException(404, "Choose one of the published live-demo deliveries.")


def _rounded(value: object, digits: int = 1) -> float:
    return round(float(value), digits)


def _live_predictions(index: int) -> tuple[dict[str, dict[str, object]], dict[str, pd.DataFrame]]:
    """Call each fitted model at request time against the fixed evaluation batch."""
    clean, poisoned = _live_demo_frames()
    frames = {"clean": clean, "poisoned": poisoned, "safeguarded": poisoned}
    outputs: dict[str, dict[str, object]] = {}
    effective_frames: dict[str, pd.DataFrame] = {}
    for scenario, frame in frames.items():
        fitted = _load_live_model(scenario)
        predictions = fitted.predict(frame)
        half_widths, flagged = fitted.intervals_and_flags(frame)
        effective_frames[scenario] = fitted._model_frame(frame)  # display the signals used
        prediction = float(predictions[index])
        half_width = float(half_widths[index])
        outputs[scenario] = {
            "prediction_minutes": round(prediction, 1),
            "interval": {
                "low_minutes": round(max(0.0, prediction - half_width), 1),
                "high_minutes": round(prediction + half_width, 1),
            },
            "review": {
                "flagged": bool(flagged[index]),
                "reason": review_reason(
                    frame.iloc[index], stale_after_days=DEFAULT_CONFIG.stale_after_days
                ),
            },
        }
    return outputs, effective_frames


def _uploaded_prediction_frames(
    request: UploadedPredictionRequest,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build trusted and transparently degraded inputs from prepared uploaded rows."""
    records = [row.model_dump() for row in request.rows]
    clean = pd.DataFrame(records)
    clean["package_id"] = clean["package_id"].astype(str)
    clean["hour_sin"] = np.sin(2 * np.pi * clean["accept_hour"] / 24)
    clean["hour_cos"] = np.cos(2 * np.pi * clean["accept_hour"] / 24)
    clean["data_age_days"] = 0.0
    clean["source_reliability"] = 1.0
    clean["low_quality_share"] = 0.0
    clean["source_name"] = "uploaded_prepared_row"

    degraded = clean.copy()
    # This deterministic upload-only profile mirrors the measured low-quality
    # intervention without pretending that a newly uploaded row belonged to the
    # original seeded evaluation batch.
    degraded["distance_km"] = (degraded["distance_km"] * 1.42).clip(0, 100)
    degraded["area_historical_eta"] = (
        degraded["area_historical_eta"] + 10.0
    ).clip(0, 360)
    degraded["source_reliability"] = 0.25
    degraded["low_quality_share"] = DEFAULT_CONFIG.noisy_feed_share
    degraded["source_name"] = "controlled_uploaded_unreliable_feed"
    return clean, degraded


@app.get("/", include_in_schema=False)
def website() -> FileResponse:
    return FileResponse(ROOT_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, object]:
    required = ["summary.csv", "timeline.csv", "predictions.csv", "runs.csv"]
    return {
        "status": "ok" if all((RESULTS_DIR / item).exists() for item in required) else "degraded",
        "service": "lade-eta-reliability-lab",
        "version": app.version,
        "utc_time": datetime.now(timezone.utc).isoformat(),
        "results_ready": all((RESULTS_DIR / item).exists() for item in required),
    }


@app.get("/api/overview")
def overview() -> dict[str, object]:
    summary = _read_csv("summary.csv")
    mode = str(summary["data_mode"].iloc[0])
    runs = _read_csv("runs.csv")
    return {
        "title": "When More Data Hurts",
        "subtitle": "A delivery ETA case study in accidental data decay",
        "data_mode": mode,
        "evidence_label": "Measured LaDe experiment" if mode == "LaDe" else "Synthetic preview",
        "profile": _data_profile(),
        "finding": _finding_payload(),
        "experiment": {
            "conditions": list(CONDITIONS),
            "mitigation_states": [False, True],
            "runs": int(len(runs)),
            "seeds": sorted(runs["seed"].unique().astype(int).tolist()),
            "fixed_evaluation_cases": True,
        },
        "requirements": [
            {"name": "Decision defined", "status": "complete", "where": "Overview"},
            {"name": "Repeatable reference", "status": "complete", "where": "Experiment Lab"},
            {"name": "Stale and low-quality isolated", "status": "complete", "where": "Experiment Lab"},
            {"name": "Effect shown over time", "status": "complete", "where": "Timeline"},
            {"name": "Mechanism traced", "status": "complete", "where": "Evidence chain"},
            {"name": "Countermeasure compared", "status": "complete", "where": "Eight-way comparison"},
            {"name": "Limits and next step", "status": "complete", "where": "Governance"},
        ],
    }


@app.get("/api/comparison")
def comparison() -> dict[str, object]:
    summary = _read_csv("summary.csv").sort_values(["condition", "mitigated"])
    return {"records": _json_records(summary), "finding": _finding_payload()}


@app.get("/api/timeline")
def timeline(
    condition: Literal["reference", "stale", "low_quality", "combined"] = "combined",
    mitigated: bool = False,
) -> dict[str, object]:
    frame = _read_csv("timeline.csv")
    selected = frame[(frame["condition"] == condition) & (frame["mitigated"] == mitigated)]
    daily = (
        selected.groupby("service_date", as_index=False)
        .agg(
            mae_minutes=("mae_minutes", "mean"),
            mae_std=("mae_minutes", "std"),
            data_age_days=("data_age_days", "mean"),
            low_quality_share=("low_quality_share", "mean"),
            source_reliability=("source_reliability", "mean"),
            deferred_rate=("deferred_rate", "mean"),
            n_test=("n_test", "mean"),
        )
        .sort_values("service_date")
    )
    return {"condition": condition, "mitigated": mitigated, "records": _json_records(daily)}


@app.get("/api/cases")
def cases(
    condition: Literal["reference", "stale", "low_quality", "combined"] = "combined",
    mitigated: bool = True,
    flagged_only: bool = True,
    limit: int = Query(default=80, ge=1, le=300),
) -> dict[str, object]:
    frame = _read_csv("predictions.csv")
    frame["flagged_for_review"] = frame["flagged_for_review"].astype(str).str.lower().eq("true")
    selected = frame[(frame["condition"] == condition) & (frame["mitigated"] == mitigated)].copy()
    if flagged_only and selected["flagged_for_review"].any():
        selected = selected[selected["flagged_for_review"]]
    selected["package_id"] = selected["package_id"].astype(str)
    selected["absolute_error"] = (selected["duration_minutes"] - selected["prediction_minutes"]).abs()
    selected = selected.sort_values(["flagged_for_review", "absolute_error"], ascending=False).head(limit)
    columns = [
        "package_id", "service_date", "city", "region_id", "duration_minutes",
        "prediction_minutes", "interval_low", "interval_high", "flagged_for_review",
        "review_reason", "data_age_days", "low_quality_share", "source_reliability",
        "condition", "mitigated", "absolute_error", "data_mode",
    ]
    return {"total": int(len(selected)), "records": _json_records(selected[columns])}


@app.get("/api/live-demo/cases")
def live_demo_cases() -> dict[str, object]:
    clean, _ = _live_demo_frames()
    records: list[dict[str, object]] = []
    for index, (package_id, label, teaching_role) in zip(
        _live_demo_case_indices(), _LIVE_DEMO_CASES, strict=True
    ):
        row = clean.iloc[index]
        records.append(
            {
                "package_id": package_id,
                "label": label,
                "teaching_role": teaching_role,
                "service_date": pd.Timestamp(row["service_date"]).date().isoformat(),
                "city": str(row["city"]),
                "region_id": str(row["region_id"]),
                "accepted_at": pd.Timestamp(row["accept_ts"]).isoformat(),
                "distance_km_before_poisoning": _rounded(row["distance_km"]),
            }
        )
    return {
        "cases": records,
        "data": {
            "dataset": "LaDe delivery data",
            "split": "fixed chronological held-out evaluation set",
            "synthetic": False,
            "same_delivery_across_scenarios": True,
        },
        "selection_note": (
            "Four held-out teaching examples were intentionally selected: clear harm, different safeguard "
            "outcomes, and one counterexample. Use the full 3,407-case experiment—not these examples—for "
            "the average-effect claim."
        ),
    }


@app.post("/api/live-demo/predict")
def live_demo_predict(request: LivePredictionRequest) -> dict[str, object]:
    index = _live_demo_case_index(request.package_id)
    clean, poisoned = _live_demo_frames()
    outputs, effective_frames = _live_predictions(index)
    clean_prediction = float(outputs["clean"]["prediction_minutes"])
    poisoned_prediction = float(outputs["poisoned"]["prediction_minutes"])
    safeguarded_prediction = float(outputs["safeguarded"]["prediction_minutes"])
    selected = outputs[request.scenario]
    selected_prediction = float(selected["prediction_minutes"])

    clean_row = clean.iloc[index]
    poisoned_row = poisoned.iloc[index]
    effective_row = effective_frames[request.scenario].iloc[index]
    signals: list[dict[str, object]] = []
    for key, label, unit, scale in _LIVE_SIGNALS:
        clean_value = float(clean_row[key]) * scale
        poisoned_value = float(poisoned_row[key]) * scale
        value_used = float(effective_row[key]) * scale
        signals.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "clean_value": round(clean_value, 1),
                "poisoned_value": round(poisoned_value, 1),
                "value_used_by_selected_scenario": round(value_used, 1),
                "changed_by_poisoning": not bool(np.isclose(clean_value, poisoned_value)),
                "adjusted_by_safeguard": request.scenario == "safeguarded"
                and not bool(np.isclose(poisoned_value, value_used)),
            }
        )

    fitted = _load_live_model(request.scenario)
    estimator = fitted.pipeline.named_steps.get("model")
    changed_labels = [signal["label"] for signal in signals if signal["changed_by_poisoning"]]
    return {
        "package_id": request.package_id,
        "scenario": request.scenario,
        "scenario_label": _LIVE_SCENARIO_LABELS[request.scenario],
        "prediction_minutes": selected_prediction,
        "interval": selected["interval"],
        "review": selected["review"],
        "comparison": {
            "clean_prediction_minutes": clean_prediction,
            "poisoned_prediction_minutes": poisoned_prediction,
            "safeguarded_prediction_minutes": safeguarded_prediction,
            "poisoning_shift_minutes": round(poisoned_prediction - clean_prediction, 1),
            "safeguard_shift_from_poisoned_minutes": round(
                safeguarded_prediction - poisoned_prediction, 1
            ),
            "safeguard_recovery_toward_clean_minutes": round(
                abs(poisoned_prediction - clean_prediction)
                - abs(safeguarded_prediction - clean_prediction),
                1,
            ),
        },
        "input_signals": signals,
        "inputs_changed_by_poisoning": changed_labels,
        "intervention": {
            "training_data": {
                "changed_for_selected_scenario": request.scenario != "clean",
                "controlled_unreliable_share_percent": (
                    round(DEFAULT_CONFIG.noisy_feed_share * 100, 1)
                    if request.scenario != "clean"
                    else 0.0
                ),
                "description": (
                    "The fitted model saw controlled duplicated rows with corrupted targets, distance, and area-history values."
                    if request.scenario != "clean"
                    else "The fitted model used the trusted reference training evidence."
                ),
            },
            "inference_inputs": {
                "changed_for_selected_scenario": request.scenario != "clean",
                "changed_signals_for_selected_scenario": (
                    changed_labels if request.scenario != "clean" else []
                ),
                "description": (
                    "This held-out delivery receives controlled noisy distance and area-history signals plus low source-reliability metadata."
                    if request.scenario != "clean"
                    else "This held-out delivery receives its trusted reference signals."
                ),
            },
        },
        "model": {
            "algorithm": type(estimator).__name__ if estimator is not None else "Unknown",
            "artifact": _LIVE_ARTIFACTS[request.scenario],
            "artifact_fitted_before_request": True,
            "prediction_computed_live_on_request": True,
            "training_condition": (
                "trusted reference evidence"
                if request.scenario == "clean"
                else "55% controlled unreliable-feed share"
            ),
            "inference_condition": (
                "trusted held-out inputs"
                if request.scenario == "clean"
                else "corresponding controlled degraded held-out inputs"
            ),
        },
        "evidence": {
            "dataset": "LaDe delivery data",
            "synthetic": False,
            "evaluation_split": "fixed chronological held-out set",
            "controlled_seed": int(DEFAULT_CONFIG.seeds[0]),
            "same_delivery_across_scenarios": True,
            "scoring_context": (
                "The fitted model re-scores the full fixed evaluation batch so safeguard preprocessing uses "
                "the same stable batch context as the measured experiment; only this delivery is returned."
            ),
        },
        "caveats": [
            "Inference is recalculated over the fixed held-out batch when this endpoint is called, but the model artifacts were fitted in advance so the demo is fast and repeatable.",
            "The actual delivery duration stays hidden here; a separate retrospective reveal endpoint returns it after the comparison, and it is never supplied to the model as an input.",
            "The noisy-feed intervention is a controlled simulation of accidental data-quality failure, not evidence that the source dataset was maliciously attacked.",
            "This delivery is illustrative; deployment conclusions must use the aggregate experiment rather than a single example.",
        ],
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/live-demo/upload-predict")
def live_demo_upload_predict(request: UploadedPredictionRequest) -> dict[str, object]:
    """Score prepared CSV rows through all three fitted live-demo pipelines."""
    clean, degraded = _uploaded_prediction_frames(request)
    source_frames = {
        "clean": clean,
        "poisoned": degraded,
        "safeguarded": degraded,
    }
    scenario_outputs: dict[str, dict[str, object]] = {}
    for scenario, frame in source_frames.items():
        fitted = _load_live_model(scenario)
        predictions = fitted.predict(frame)
        half_widths, flagged = fitted.intervals_and_flags(frame)
        scenario_outputs[scenario] = {
            "predictions": predictions,
            "half_widths": half_widths,
            "flagged": flagged,
        }

    results: list[dict[str, object]] = []
    errors: dict[str, list[float]] = {scenario: [] for scenario in _LIVE_ARTIFACTS}
    for index, row in clean.iterrows():
        actual = row.get("duration_minutes")
        actual_value = None if pd.isna(actual) else float(actual)
        predictions: dict[str, object] = {}
        for scenario in _LIVE_ARTIFACTS:
            output = scenario_outputs[scenario]
            prediction = float(output["predictions"][index])
            half_width = float(output["half_widths"][index])
            absolute_error = (
                None if actual_value is None else abs(actual_value - prediction)
            )
            if absolute_error is not None:
                errors[scenario].append(absolute_error)
            scenario_frame = source_frames[scenario]
            predictions[scenario] = {
                "prediction_minutes": round(prediction, 1),
                "interval": {
                    "low_minutes": round(max(0.0, prediction - half_width), 1),
                    "high_minutes": round(prediction + half_width, 1),
                },
                "absolute_error_minutes": (
                    None if absolute_error is None else round(absolute_error, 1)
                ),
                "review": {
                    "flagged": bool(output["flagged"][index]),
                    "reason": review_reason(
                        scenario_frame.iloc[index],
                        stale_after_days=DEFAULT_CONFIG.stale_after_days,
                    ),
                },
            }
        results.append(
            {
                "package_id": str(row["package_id"]),
                "city": row.get("city"),
                "region_id": row.get("region_id"),
                "actual_duration_minutes_demo_only": actual_value,
                "predictions": predictions,
            }
        )

    rows_with_outcomes = sum(
        result["actual_duration_minutes_demo_only"] is not None for result in results
    )
    return {
        "results": results,
        "summary": {
            "rows_scored": len(results),
            "rows_with_outcomes": rows_with_outcomes,
            "mae_minutes": {
                scenario: (
                    round(float(np.mean(values)), 1) if values else None
                )
                for scenario, values in errors.items()
            },
            "safeguarded_review_rate": round(
                float(np.mean(scenario_outputs["safeguarded"]["flagged"])), 4
            ),
        },
        "method": {
            "input_format": "prepared acceptance-time feature rows",
            "models": _LIVE_ARTIFACTS,
            "trusted_input": "uploaded feature values with trusted-source metadata",
            "degraded_input": {
                "distance_multiplier": 1.42,
                "area_history_added_minutes": 10.0,
                "source_reliability": 0.25,
                "low_quality_share": DEFAULT_CONFIG.noisy_feed_share,
            },
            "outcome_used_as_model_input": False,
            "note": (
                "The upload comparison uses the same three fitted artifacts. A transparent, "
                "deterministic low-quality profile is applied to uploaded rows because they were "
                "not members of the original seeded evaluation batch."
            ),
        },
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/live-demo/outcome/{package_id}")
def live_demo_outcome(package_id: str) -> dict[str, object]:
    """Reveal the retrospective label only after the UI has shown predictions."""
    index = _live_demo_case_index(package_id)
    clean, _ = _live_demo_frames()
    outputs, _ = _live_predictions(index)
    actual = float(clean.iloc[index]["duration_minutes"])
    errors = {
        scenario: round(abs(actual - float(output["prediction_minutes"])), 1)
        for scenario, output in outputs.items()
    }
    return {
        "package_id": package_id,
        "actual_duration_minutes_demo_only": round(actual, 1),
        "absolute_error_minutes": errors,
        "caveat": (
            "This actual duration is a held-out evaluation label revealed only for retrospective comparison. "
            "It was never available to any model during prediction."
        ),
    }


@app.post("/api/decisions", status_code=201)
def create_decision(request: DecisionRequest) -> dict[str, object]:
    cases_frame = _read_csv("predictions.csv")
    match = cases_frame[
        (cases_frame["package_id"].astype(str) == request.package_id)
        & (cases_frame["condition"] == request.condition)
        & (cases_frame["mitigated"] == request.mitigated)
    ]
    if match.empty:
        raise HTTPException(404, "The selected case was not found in the fixed evaluation set.")
    row = match.iloc[0]
    entry = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "package_id": request.package_id,
        "condition": request.condition,
        "mitigated": request.mitigated,
        "model_eta_minutes": round(float(row["prediction_minutes"]), 3),
        "actual_minutes_demo_only": round(float(row["duration_minutes"]), 3),
        "action": request.action,
        "override_minutes": request.override_minutes,
        "reviewer": request.reviewer,
        "review_seconds": request.review_seconds,
        "note": request.note,
    }
    with _decision_lock:
        output = pd.DataFrame([entry])
        output.to_csv(OVERRIDES_PATH, mode="a", header=not OVERRIDES_PATH.exists(), index=False)
    return {"saved": True, "decision": entry}


@app.get("/api/decisions")
def decisions(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, object]:
    if not OVERRIDES_PATH.exists():
        return {"records": []}
    frame = pd.read_csv(OVERRIDES_PATH).tail(limit).iloc[::-1]
    return {"records": _json_records(frame)}


@app.post("/api/experiments/preview")
def preview_experiment(request: PreviewRequest) -> dict[str, object]:
    raw = make_synthetic_delivery_data(request.rows, request.seed)
    config = ExperimentConfig(seeds=(request.seed,), max_rows=request.rows)
    summary, _, _ = run_suite(raw, config, save_outputs=False)
    return {
        "label": "Synthetic sensitivity preview — not competition evidence",
        "records": _json_records(summary),
    }


@app.get("/api/docs")
def docs_index() -> dict[str, object]:
    labels = {
        "setup": "Setup and reproducibility",
        "deployment": "Online deployment",
        "method": "Experiment card",
        "data": "Data manifest",
        "governance": "Governance",
        "ai": "AI use disclosure",
        "architecture": "Architecture",
        "demo": "Demo and recording script",
        "api": "API reference",
    }
    return {"documents": [{"id": key, "label": labels[key]} for key in DOCS]}


@app.get("/api/docs/{document_id}")
def document(document_id: str) -> dict[str, object]:
    path = DOCS.get(document_id)
    if path is None or not path.exists():
        raise HTTPException(404, "Documentation page not found")
    return {"id": document_id, "markdown": path.read_text(encoding="utf-8")}


@app.get("/api/downloads/{filename}")
def download(filename: str) -> FileResponse:
    path = DOWNLOADS.get(filename)
    if path is None or not path.exists():
        raise HTTPException(404, "Download not found")
    return FileResponse(path, filename=path.name)
