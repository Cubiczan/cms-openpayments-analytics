"""Environment-driven answer-gate settings (12-factor, like the erp ref).

Every knob comes from the environment. Paths default under the gitignored
``data/`` tree — runtime state, not versioned content. Unlike the erp ref,
``CHP_REQUIRE_HUMAN_LOCK`` defaults ON: publishing a financial-disclosure
claim without a named human confirmer is the failure mode this gate exists
to prevent.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_FALSE = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class AnswerSettings:
    """Deployment settings for the governed-answer promotion loop."""

    # The dbt-built DuckDB file (profiles.yml: ./data/openpayments.duckdb).
    duckdb_path: str
    row_cap: int
    statement_timeout_seconds: float
    # Golden set used for promotion-time parity evidence (dbt-computed values).
    golden_path: Path
    # Append-only CHP decision ledger.
    decisions_path: Path
    # Human lock is mandatory unless explicitly disabled — default ON.
    require_human_lock: bool

    @property
    def duckdb_uri(self) -> str:
        """The canonical READ_ONLY DuckDB URI for every answer execution."""
        return f"duckdb:///{self.duckdb_path}?access_mode=READ_ONLY&read_only=1"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AnswerSettings:
        env = dict(os.environ if env is None else env)
        state_dir = Path(env.get("CHP_STATE_DIR", str(REPO_ROOT / "data" / "chp")))
        return cls(
            duckdb_path=env.get("OP_DUCKDB_PATH", "data/openpayments.duckdb"),
            row_cap=int(env.get("CHP_ROW_CAP", "10000")),
            statement_timeout_seconds=float(env.get("CHP_STATEMENT_TIMEOUT_SECONDS", "30")),
            golden_path=Path(env.get("CHP_GOLDEN_PATH", str(REPO_ROOT / "golden_qa.yaml"))),
            decisions_path=Path(env.get("CHP_DECISIONS_PATH", str(state_dir / "decisions.jsonl"))),
            require_human_lock=env.get("CHP_REQUIRE_HUMAN_LOCK", "").strip().lower() not in _FALSE,
        )
