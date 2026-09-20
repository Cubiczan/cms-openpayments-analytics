"""CHP-hardened answer promotion gate (consensus-hardening-protocol 0.1.1).

Port of the erp-control-plane GenBI promotion gate (commit 70678cc) onto this
repo's dbt + DuckDB marts. Every answer promotion becomes a CHP decision case
so the question "why is this number in the deck?" has a mechanical answer.
Four hardening stages wrap an answer:

1. **R0 gate — before the engine.** ``chp.gates.evaluate_r0_gate`` with
   promotion-shaped criteria: the request is *solvable* (question and SQL are
   non-empty — SQL legality itself belongs to the guardrails, which refuse
   non-SELECT statements before execution), *scoped* (execution is bounded by
   a positive row cap and statement timeout), *valid* (a well-formed mart
   backing, or none), and *worth_it* (a metric-bearing question: analytical
   phrasing or a golden-set match). HALT refuses the promotion with nothing
   executed or persisted.
2. **Foundation pass — after the guardrailed execution.** The deterministic
   adversary scores the answer's foundation out of 100: 40 for guardrails
   passed, 30 for a non-empty bounded result, and 30 for golden parity — the
   executed value matching the pinned ``golden_qa.yaml`` case for this
   question. CMS Open Payments answers are financial-disclosure claims, so a
   parity-verified answer is a ``finance``-domain case and gates at CHP's
   finance floor (100): without parity evidence the foundation cannot
   self-certify, and the promotion needs a named human confirmer. A parity
   *mismatch* is fatal — an answer contradicting pinned dbt truth must not
   persist, and no confirmer can wave it through.
3. **Human lock.** Sessions open EXPLORING; a hardened case is explicitly
   transitioned to ``PROVISIONAL_LOCK`` before confirmation, and a named
   confirmer (``confirmed_by``) locks it through CHP third-party validation
   (``LOCKED``). ``CHP_REQUIRE_HUMAN_LOCK`` (default ON) makes that
   confirmation mandatory for every promotion.
4. **Decision record.** The case, verdicts, parity evidence, and promoted
   artifacts are serialised into a CHP payload envelope and appended to the
   decision ledger (JSONL under the gitignored data tree). Reads re-validate
   envelope integrity.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import threading
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from chp import (
    CHPOrchestrator,
    CHPReport,
    DecisionCase,
    Dossier,
    FoundationAttack,
    FoundationDisclosure,
    ThirdPartyValidation,
    ValidationResult,
    Verdict,
    apply_third_party_validation,
    build_payload_envelope,
    validate_payload_envelope,
)
from chp.gates import GateEvaluation, evaluate_r0_gate
from chp.models import SessionStatus

from answers.golden import is_within_tolerance, load_golden
from answers.guardrails import ExecutionResult
from answers.identity import question_hash
from answers.settings import AnswerSettings

logger = logging.getLogger(__name__)

# Deterministic adversary scoring (out of 100). The finance floor in
# chp.foundation is exactly 100, so only a parity-verified answer can
# self-certify a financial-disclosure claim.
_GUARDRAIL_POINTS = 40
_BOUNDED_RESULT_POINTS = 30
_PARITY_POINTS = 30
_FULL_SCORE = _GUARDRAIL_POINTS + _BOUNDED_RESULT_POINTS + _PARITY_POINTS

_FINANCE_DOMAIN = "finance"
_GENERAL_DOMAIN = "general"

# Metric-bearing phrasing: a deterministic proxy for R0's worth_it on the
# promotion path. Golden-set matches are always worth it by definition.
_ANALYTICAL = re.compile(
    r"\b(what|how|which|why|when|who|show|list|top|bottom|total|sum|count|"
    r"average|avg|rate|margin|revenue|cost|payment|trend|compare|breakdown|by)\b",
    re.IGNORECASE,
)


def _normalize_question(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def first_scalar(rows: list[tuple]) -> float | None:
    """A comparable scalar: exactly one row whose first cell is numeric."""
    if len(rows) != 1 or not rows[0]:
        return None
    value = rows[0][0]
    # DuckDB SUM over a DECIMAL column returns decimal.Decimal.
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


class ChpRejection(Exception):
    """CHP refused the promotion (R0 HALT, foundation REFRAME, or lock required)."""

    def __init__(self, reason: str, evaluation: GateEvaluation | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.evaluation = evaluation


@dataclass(frozen=True)
class ParityEvidence:
    """The executed answer vs the dbt-pinned golden case (when one matches)."""

    case_id: str
    metric: str
    unit: str
    expected: float
    tolerance: float
    actual: float | None  # None = the result is not a single comparable scalar
    within_tolerance: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FoundationAssessment:
    """The deterministic adversary's verdict on an executed answer."""

    score: int
    domain: str
    findings: list[str] = field(default_factory=list)
    parity: ParityEvidence | None = None
    golden_matched: bool = False


@dataclass(frozen=True)
class ChpDecision:
    """A hardened promotion: the CHP case, its report, and the assessment."""

    case: DecisionCase
    report: CHPReport
    assessment: FoundationAssessment


class DecisionLedger:
    """Append-only JSONL of CHP decision records; envelope integrity re-checked on read."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _read_all(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return []
            lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        """Newest-first records with envelope and body-integrity re-validated on read."""
        return [self._checked(entry) for entry in self._read_all()[-limit:]][::-1]

    def get(self, decision_id: str) -> dict[str, Any] | None:
        for entry in reversed(self._read_all()):
            if entry.get("decision_id") == decision_id:
                return self._checked(entry)
        return None

    @staticmethod
    def _checked(entry: dict[str, Any]) -> dict[str, Any]:
        """Re-validate a record on read: envelope structure and body digest.

        The CHP payload envelope validates structure only, so the ledger adds
        its own SHA-256 digest over the sealed body — a tampered record reads
        as ``integrity_valid: false``.
        """
        body = entry.get("body", "")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return {
            **entry,
            "envelope_valid": validate_payload_envelope(entry.get("envelope", "")),
            "integrity_valid": digest == entry.get("body_sha256"),
        }


class ChpPromotionGate:
    """Runs an answer promotion through CHP: R0 -> foundation -> human lock -> record."""

    def __init__(self, settings: AnswerSettings) -> None:
        self.settings = settings
        self.records = DecisionLedger(settings.decisions_path)

    # ------------------------------------------------------------- golden set
    def _golden(self) -> dict | None:
        try:
            return load_golden(Path(self.settings.golden_path))
        except (OSError, ValueError, KeyError, SystemExit) as exc:
            # load_golden signals an unusable golden set with SystemExit
            # (CLI-friendly); the gate treats that as parity-unavailable,
            # never as a promotion blocker on its own.
            logger.warning("golden set unavailable (%s) — parity evidence disabled", exc)
            return None

    def _match_golden(self, question: str) -> dict | None:
        golden = self._golden()
        if not golden:
            return None
        normalized = _normalize_question(question)
        for case in golden["cases"]:
            if _normalize_question(case["question"]) == normalized:
                return case
        return None

    # ------------------------------------------------------------------- R0
    def open_r0(self, question: str, sql: str, backing: str | None = None) -> GateEvaluation:
        """The pre-execution gate: HALT before the engine sees the request."""
        evaluation = evaluate_r0_gate(
            solvable=bool(question.strip()) and bool(sql.strip()),
            scoped=self.settings.row_cap > 0 and self.settings.statement_timeout_seconds > 0,
            valid=backing is None or bool(backing.strip()),
            worth_it=self._match_golden(question) is not None or bool(_ANALYTICAL.search(question)),
        )
        if evaluation.verdict != Verdict.PASS:
            failed = [name for name, result in evaluation.results.items() if result != "PASS"]
            raise ChpRejection(
                "CHP R0 gate: the promotion request failed " + ", ".join(sorted(failed)),
                evaluation,
            )
        return evaluation

    # ------------------------------------------------------------ foundation
    def assess_foundation(self, question: str, execution: ExecutionResult) -> FoundationAssessment:
        """The deterministic adversary scores the executed answer (0-100)."""
        findings: list[str] = []
        score = 0

        score += _GUARDRAIL_POINTS
        findings.append(
            "guardrails passed: SELECT-only, single statement, READ_ONLY, bounded execution"
        )

        if execution.row_count >= 1:
            score += _BOUNDED_RESULT_POINTS
            findings.append(
                f"bounded result: {execution.row_count} row(s) in {execution.latency_ms} ms"
            )
        else:
            findings.append("query returned zero rows — no result evidence")

        golden_case = self._match_golden(question)
        parity: ParityEvidence | None = None
        if golden_case is None:
            findings.append(
                "no golden-set case matches this question — parity evidence unavailable"
            )
        else:
            parity = self._parity_for(golden_case, execution)
            if parity.actual is None:
                findings.append(
                    "golden case matched but the result is not a single comparable scalar"
                    " — parity evidence unavailable"
                )
            elif parity.within_tolerance:
                score += _PARITY_POINTS
                findings.append(
                    f"golden parity: {parity.case_id} ({parity.metric}) expected"
                    f" {parity.expected} ± {parity.tolerance} {parity.unit}, got {parity.actual}"
                )
            else:
                findings.append(
                    f"golden parity MISMATCH: {parity.case_id} ({parity.metric}) expected"
                    f" {parity.expected} ± {parity.tolerance} {parity.unit}, got {parity.actual}"
                )

        return FoundationAssessment(
            score=min(score, _FULL_SCORE),
            domain=_FINANCE_DOMAIN if golden_case is not None else _GENERAL_DOMAIN,
            findings=findings,
            parity=parity,
            golden_matched=golden_case is not None,
        )

    @staticmethod
    def _parity_for(golden_case: dict, execution: ExecutionResult) -> ParityEvidence:
        actual = first_scalar(execution.rows)
        within = (
            is_within_tolerance(
                golden_case["unit"], golden_case["expected"], golden_case["tolerance"], actual
            )
            if actual is not None
            else None
        )
        return ParityEvidence(
            case_id=golden_case["id"],
            metric=golden_case["metric"],
            unit=golden_case["unit"],
            expected=golden_case["expected"],
            tolerance=golden_case["tolerance"],
            actual=actual,
            within_tolerance=within,
        )

    # --------------------------------------------------------------- session
    def harden(
        self,
        *,
        question: str,
        sql: str,
        execution: ExecutionResult,
        mart: str | None = None,
    ) -> ChpDecision:
        """Run the CHP foundation pass and open the case as PROVISIONAL_LOCK."""
        assessment = self.assess_foundation(question, execution)
        if assessment.parity is not None and assessment.parity.within_tolerance is False:
            raise ChpRejection(
                f"CHP foundation: {assessment.findings[-1]} — an answer contradicting the"
                " dbt-pinned golden set must not be promoted; fix the SQL or regenerate the"
                " golden set."
            )

        question_hash_value = question_hash(question)
        case = DecisionCase(
            decision_id=f"promote-{question_hash_value}",
            title=question,
            domain=assessment.domain,
            created_at=dt.datetime.now(dt.UTC).isoformat(),
            owner="openpayments-answers",
            high_stakes=True,
            origin_system="cms-openpayments-analytics",
            dossier=Dossier(
                core_problem=f"Promote the governed answer: {question}",
                goal_state=["record the answer as a governed, CHP-locked decision"],
                current_state=[
                    f"guardrailed execution returned {execution.row_count} row(s)"
                    f" in {execution.latency_ms} ms",
                    f"read-only mart: {Path(self.settings.duckdb_path).name}",
                ],
                constraints=[
                    f"row cap {self.settings.row_cap}",
                    f"statement timeout {self.settings.statement_timeout_seconds:g}s",
                    "READ_ONLY DuckDB",
                ],
                scope=[
                    f"mart:{mart}" if mart else "marts:main",
                    f"golden:{Path(self.settings.golden_path).name}",
                ],
            ),
        )
        disclosure = FoundationDisclosure(
            weakest_assumptions=[
                "the answer SQL faithfully implements the question",
                "the READ_ONLY mart is current as of the last dbt build",
            ],
            invalidation_conditions=[
                "golden parity mismatch on a known golden question",
                "guardrailed execution fails or returns zero rows",
            ],
            key_vulnerability=(
                f"single-source parity: only the golden expected value for"
                f" {assessment.parity.metric}"
                if assessment.parity
                else "no golden-set parity evidence for this question"
            ),
        )
        # The adversary must address each disclosed weak assumption
        # (validate_foundation_pair requires min(3, len(assumptions)) attacks).
        attack = FoundationAttack(
            attack_summary="; ".join(assessment.findings),
            foundation_score=assessment.score,
            vulnerability_strike=(
                "without golden parity the answer rests only on structural guardrails,"
                " not on pinned dbt truth"
            ),
            assumption_attacks=[
                "parity check against the dbt-pinned golden set",
                "mart freshness pinned by the golden-set window",
                "guardrails bound every execution server-side",
            ],
        )

        # Fresh orchestrator per case: the protocol registry is in-memory state
        # we do not rely on — the decision ledger is the durable record.
        report = CHPOrchestrator().run_initial_session(
            case=case, foundation_disclosure=disclosure, foundation_attack=attack
        )

        # The gate collapses CHP's multi-round phase flow into one promotion
        # step: every hardened request opens as a provisional decision pending
        # human confirmation (which apply_third_party_validation then locks).
        # A REFRAME verdict keeps that status too — the promotion may only
        # proceed through the same human lock, never self-certify.
        case.status = SessionStatus.PROVISIONAL_LOCK
        return ChpDecision(case, report, assessment)

    # ------------------------------------------------------------- human lock
    def lock(self, decision: ChpDecision, confirmed_by: str) -> SessionStatus:
        """Third-party confirmation: PROVISIONAL_LOCK -> LOCKED (recorded in the case)."""
        status = apply_third_party_validation(
            decision.case,
            ThirdPartyValidation(
                validator=confirmed_by,
                item=decision.case.decision_id,
                challenge="Confirm the promoted answer matches governed dbt truth",
                result=ValidationResult.CONFIRM,
                rationale="Named confirmer approved the answer promotion via the answers CLI",
            ),
        )
        return status

    # ----------------------------------------------------------------- record
    def record(
        self,
        decision: ChpDecision,
        *,
        question: str,
        sql: str,
        artifacts: dict[str, Any],
        confirmed_by: str | None,
    ) -> dict[str, Any]:
        """Seal the decision into a CHP payload envelope and append the ledger."""
        case = decision.case
        body = json.dumps(
            {
                "decision_id": case.decision_id,
                "title": case.title,
                "domain": case.domain,
                "sql": sql,
                "r0_verdict": decision.report.r0_verdict.value,
                "foundation_verdict": decision.report.foundation_verdict.value,
                "foundation_score": case.foundation_score,
                "adversary_findings": decision.assessment.findings,
                "parity": decision.assessment.parity.to_dict()
                if decision.assessment.parity
                else None,
                "artifacts": artifacts,
                "locked_decisions": list(case.locked_decisions),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        envelope = build_payload_envelope(body, route="PROMOTE")
        entry = {
            "decision_id": case.decision_id,
            "created_at": case.created_at,
            "question": question,
            "domain": case.domain,
            "session_status": case.status.value,
            "r0_verdict": decision.report.r0_verdict.value,
            "foundation_verdict": decision.report.foundation_verdict.value,
            "foundation_score": case.foundation_score,
            "confirmed_by": confirmed_by,
            "artifacts": artifacts,
            "body": body,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "envelope": envelope.render(),
        }
        self.records.append(entry)
        return entry
