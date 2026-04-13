from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.seeds.runner import SeedRunner

DEFAULT_SEED_DIR = Path(__file__).resolve().parents[2] / "seed"


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Phase 1 seed data")
    parser.add_argument(
        "--seed-dir",
        default=str(DEFAULT_SEED_DIR),
        help="Folder containing platform/reference/vendors/market seed assets",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    configure_logging(args.verbose)
    runner = SeedRunner(args.seed_dir)
    results = runner.run()

    total_inserted = sum(item.inserted for item in results)
    total_updated = sum(item.updated for item in results)

    print("\nSeed run complete")
    print("=" * 72)
    for item in results:
        print(f"{item.name:<42} inserted={item.inserted:>4} updated={item.updated:>4}")
    print("-" * 72)
    print(f"TOTAL{'':<37} inserted={total_inserted:>4} updated={total_updated:>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
