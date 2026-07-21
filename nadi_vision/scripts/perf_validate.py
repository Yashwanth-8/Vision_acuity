"""Performance gate checks for Nadi Vision on Raspberry Pi 4.

Usage:
    python scripts/perf_validate.py --benchmark-json real_benchmark_results.json

This script evaluates benchmark outputs against Phase 8 acceptance budgets.
It does not run inference itself; it validates already captured measurements.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


DEFAULT_THRESHOLDS = {
    "avg_ms": 20.0,
    "p95_ms": 30.0,
    "max_ms": 50.0,
    "rss_mb": 250.0,
}


def _load_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"Benchmark file not found: {path}")

    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)

    if not isinstance(payload, list):
        raise SystemExit("Benchmark JSON must be a list of model rows")

    rows: list[dict] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        rows.append(row)
    if not rows:
        raise SystemExit("Benchmark JSON contains no valid model rows")
    return rows


def _find_model(rows: list[dict], model_name: str) -> dict:
    wanted = model_name.lower()
    for row in rows:
        if str(row.get("model", "")).lower() == wanted:
            return row
    raise SystemExit(f"Model '{model_name}' not found in benchmark JSON")


def _check_thresholds(row: dict, thresholds: dict[str, float]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for key, limit in thresholds.items():
        value = float(row.get(key, float("inf")))
        if value > limit:
            failures.append(f"{key}={value:.2f} exceeds limit {limit:.2f}")
    return (len(failures) == 0, failures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-json", default="real_benchmark_results.json")
    parser.add_argument("--model", default="MediaPipe")
    parser.add_argument("--avg-ms-max", type=float, default=DEFAULT_THRESHOLDS["avg_ms"])
    parser.add_argument("--p95-ms-max", type=float, default=DEFAULT_THRESHOLDS["p95_ms"])
    parser.add_argument("--max-ms-max", type=float, default=DEFAULT_THRESHOLDS["max_ms"])
    parser.add_argument("--rss-mb-max", type=float, default=DEFAULT_THRESHOLDS["rss_mb"])
    args = parser.parse_args()

    rows = _load_rows(Path(args.benchmark_json))
    model_row = _find_model(rows, args.model)

    thresholds = {
        "avg_ms": args.avg_ms_max,
        "p95_ms": args.p95_ms_max,
        "max_ms": args.max_ms_max,
        "rss_mb": args.rss_mb_max,
    }

    ok, failures = _check_thresholds(model_row, thresholds)

    print(f"Model: {model_row.get('model')}")
    print(
        "Metrics: "
        f"avg={float(model_row.get('avg_ms', 0.0)):.2f}ms "
        f"p95={float(model_row.get('p95_ms', 0.0)):.2f}ms "
        f"max={float(model_row.get('max_ms', 0.0)):.2f}ms "
        f"rss={float(model_row.get('rss_mb', 0.0)):.2f}MB"
    )

    if ok:
        print("Result: PASS (meets Phase 8 thresholds)")
        return 0

    print("Result: FAIL")
    for failure in failures:
        print(f" - {failure}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
