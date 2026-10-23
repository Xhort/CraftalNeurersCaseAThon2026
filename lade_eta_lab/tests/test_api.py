from __future__ import annotations

from fastapi.testclient import TestClient

import app as webapp


client = TestClient(webapp.app)


def test_health_and_overview_use_measured_results():
    website = client.get("/")
    assert website.status_code == 200
    assert './lade_eta_lab/static/styles.css' in website.text
    assert "More data made delivery ETAs" in website.text
    assert "Do not deploy yet" in website.text
    assert 'id="roadmap-frame"' in website.text
    assert 'id="evidence-files"' in website.text
    assert 'id="docs"' not in website.text
    assert "mechanism-section" not in website.text
    assert 'id="deck-modal"' not in website.text
    assert client.get("/lade_eta_lab/static/app.js").status_code == 200

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["results_ready"] is True

    overview = client.get("/api/overview")
    assert overview.status_code == 200
    payload = overview.json()
    assert payload["data_mode"] == "LaDe"
    assert payload["profile"]["source_rows"] == 31_415
    assert payload["experiment"]["runs"] == 24
    assert len(payload["requirements"]) == 7


def test_comparison_and_timeline_cover_the_experiment():
    comparison = client.get("/api/comparison").json()
    assert len(comparison["records"]) == 8
    assert comparison["finding"]["low_quality_delta"] > 0

    timeline = client.get("/api/timeline", params={"condition": "combined", "mitigated": "false"})
    assert timeline.status_code == 200
    assert len(timeline.json()["records"]) >= 30


def test_live_demo_runs_real_models_for_the_same_delivery(monkeypatch):
    case_response = client.get("/api/live-demo/cases")
    assert case_response.status_code == 200
    case_payload = case_response.json()
    assert len(case_payload["cases"]) == 4
    assert case_payload["data"]["synthetic"] is False
    assert [case["package_id"] for case in case_payload["cases"]] == [
        "482116", "3962272", "2747792", "2754711"
    ]
    assert "counterexample" in case_payload["selection_note"]
    package_id = case_payload["cases"][0]["package_id"]
    assert "actual_duration_minutes_demo_only" not in case_payload["cases"][0]

    real_models = {
        scenario: webapp._load_live_model(scenario)
        for scenario in ("clean", "poisoned", "safeguarded")
    }

    class PredictSpy:
        def __init__(self, fitted):
            self.fitted = fitted
            self.predict_calls = 0

        def predict(self, frame):
            self.predict_calls += 1
            return self.fitted.predict(frame)

        def __getattr__(self, name):
            return getattr(self.fitted, name)

    spies = {scenario: PredictSpy(fitted) for scenario, fitted in real_models.items()}
    monkeypatch.setattr(webapp, "_load_live_model", lambda scenario: spies[scenario])

    responses = {}
    for scenario in ("clean", "poisoned", "safeguarded"):
        response = client.post(
            "/api/live-demo/predict",
            json={"package_id": package_id, "scenario": scenario},
        )
        assert response.status_code == 200
        responses[scenario] = response.json()
        assert "actual_duration_minutes_demo_only" not in response.json()
        assert "absolute_error_minutes_demo_only" not in response.json()

    assert all(spy.predict_calls >= 3 for spy in spies.values())
    clean = responses["clean"]
    poisoned = responses["poisoned"]
    safeguarded = responses["safeguarded"]
    assert clean["package_id"] == poisoned["package_id"] == safeguarded["package_id"]
    assert clean["prediction_minutes"] != poisoned["prediction_minutes"]
    assert poisoned["comparison"]["poisoning_shift_minutes"] != 0
    assert poisoned["inputs_changed_by_poisoning"]
    assert poisoned["model"]["algorithm"] == "HistGradientBoostingRegressor"
    assert poisoned["model"]["prediction_computed_live_on_request"] is True
    assert poisoned["model"]["artifact_fitted_before_request"] is True
    assert poisoned["model"]["inference_condition"].startswith("corresponding controlled")
    assert poisoned["evidence"]["synthetic"] is False
    assert clean["intervention"]["training_data"]["changed_for_selected_scenario"] is False
    assert poisoned["intervention"]["training_data"]["changed_for_selected_scenario"] is True
    assert poisoned["intervention"]["inference_inputs"]["changed_for_selected_scenario"] is True
    assert "duration_minutes" not in {signal["key"] for signal in poisoned["input_signals"]}
    assert len(poisoned["caveats"]) == 4
    assert safeguarded["scenario_label"] == "Degraded pipeline + safeguard"

    expected = webapp._read_csv("predictions.csv")
    expected["package_id"] = expected["package_id"].astype(str)
    scenario_rows = {
        "clean": expected[
            (expected["package_id"] == package_id)
            & (expected["condition"] == "reference")
            & (~expected["mitigated"])
        ].iloc[0],
        "poisoned": expected[
            (expected["package_id"] == package_id)
            & (expected["condition"] == "low_quality")
            & (~expected["mitigated"])
        ].iloc[0],
        "safeguarded": expected[
            (expected["package_id"] == package_id)
            & (expected["condition"] == "low_quality")
            & (expected["mitigated"])
        ].iloc[0],
    }
    for scenario, row in scenario_rows.items():
        assert responses[scenario]["prediction_minutes"] == round(float(row["prediction_minutes"]), 1)

    outcome_response = client.get(f"/api/live-demo/outcome/{package_id}")
    assert outcome_response.status_code == 200
    outcome = outcome_response.json()
    assert outcome["actual_duration_minutes_demo_only"] > 0
    assert set(outcome["absolute_error_minutes"]) == {"clean", "poisoned", "safeguarded"}
    assert "never available" in outcome["caveat"]

    missing = client.post(
        "/api/live-demo/predict",
        json={"package_id": "not-a-published-case", "scenario": "clean"},
    )
    assert missing.status_code == 404

    invalid = client.post(
        "/api/live-demo/predict",
        json={"package_id": package_id, "scenario": "invented"},
    )
    assert invalid.status_code == 422


def test_uploaded_prepared_rows_run_through_all_three_pipelines():
    clean, _ = webapp._live_demo_frames()
    source = clean.head(2)
    rows = []
    for _, row in source.iterrows():
        rows.append(
            {
                "package_id": str(row["package_id"]),
                "city": row["city"],
                "region_id": str(row["region_id"]),
                "region_code": row["region_code"],
                "aoi_type_code": row["aoi_type_code"],
                "accept_hour": row["accept_hour"],
                "day_of_week": row["day_of_week"],
                "distance_km": row["distance_km"],
                "courier_recent_accepts": row["courier_recent_accepts"],
                "area_historical_eta": row["area_historical_eta"],
                "courier_historical_eta": row["courier_historical_eta"],
                "duration_minutes": row["duration_minutes"],
            }
        )

    response = client.post("/api/live-demo/upload-predict", json={"rows": rows})
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["rows_scored"] == 2
    assert payload["summary"]["rows_with_outcomes"] == 2
    assert set(payload["summary"]["mae_minutes"]) == {"clean", "poisoned", "safeguarded"}
    assert set(payload["results"][0]["predictions"]) == {"clean", "poisoned", "safeguarded"}
    assert payload["method"]["outcome_used_as_model_input"] is False
    assert payload["results"][0]["predictions"]["clean"]["absolute_error_minutes"] is not None

    missing = rows[0].copy()
    missing.pop("distance_km")
    invalid = client.post("/api/live-demo/upload-predict", json={"rows": [missing]})
    assert invalid.status_code == 422


def test_case_review_and_validated_decision_log(tmp_path, monkeypatch):
    temporary_log = tmp_path / "overrides.csv"
    monkeypatch.setattr(webapp, "OVERRIDES_PATH", temporary_log)
    cases = client.get("/api/cases", params={"condition": "combined", "mitigated": "true", "limit": 1})
    assert cases.status_code == 200
    selected = cases.json()["records"][0]

    saved = client.post(
        "/api/decisions",
        json={
            "package_id": selected["package_id"],
            "condition": "combined",
            "mitigated": True,
            "action": "override",
            "override_minutes": 75,
            "reviewer": "Test dispatcher",
            "review_seconds": 21,
            "note": "Verified with dispatcher context",
        },
    )
    assert saved.status_code == 201
    assert saved.json()["decision"]["action"] == "override"
    assert temporary_log.exists()

    invalid = client.post(
        "/api/decisions",
        json={
            "package_id": selected["package_id"],
            "condition": "combined",
            "mitigated": True,
            "action": "accept",
            "override_minutes": 75,
        },
    )
    assert invalid.status_code == 422


def test_documentation_and_download_allowlist():
    docs = client.get("/api/docs").json()["documents"]
    assert len(docs) >= 10
    assert client.get("/api/docs/method").status_code == 200
    assert client.get("/api/docs/deployment").status_code == 200
    assert client.get("/api/docs/roadmap").status_code == 200
    assert client.get("/api/downloads/data_manifest.json").status_code == 200
    assert client.get("/roadmap-section.html").status_code == 200
    assert client.get("/roadmap-data.js").status_code == 200
    assert client.get("/user-report.html").status_code == 200
    assert client.get("/api/downloads/render_videos.py").status_code == 200
    assert client.get("/lade_eta_lab/demo/videos/impact_roadmap.mp4").status_code == 200
    assert client.get("/lade_eta_lab/demo/videos/scenario_combined.mp4").status_code == 200
    assert client.get("/api/downloads/../../data/raw/delivery_jl.csv").status_code == 404


def test_online_bundle_works_without_the_raw_source(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "DATA_DIR", tmp_path)
    webapp._data_profile.cache_clear()
    webapp._live_demo_frames.cache_clear()

    overview = client.get("/api/overview")
    assert overview.status_code == 200
    assert overview.json()["profile"]["source_rows"] == 31_415
    assert overview.json()["profile"]["profile_source"] == "published experiment manifest"

    cases = client.get("/api/live-demo/cases")
    assert cases.status_code == 200
    prediction = client.post(
        "/api/live-demo/predict",
        json={"package_id": cases.json()["cases"][0]["package_id"], "scenario": "poisoned"},
    )
    assert prediction.status_code == 200
    assert prediction.json()["model"]["prediction_computed_live_on_request"] is True

    webapp._data_profile.cache_clear()
    webapp._live_demo_frames.cache_clear()
