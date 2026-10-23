"""Build the redistributable held-out inference inputs used by the online demo.

The source LaDe CSV stays local. Only the fixed evaluation features needed to
reproduce the already-published live predictions are written to runtime/.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from config import DATA_DIR, DEFAULT_CONFIG  # noqa: E402
from data import load_delivery_csv  # noqa: E402
from features import MODEL_FEATURES, chronological_split, prepare_features  # noqa: E402
from scenarios import apply_condition  # noqa: E402


RUNTIME_COLUMNS = [
    "package_id",
    "service_date",
    "accept_ts",
    "city",
    "region_id",
    "duration_minutes",
    *MODEL_FEATURES,
    "data_age_days",
    "source_reliability",
    "low_quality_share",
    "source_name",
    "data_mode",
]


def main() -> None:
    source = DATA_DIR / "delivery_jl.csv"
    if not source.exists():
        raise SystemExit(f"Missing local source dataset: {source}")

    prepared = prepare_features(load_delivery_csv(source))
    train, validation, clean = chronological_split(
        prepared,
        DEFAULT_CONFIG.train_fraction,
        DEFAULT_CONFIG.validation_fraction,
    )
    _, _, poisoned = apply_condition(
        train,
        validation,
        clean,
        "low_quality",
        DEFAULT_CONFIG.noisy_feed_share,
        DEFAULT_CONFIG.seeds[0],
    )

    runtime_dir = PROJECT_DIR / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    clean[RUNTIME_COLUMNS].to_csv(
        runtime_dir / "live_clean.csv.gz", index=False, compression="gzip"
    )
    poisoned[RUNTIME_COLUMNS].to_csv(
        runtime_dir / "live_poisoned.csv.gz", index=False, compression="gzip"
    )
    print(f"Wrote {len(clean):,} clean and {len(poisoned):,} poisoned evaluation rows.")


if __name__ == "__main__":
    main()
