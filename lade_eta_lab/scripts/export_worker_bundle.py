"""Export the measured API evidence and fitted ETA trees for Cloudflare Workers.

The Worker runtime cannot import Python or joblib on the free plan. This script
converts the fitted scikit-learn HistGradientBoostingRegressor trees to compact
JSON-compatible arrays and snapshots the deterministic evidence endpoints.
Cloudflare still evaluates every tree for every live-demo request.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np


PROJECT_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = PROJECT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

import app as api  # noqa: E402
from features import MODEL_FEATURES  # noqa: E402


OUTPUT_PATH = ROOT_DIR / "cloudflare" / "generated-data.ts"
SCENARIOS = ("clean", "poisoned", "safeguarded")


def clean_json(value: Any) -> Any:
    """Convert numpy/pandas values and non-finite floats to strict JSON."""
    if isinstance(value, dict):
        return {str(key): clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def model_payload(scenario: str) -> dict[str, Any]:
    fitted = api._load_live_model(scenario)
    pipeline = fitted.pipeline
    imputer = pipeline.named_steps["impute"]
    estimator = pipeline.named_steps["model"]
    trees: list[list[list[Any]]] = []
    for iteration in estimator._predictors:
        nodes = iteration[0].nodes
        trees.append(
            [
                [
                    int(node["feature_idx"]),
                    float(node["num_threshold"]),
                    int(node["missing_go_to_left"]),
                    int(node["left"]),
                    int(node["right"]),
                    float(node["value"]),
                    int(node["is_leaf"]),
                ]
                for node in nodes
            ]
        )
    return {
        "baseline": float(estimator._baseline_prediction.ravel()[0]),
        "imputer": [float(value) for value in imputer.statistics_],
        "residualQ90": float(fitted.residual_q90),
        "globalBaseline": float(fitted.global_baseline),
        "staleAfterDays": int(fitted.stale_after_days),
        "trees": trees,
    }


def evaluate_exported_model(model: dict[str, Any], vector: list[float | None]) -> float:
    """Mirror the Worker tree traversal so a bad export fails before deployment."""
    values = [
        model["imputer"][index] if value is None or not math.isfinite(value) else value
        for index, value in enumerate(vector)
    ]
    prediction = model["baseline"]
    for tree in model["trees"]:
        node_index = 0
        while True:
            feature, threshold, missing_left, left, right, leaf_value, is_leaf = tree[node_index]
            if is_leaf:
                prediction += leaf_value
                break
            value = values[feature]
            node_index = left if (not math.isfinite(value) and missing_left) or value <= threshold else right
    return prediction


def live_payload(models: dict[str, Any]) -> dict[str, Any]:
    clean, poisoned = api._live_demo_frames()
    source_frames = {"clean": clean, "poisoned": poisoned, "safeguarded": poisoned}
    fitted_models = {scenario: api._load_live_model(scenario) for scenario in SCENARIOS}
    effective_frames = {
        scenario: fitted_models[scenario]._model_frame(source_frames[scenario])
        for scenario in SCENARIOS
    }
    records: dict[str, Any] = {}
    for index, (package_id, _, _) in zip(
        api._live_demo_case_indices(), api._LIVE_DEMO_CASES, strict=True
    ):
        templates: dict[str, Any] = {}
        vectors: dict[str, Any] = {}
        for scenario in SCENARIOS:
            response = api.live_demo_predict(
                api.LivePredictionRequest(package_id=package_id, scenario=scenario)
            )
            response.pop("computed_at_utc", None)
            response["evidence"]["scoring_context"] = (
                "The request evaluates the exported fitted trees against this fixed held-out "
                "delivery. Safeguard preprocessing uses the same fixed evaluation-batch median "
                "as the measured experiment."
            )
            response["caveats"][0] = (
                "The fitted model trees are evaluated when this endpoint is called. Model fitting "
                "and the fixed-batch safeguard context were prepared in advance for a fast, repeatable demo."
            )
            templates[scenario] = response
            vectors[scenario] = [
                None
                if np.isnan(float(effective_frames[scenario].iloc[index][name]))
                else float(effective_frames[scenario].iloc[index][name])
                for name in MODEL_FEATURES
            ]
            exported_prediction = evaluate_exported_model(models[scenario], vectors[scenario])
            if not np.isclose(
                round(exported_prediction, 1),
                float(response["prediction_minutes"]),
                atol=0.05,
            ):
                raise RuntimeError(
                    f"Exported {scenario} model changed prediction for package {package_id}: "
                    f"{exported_prediction} != {response['prediction_minutes']}"
                )
        records[package_id] = {
            "templates": templates,
            "vectors": vectors,
            "outcome": api.live_demo_outcome(package_id),
        }
    return {
        "cases": api.live_demo_cases(),
        "records": records,
        "models": models,
    }


def evidence_payload() -> dict[str, Any]:
    timelines: dict[str, Any] = {}
    case_sets: dict[str, Any] = {}
    for condition in api.CONDITIONS:
        for mitigated in (False, True):
            key = f"{condition}:{str(mitigated).lower()}"
            timelines[key] = api.timeline(condition=condition, mitigated=mitigated)
            case_sets[key] = api.cases(
                condition=condition,
                mitigated=mitigated,
                flagged_only=False,
                limit=300,
            )

    docs = api.docs_index()
    doc_content = {
        item["id"]: api.document(item["id"])["markdown"]
        for item in docs["documents"]
    }
    return {
        "overview": api.overview(),
        "comparison": api.comparison(),
        "timelines": timelines,
        "cases": case_sets,
        "docs": docs,
        "docContent": doc_content,
    }


def main() -> None:
    models = {scenario: model_payload(scenario) for scenario in SCENARIOS}
    payload = clean_json({"evidence": evidence_payload(), "live": live_payload(models)})
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    OUTPUT_PATH.write_text(
        "// Generated by lade_eta_lab/scripts/export_worker_bundle.py.\n"
        "// Do not hand-edit; rerun the exporter after changing models or evidence.\n"
        f"export const EDGE_DATA: any = {encoded};\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT_DIR)} ({OUTPUT_PATH.stat().st_size:,} bytes).")


if __name__ == "__main__":
    main()
