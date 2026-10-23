"""Command-line entry point for the complete experiment."""

from __future__ import annotations

import argparse
from pathlib import Path

from config import DATA_DIR, ExperimentConfig
from data import download_city, load_delivery_csv, make_synthetic_delivery_data
from evaluate import run_suite


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, help="Path to one LaDe delivery CSV")
    parser.add_argument("--download", action="store_true", help="Download the chosen city first")
    parser.add_argument("--city", choices=("jl", "yt", "cq", "sh", "hz"), default="jl")
    parser.add_argument("--synthetic", action="store_true", help="Use clearly labeled demo data")
    parser.add_argument("--max-rows", type=int, default=60_000)
    parser.add_argument("--seeds", type=int, nargs="+", default=[17, 29, 43])
    args = parser.parse_args()

    if args.synthetic:
        raw = make_synthetic_delivery_data(min(args.max_rows, 12_000), args.seeds[0])
    else:
        path = args.data
        if args.download:
            path = download_city(args.city)
        if path is None:
            path = DATA_DIR / f"delivery_{args.city}.csv"
        raw = load_delivery_csv(path, args.max_rows)

    config = ExperimentConfig(seeds=tuple(args.seeds), max_rows=args.max_rows)
    summary, _, _ = run_suite(raw, config, save_outputs=True)
    print(summary.to_string(index=False))
    print("\nSaved results/ CSVs and artifacts/ models.")


if __name__ == "__main__":
    main()

