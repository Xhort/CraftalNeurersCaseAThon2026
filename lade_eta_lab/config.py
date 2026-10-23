"""Shared experiment configuration and paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data" / "raw"
RESULTS_DIR = PROJECT_DIR / "results"
ARTIFACTS_DIR = PROJECT_DIR / "artifacts"

CONDITIONS = ("reference", "stale", "low_quality", "combined")
CITY_FILES = {
    "jl": "delivery/delivery_jl.csv",
    "yt": "delivery/delivery_yt.csv",
    "cq": "delivery/delivery_cq.csv",
    "sh": "delivery/delivery_sh.csv",
    "hz": "delivery/delivery_hz.csv",
}


@dataclass(frozen=True)
class ExperimentConfig:
    """Parameters kept constant across the controlled comparisons."""

    seeds: tuple[int, ...] = (17, 29, 43)
    train_fraction: float = 0.60
    validation_fraction: float = 0.20
    noisy_feed_share: float = 0.55
    stale_after_days: int = 7
    max_rows: int | None = 60_000
    late_threshold_minutes: float = 15.0


DEFAULT_CONFIG = ExperimentConfig()


def ensure_output_dirs() -> None:
    for directory in (DATA_DIR, RESULTS_DIR, ARTIFACTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)

