"""Regenerate the golden Q->A set from the dbt-built marts.

The golden set is committed (golden_qa.yaml) so parity truth is versioned
alongside the code that produces it. Regenerate whenever the dbt marts
legitimately change:

    dbt build --profiles-dir .
    python answers/generate_golden.py            # rewrite golden_qa.yaml
    python answers/generate_golden.py --check    # CI-style drift gate

Every case below computes its expected value from a mart, so golden coverage
can never silently drift from the warehouse. Run from the repo root; the
DuckDB path follows OP_DUCKDB_PATH (default data/openpayments.duckdb).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root

from answers.golden import load_golden

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "golden_qa.yaml"
SCHEMA_VERSION = 1

# case id -> (question, metric, unit, tolerance, mart SQL). The SQL is the
# dbt-computed source of truth; unit/tolerance match the committed file.
CASES: dict[str, tuple[str, str, str, float, str]] = {
    "manufacturer_entity_count": (
        "How many real manufacturer entities made payments?",
        "manufacturer_entity_count",
        "count",
        0.5,
        "select count(*) from main.stg_manufacturers",
    ),
    "livanova_total_dollars": (
        "What is the total payment amount from LivaNova?",
        "livanova_dollars",
        "usd",
        552000,
        "select sum(dollars) from main.mart_npi_manufacturers"
        " where lower(sponsor_name) like '%livanova%'",
    ),
    "neuronetics_total_dollars": (
        "What is the total payment amount from Neuronetics?",
        "neuronetics_dollars",
        "usd",
        50000,
        "select sum(dollars) from main.mart_npi_manufacturers"
        " where lower(sponsor_name) like '%neuronetic%'",
    ),
    "untapped_bench_count": (
        "How many untapped bench investigators are in the psychiatry population?",
        "untapped_bench_count",
        "count",
        0.5,
        "select count(*) from main.mart_psychiatry_bench where capacity_segment = 'untapped_bench'",
    ),
    "active_investigator_count": (
        "How many active investigators are in the psychiatry population?",
        "active_investigator_count",
        "count",
        0.5,
        "select count(*) from main.mart_psychiatry_bench"
        " where capacity_segment = 'active_investigator'",
    ),
}


def compute_cases(duckdb_path: str) -> list[dict]:
    con = duckdb.connect(duckdb_path, read_only=True)
    try:
        cases = []
        for case_id, (question, metric, unit, tolerance, sql) in CASES.items():
            row = con.execute(sql).fetchone()
            if row is None or row[0] is None:
                raise SystemExit(f"golden case {case_id!r}: mart query returned nothing: {sql}")
            cases.append(
                {
                    "id": case_id,
                    "question": question,
                    "metric": metric,
                    "unit": unit,
                    "expected": float(row[0]),
                    "tolerance": tolerance,
                }
            )
        return cases
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate the golden Q->A set from the marts.")
    parser.add_argument(
        "--check", action="store_true", help="Fail if values drift from the committed file."
    )
    parser.add_argument("--duckdb", default=None, help="Path to the dbt-built DuckDB file.")
    args = parser.parse_args()

    duckdb_path = args.duckdb or "data/openpayments.duckdb"
    if not Path(duckdb_path).exists():
        print(
            f"mart not found at {duckdb_path} — run `dbt build --profiles-dir .` first",
            file=sys.stderr,
        )
        return 2

    cases = compute_cases(duckdb_path)
    document = {
        "schema_version": SCHEMA_VERSION,
        "generated_from": (
            "dbt marts (README/test-documented values); regenerate with answers/generate_golden.py"
        ),
        "cases": cases,
    }

    if args.check:
        committed = load_golden(GOLDEN_PATH)
        by_id = {case["id"]: case for case in committed["cases"]}
        drift = []
        for case in cases:
            pinned = by_id.get(case["id"])
            if pinned is None:
                drift.append(f"{case['id']}: no committed case")
            elif abs(float(pinned["expected"]) - case["expected"]) > float(pinned["tolerance"]):
                drift.append(
                    f"{case['id']}: mart {case['expected']} vs pinned {pinned['expected']}"
                    f" (tolerance {pinned['tolerance']})"
                )
        if drift:
            print("golden parity FAILED — regenerate golden_qa.yaml:")
            for line in drift:
                print(f"  - {line}")
            return 1
        print(f"golden parity OK: {len(cases)} cases match the dbt marts.")
        return 0

    GOLDEN_PATH.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    print(f"wrote {len(cases)} golden cases to {GOLDEN_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
