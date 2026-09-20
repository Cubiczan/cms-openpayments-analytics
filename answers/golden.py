"""Golden set loading and parity scoring (deterministic, keyless).

The golden set pins dbt-computed values from the marts (and the repo's dbt
tests, which encode real analytical failures, as golden rules). An unusable
golden set signals with ``SystemExit`` — CLI-friendly, and the promotion gate
treats that as parity-unavailable, never as a silent pass.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import yaml

# "count" extends the erp ref's units: this repo's marts answer count
# questions (investigators, entities) that the ref's kpi mart did not.
VALID_UNITS = {"ratio", "percent", "days", "usd", "count"}

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def load_golden(path: Path) -> dict:
    """Load and validate a golden YAML file, or raise ``SystemExit``."""
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise SystemExit(f"unsupported golden schema_version: {document.get('schema_version')!r}")
    cases = document.get("cases") or []
    if not cases:
        raise SystemExit(f"no golden cases found in {path}")
    ids = [case.get("id") for case in cases]
    if len(set(ids)) != len(ids):
        raise SystemExit("golden case ids are not unique")
    for case in cases:
        for field_name in ("question", "metric", "unit", "expected", "tolerance"):
            if case.get(field_name) is None:
                raise SystemExit(f"golden case {case.get('id')!r} is missing {field_name!r}")
        if case["unit"] not in VALID_UNITS:
            raise SystemExit(f"golden case {case['id']!r} has unknown unit {case['unit']!r}")
        if float(case["tolerance"]) <= 0:
            raise SystemExit(f"golden case {case['id']!r} tolerance must be positive")
    return document


def is_within_tolerance(unit: str, expected: float, tolerance: float, actual: float) -> bool:
    """Compare an executed value against the golden expectation.

    percent-unit metrics are stored as ratios (0.2443 == 24.43%); both
    interpretations are accepted so phrasing conventions cannot fake parity.
    """
    candidates = {actual, actual / 100.0} if unit == "percent" else {actual}
    return any(not math.isnan(c) and abs(c - expected) <= tolerance for c in candidates)


def extract_first_number(text: str) -> float | None:
    """First numeric token in an answer string, or None."""
    match = _NUMBER_RE.search(text.replace("%", ""))
    if not match:
        return None
    return float(match.group(0).replace(",", ""))
