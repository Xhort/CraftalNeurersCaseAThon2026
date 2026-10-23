"""Download, validate, and load LaDe delivery data."""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from config import CITY_FILES, DATA_DIR, ensure_output_dirs


HF_BASE_URL = "https://huggingface.co/datasets/Cainiao-AI/LaDe/resolve/main"
REQUIRED_COLUMNS = {
    "city",
    "courier_id",
    "region_id",
    "accept_time",
    "delivery_time",
    "lng",
    "lat",
}


def download_city(city: str = "jl", force: bool = False) -> Path:
    """Download one delivery CSV directly from the official LaDe repository."""
    city = city.lower()
    if city not in CITY_FILES:
        raise ValueError(f"Unknown city '{city}'. Choose from {sorted(CITY_FILES)}")

    ensure_output_dirs()
    remote_path = CITY_FILES[city]
    destination = DATA_DIR / Path(remote_path).name
    if destination.exists() and not force:
        return destination

    url = f"{HF_BASE_URL}/{remote_path}"
    temporary = destination.with_suffix(".csv.part")
    try:
        urllib.request.urlretrieve(url, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def load_delivery_csv(path: str | Path, max_rows: int | None = None) -> pd.DataFrame:
    """Load one LaDe-D city file without mixing it with incompatible file types."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Dataset not found at {path}. Run `python data.py --download --city jl`."
        )
    frame = pd.read_csv(path, nrows=max_rows, low_memory=False)
    # Older mirrors use order_id while the dataset card calls it package_id.
    if "package_id" not in frame and "order_id" in frame:
        frame = frame.rename(columns={"order_id": "package_id"})
    missing = REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"LaDe delivery file is missing columns: {sorted(missing)}")
    frame["data_mode"] = "LaDe"
    return frame


def make_synthetic_delivery_data(rows: int = 8_000, seed: int = 17) -> pd.DataFrame:
    """Create raw-schema demo data. Never use it as competition evidence."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2020-03-01")
    day = rng.integers(0, 60, rows)
    minute = rng.integers(7 * 60, 19 * 60, rows)
    accept = start + pd.to_timedelta(day, unit="D") + pd.to_timedelta(minute, unit="m")
    region = rng.integers(1, 18, rows)
    courier = rng.integers(1, 120, rows)
    lng = 126.45 + rng.normal(0, 0.08, rows)
    lat = 43.85 + rng.normal(0, 0.07, rows)
    accept_lng = lng + rng.normal(0, 0.025, rows)
    accept_lat = lat + rng.normal(0, 0.025, rows)
    distance = np.sqrt((lng - accept_lng) ** 2 + (lat - accept_lat) ** 2) * 86
    rush = ((accept.hour >= 16) & (accept.hour <= 18)).astype(float)
    duration = 18 + 2.8 * distance + 5 * rush + region * 0.35 + rng.normal(0, 6, rows)
    duration = np.clip(duration, 4, 180)
    delivery = accept + pd.to_timedelta(duration, unit="m")

    return pd.DataFrame(
        {
            "package_id": [f"demo-{i:06d}" for i in range(rows)],
            "region_id": region,
            "city": "Jilin-demo",
            "courier_id": courier,
            "lng": lng,
            "lat": lat,
            "aoi_id": rng.integers(1, 500, rows),
            "aoi_type": rng.integers(0, 4, rows),
            "accept_time": accept.strftime("%m-%d %H:%M:%S"),
            "accept_gps_time": accept.strftime("%m-%d %H:%M:%S"),
            "accept_gps_lng": accept_lng,
            "accept_gps_lat": accept_lat,
            "delivery_time": delivery.strftime("%m-%d %H:%M:%S"),
            "delivery_gps_time": delivery.strftime("%m-%d %H:%M:%S"),
            "delivery_gps_lng": lng,
            "delivery_gps_lat": lat,
            "ds": accept.strftime("%m%d").astype(int),
            "data_mode": "SYNTHETIC_DEMO",
        }
    ).sort_values("accept_time", ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Download a LaDe city CSV")
    parser.add_argument("--city", choices=sorted(CITY_FILES), default="jl")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.download:
        path = download_city(args.city, args.force)
        print(f"Downloaded {path}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

