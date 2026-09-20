"""The promotion loop: NL answer -> CHP hardening -> guardrailed execution -> decision record.

Order of operations is the governance story:

1. **CHP R0 gate** (fail closed, before the engine): the promotion request must
   be solvable, scoped, valid, and worth_it — or it is refused with nothing
   executed.
2. **Guardrails** (fail closed): the answer SQL executes against the
   canonical READ_ONLY DuckDB URI — SELECT-only, single statement, statement
   timeout, row cap.
3. **CHP foundation pass**: the deterministic adversary scores the answer —
   guardrails, bounded result, golden parity against the dbt-pinned
   ``golden_qa.yaml``. A finance-domain answer (a parity-verified
   financial-disclosure claim) that cannot self-certify (score below CHP's
   finance floor of 100) requires a named human confirmer; a golden parity
   mismatch is refused outright. Every promotion opens as a CHP
   ``PROVISIONAL_LOCK`` case; ``confirmed_by`` locks it.
4. **Governed record**: the sealed CHP decision lands in the append-only
   decision ledger — this repo's promotion artifact (no BI layer to persist
   charts into).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from chp import Verdict

from answers.chp import ChpPromotionGate, ChpRejection, first_scalar
from answers.guardrails import ExecutionResult, GuardrailError, execute_readonly
from answers.identity import question_hash
from answers.settings import AnswerSettings


class AnswerService:
    """One promotion request, end to end."""

    def __init__(
        self,
        settings: AnswerSettings,
        executor: Callable[..., ExecutionResult] = execute_readonly,
        gate: ChpPromotionGate | None = None,
    ) -> None:
        self.settings = settings
        self.executor = executor
        self.gate = gate if gate is not None else ChpPromotionGate(settings)

    def ask(
        self,
        question: str,
        sql: str,
        *,
        confirmed_by: str | None = None,
        mart: str | None = None,
    ) -> dict[str, Any]:
        """CHP-gate, guardrail, execute, lock, then record the governed answer."""
        question_hash_value = question_hash(question)

        # CHP R0 — before the engine: an ill-posed request costs nothing.
        self.gate.open_r0(question, sql, mart)

        execution = self._execute(sql)

        # CHP foundation pass — the deterministic adversary scores the answer.
        decision = self.gate.harden(question=question, sql=sql, execution=execution, mart=mart)
        if decision.report.foundation_verdict != Verdict.PASS and not confirmed_by:
            reason = (
                f"CHP foundation: {decision.report.foundation_verdict.value}"
                f" (score {decision.case.foundation_score}, {decision.assessment.domain} domain)"
                " — the promotion cannot self-certify; retry with a named confirmer"
                " (confirmed_by)."
            )
            raise ChpRejection(reason)
        if self.settings.require_human_lock and not confirmed_by:
            reason = (
                "CHP human lock: CHP_REQUIRE_HUMAN_LOCK is on by default — every promotion"
                " needs a named confirmer (confirmed_by)."
            )
            raise ChpRejection(reason)
        if confirmed_by:
            self.gate.lock(decision, confirmed_by)

        answer = first_scalar(execution.rows) if execution.row_count == 1 else None
        artifacts = {
            "row_count": execution.row_count,
            "latency_ms": execution.latency_ms,
            "columns": execution.columns,
            "answer": answer,
        }
        record = self.gate.record(
            decision,
            question=question,
            sql=sql,
            artifacts=artifacts,
            confirmed_by=confirmed_by,
        )
        return {
            "question": question,
            "question_hash": question_hash_value,
            "sql": sql,
            "columns": execution.columns,
            "rows": [list(row) for row in execution.rows],
            "row_count": execution.row_count,
            "latency_ms": execution.latency_ms,
            "answer": answer,
            "chp": {
                "decision_id": record["decision_id"],
                "session_status": record["session_status"],
                "r0_verdict": record["r0_verdict"],
                "foundation_verdict": record["foundation_verdict"],
                "foundation_score": record["foundation_score"],
                "confirmed_by": confirmed_by,
                "parity": decision.assessment.parity.to_dict()
                if decision.assessment.parity
                else None,
            },
        }

    # ------------------------------------------------------------ guardrails
    def _execute(self, sql: str) -> ExecutionResult:
        """Run the guardrailed execution; guardrail failures propagate fail-closed."""
        return self.executor(
            sql,
            uri=self.settings.duckdb_uri,
            timeout_seconds=self.settings.statement_timeout_seconds,
            row_cap=self.settings.row_cap,
        )


__all__ = ["AnswerService", "ChpRejection", "GuardrailError"]
