"""CHP-hardened answer promotion gate: R0 refusal, foundation scoring, human lock, and the ledger.

Covers the consensus-hardening-protocol integration (``answers/chp.py``), mirroring
the erp-control-plane CHP suite (commit 70678cc), adapted to this repo:

- the promotion-shaped R0 gate refuses ill-posed requests before the engine;
- the deterministic adversary scores guardrails + bounded result + golden
  parity (CMS Open Payments answers are financial-disclosure claims: a
  parity-verified answer is finance-domain and gates at CHP's finance floor of
  100, and a parity mismatch is fatal);
- every hardened case opens ``PROVISIONAL_LOCK`` and a named confirmer locks
  it through CHP third-party validation;
- the human lock is mandatory BY DEFAULT (``CHP_REQUIRE_HUMAN_LOCK``), unlike
  the erp ref where the flag opts in;
- every promotion seals a CHP payload envelope into the append-only decision
  ledger, whose reads re-validate envelope integrity.

Golden-set parity is exercised against a small temporary golden file whose
expected value matches the ``analytics_file`` fixture's ``mart_psychiatry_bench``
(3 untapped bench rows), so parity here is self-grounding rather than pinned
to the 49M-row dbt build.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
import yaml
from chp import Verdict
from chp.models import SessionStatus

from answers.chp import ChpPromotionGate, ChpRejection, first_scalar
from answers.guardrails import ExecutionResult, MultipleStatements, NotSelectOnly
from answers.service import AnswerService
from answers.settings import AnswerSettings

GOLDEN_QUESTION = "How many untapped bench investigators are in the psychiatry population?"
GOLDEN_SQL = "select count(*) from mart_psychiatry_bench where capacity_segment = 'untapped_bench'"
QUESTION = "What is the total payment amount for the top sponsor?"
SQL = "select sponsor, total_dollars from mart_sponsor_ranking order by total_dollars desc"
BENCH_COUNT = 3.0  # the analytics_file fixture seeds exactly 3 untapped_bench rows
CONFIRMER = "sam@cubiczan.com"


def execution(
    rows: list[tuple], columns: tuple[str, ...] = ("untapped_bench_count",)
) -> ExecutionResult:
    return ExecutionResult(
        columns=list(columns),
        rows=rows,
        row_count=len(rows),
        latency_ms=3,
        duckdb_uri="duckdb:///openpayments.duckdb?access_mode=READ_ONLY&read_only=1",
    )


def scalar_case(expected: float = BENCH_COUNT) -> dict:
    return {
        "id": "untapped_bench_count",
        "question": GOLDEN_QUESTION,
        "metric": "untapped_bench_count",
        "unit": "count",
        "expected": expected,
        "tolerance": 0.5,
    }


def base_env(
    tmp_path: Path, cases: list[dict] | None = None, require_lock: str | None = None
) -> dict[str, str]:
    golden_path = tmp_path / "golden.yaml"
    golden_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "generated_from": "test",
                "cases": list(cases or []),
            }
        ),
        encoding="utf-8",
    )
    env = {
        "OP_DUCKDB_PATH": str(tmp_path / "openpayments.duckdb"),
        "CHP_GOLDEN_PATH": str(golden_path),
        "CHP_DECISIONS_PATH": str(tmp_path / "chp_decisions.jsonl"),
    }
    if require_lock is not None:
        env["CHP_REQUIRE_HUMAN_LOCK"] = require_lock
    return env


def make_service(env: dict[str, str]) -> AnswerService:
    return AnswerService(AnswerSettings.from_env(env))


@pytest.fixture()
def analytics_file(tmp_path: Path) -> Path:
    """A small stand-in mart: 3 untapped_bench, 2 active_investigator, 1 low_signal."""
    path = tmp_path / "openpayments.duckdb"
    con = duckdb.connect(str(path))
    con.execute("create table mart_psychiatry_bench (npi varchar, capacity_segment varchar)")
    con.execute(
        "insert into mart_psychiatry_bench values"
        " ('1','untapped_bench'), ('2','untapped_bench'), ('3','untapped_bench'),"
        " ('4','active_investigator'), ('5','active_investigator'), ('6','low_signal')"
    )
    con.execute("create table mart_sponsor_ranking (sponsor varchar, total_dollars double)")
    con.execute(
        "insert into mart_sponsor_ranking values"
        " ('LivaNova', 55200000.0), ('Neuronetics', 2800000.0), ('AbbVie', 90.0)"
    )
    con.close()
    return path


# ----------------------------------------------------------------------- R0


def test_r0_refuses_a_non_analytical_question_before_execution(tmp_path: Path) -> None:
    gate = ChpPromotionGate(AnswerSettings.from_env(base_env(tmp_path)))
    with pytest.raises(ChpRejection) as excinfo:
        gate.open_r0("hello there", "select 1", None)
    assert excinfo.value.evaluation.results["Worth_it"] == "FATAL"


def test_r0_refuses_an_empty_request(tmp_path: Path) -> None:
    gate = ChpPromotionGate(AnswerSettings.from_env(base_env(tmp_path)))
    with pytest.raises(ChpRejection) as excinfo:
        gate.open_r0("What is revenue by sponsor?", "   ", None)
    assert excinfo.value.evaluation.results["Solvable"] == "FATAL"


def test_r0_accepts_analytical_and_golden_questions(tmp_path: Path) -> None:
    gate = ChpPromotionGate(AnswerSettings.from_env(base_env(tmp_path, [scalar_case()])))
    gate.open_r0(QUESTION, "select 1", None)
    gate.open_r0(GOLDEN_QUESTION, GOLDEN_SQL, None)


# ---------------------------------------------------------------- foundation


def test_golden_parity_scores_a_full_finance_foundation(tmp_path: Path) -> None:
    gate = ChpPromotionGate(AnswerSettings.from_env(base_env(tmp_path, [scalar_case()])))
    assessment = gate.assess_foundation(GOLDEN_QUESTION, execution([(BENCH_COUNT,)]))
    assert assessment.domain == "finance"
    assert assessment.score == 100
    assert assessment.parity is not None and assessment.parity.within_tolerance is True


def test_duckdb_decimal_sums_are_comparable(tmp_path: Path) -> None:
    # DuckDB SUM over a DECIMAL column returns decimal.Decimal, not float.
    gate = ChpPromotionGate(AnswerSettings.from_env(base_env(tmp_path, [scalar_case()])))
    assessment = gate.assess_foundation(GOLDEN_QUESTION, execution([(Decimal("3.0"),)]))
    assert assessment.parity is not None
    assert assessment.parity.actual == 3.0
    assert assessment.parity.within_tolerance is True
    assert assessment.score == 100


def test_golden_parity_mismatch_is_fatal(tmp_path: Path) -> None:
    gate = ChpPromotionGate(
        AnswerSettings.from_env(base_env(tmp_path, [scalar_case(expected=999.0)]))
    )
    with pytest.raises(ChpRejection, match="MISMATCH"):
        gate.harden(question=GOLDEN_QUESTION, sql=GOLDEN_SQL, execution=execution([(BENCH_COUNT,)]))


def test_general_answer_passes_without_parity(tmp_path: Path) -> None:
    gate = ChpPromotionGate(AnswerSettings.from_env(base_env(tmp_path)))
    assessment = gate.assess_foundation(
        QUESTION,
        execution(
            [("LIVANOVA", 55_200_000.0), ("NEURONETICS", 2_800_000.0), ("ABBVIE", 90.0)],
            ("sponsor", "total_dollars"),
        ),
    )
    assert assessment.domain == "general"
    assert assessment.score == 70  # guardrails 40 + bounded result 30; no parity evidence


def test_zero_rows_cannot_self_certify(tmp_path: Path) -> None:
    gate = ChpPromotionGate(AnswerSettings.from_env(base_env(tmp_path)))
    assessment = gate.assess_foundation(QUESTION, execution([]))
    assert assessment.score == 40


def test_unverifiable_finance_answer_cannot_self_certify_below_the_finance_floor(
    tmp_path: Path,
) -> None:
    # A golden question whose result is not a single comparable scalar:
    # parity evidence unavailable, so the finance floor of 100 is unreachable.
    gate = ChpPromotionGate(AnswerSettings.from_env(base_env(tmp_path, [scalar_case()])))
    assessment = gate.assess_foundation(
        GOLDEN_QUESTION,
        execution([(3, "untapped_bench"), (2, "active_investigator")], ("bench", "segment")),
    )
    assert assessment.domain == "finance"
    assert assessment.score == 70
    decision = gate.harden(
        question=GOLDEN_QUESTION,
        sql=GOLDEN_SQL,
        execution=execution(
            [(3, "untapped_bench"), (2, "active_investigator")], ("bench", "segment")
        ),
    )
    assert decision.report.foundation_verdict == Verdict.REFRAME


def test_golden_parser_failures_disable_parity_instead_of_crashing(tmp_path: Path) -> None:
    # An empty golden set makes load_golden raise SystemExit; the gate must
    # treat that as parity-unavailable, not propagate the crash.
    gate = ChpPromotionGate(AnswerSettings.from_env(base_env(tmp_path, cases=[])))
    assessment = gate.assess_foundation(GOLDEN_QUESTION, execution([(BENCH_COUNT,)]))
    assert assessment.golden_matched is False
    assert assessment.domain == "general"
    assert assessment.score == 70


def test_first_scalar_rejects_non_numeric_and_boolean_cells() -> None:
    assert first_scalar([("LivaNova", 55_200_000.0), ("Neuronetics", 2_800_000.0)]) is None
    assert first_scalar([(True,)]) is None
    assert first_scalar([(None,)]) is None
    assert first_scalar([]) is None
    assert first_scalar([(Decimal("2800000"),)]) == 2_800_000.0


def test_hardened_case_opens_provisional_and_locks_with_a_confirmer(tmp_path: Path) -> None:
    gate = ChpPromotionGate(AnswerSettings.from_env(base_env(tmp_path)))
    decision = gate.harden(
        question=QUESTION,
        sql=SQL,
        execution=execution(
            [("LIVANOVA", 55_200_000.0), ("NEURONETICS", 2_800_000.0), ("ABBVIE", 90.0)],
            ("sponsor", "total_dollars"),
        ),
    )
    assert decision.case.status == SessionStatus.PROVISIONAL_LOCK
    assert gate.lock(decision, CONFIRMER) == SessionStatus.LOCKED
    assert decision.case.decision_id in decision.case.locked_decisions


# -------------------------------------------------------------------- ledger


def promoted(gate: ChpPromotionGate) -> dict:
    decision = gate.harden(
        question=QUESTION,
        sql=SQL,
        execution=execution(
            [("LIVANOVA", 55_200_000.0), ("NEURONETICS", 2_800_000.0), ("ABBVIE", 90.0)],
            ("sponsor", "total_dollars"),
        ),
    )
    return gate.record(
        decision,
        question=QUESTION,
        sql=SQL,
        artifacts={
            "row_count": 3,
            "latency_ms": 3,
            "columns": ["sponsor", "total_dollars"],
            "answer": None,
        },
        confirmed_by=None,
    )


def test_decision_record_seals_an_envelope(tmp_path: Path) -> None:
    gate = ChpPromotionGate(AnswerSettings.from_env(base_env(tmp_path)))
    record = promoted(gate)

    listing = gate.records.list()
    assert len(listing) == 1
    assert listing[0]["envelope_valid"] is True
    assert listing[0]["integrity_valid"] is True
    assert listing[0]["decision_id"] == record["decision_id"]
    assert gate.records.get(record["decision_id"])["question"] == QUESTION
    assert gate.records.get("promote-missing") is None


def test_tampered_ledger_bodies_read_as_invalid(tmp_path: Path) -> None:
    gate = ChpPromotionGate(AnswerSettings.from_env(base_env(tmp_path)))
    promoted(gate)

    path = gate.records.path
    lines = path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    # tamper with the sealed payload body: inflate the foundation score
    entry["body"] = entry["body"].replace('"foundation_score": 70', '"foundation_score": 100')
    lines[0] = json.dumps(entry)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    record = gate.records.list()[0]
    assert record["integrity_valid"] is False
    assert record["envelope_valid"] is True  # the CHP envelope checks structure only


# ------------------------------------------------------- service integration


def test_promotion_requires_a_confirmer_by_default(tmp_path: Path, analytics_file: Path) -> None:
    service = make_service(base_env(tmp_path, [scalar_case()]))
    with pytest.raises(ChpRejection, match="human lock"):
        service.ask(GOLDEN_QUESTION, GOLDEN_SQL)
    assert service.gate.records.list() == []


def test_a_named_confirmer_locks_the_decision(tmp_path: Path, analytics_file: Path) -> None:
    service = make_service(base_env(tmp_path, [scalar_case()]))
    result = service.ask(GOLDEN_QUESTION, GOLDEN_SQL, confirmed_by=CONFIRMER)
    assert result["chp"]["session_status"] == SessionStatus.LOCKED.value
    assert result["chp"]["foundation_score"] == 100
    assert result["answer"] == BENCH_COUNT
    record = service.gate.records.list()[0]
    assert record["confirmed_by"] == CONFIRMER
    assert record["session_status"] == SessionStatus.LOCKED.value
    assert record["artifacts"]["answer"] == BENCH_COUNT


def test_human_lock_can_be_disabled_for_unconfirmed_answers(
    tmp_path: Path, analytics_file: Path
) -> None:
    service = make_service(base_env(tmp_path, [scalar_case()], require_lock="0"))
    result = service.ask(GOLDEN_QUESTION, GOLDEN_SQL)
    assert result["chp"]["session_status"] == SessionStatus.PROVISIONAL_LOCK.value
    assert result["chp"]["confirmed_by"] is None
    assert result["chp"]["foundation_score"] == 100


def test_general_answer_promotes_at_70_when_the_lock_is_disabled(
    tmp_path: Path, analytics_file: Path
) -> None:
    service = make_service(base_env(tmp_path, require_lock="0"))
    result = service.ask(QUESTION, SQL)
    assert result["chp"]["session_status"] == SessionStatus.PROVISIONAL_LOCK.value
    assert result["chp"]["foundation_score"] == 70
    assert result["row_count"] == 3


def test_parity_mismatch_refuses_the_promotion_even_with_a_confirmer(
    tmp_path: Path, analytics_file: Path
) -> None:
    service = make_service(base_env(tmp_path, [scalar_case(expected=999.0)]))
    with pytest.raises(ChpRejection, match="MISMATCH"):
        service.ask(GOLDEN_QUESTION, GOLDEN_SQL, confirmed_by=CONFIRMER)
    assert service.gate.records.list() == []


def test_write_statements_are_refused_before_the_engine(
    tmp_path: Path, analytics_file: Path
) -> None:
    service = make_service(base_env(tmp_path, [scalar_case()]))
    with pytest.raises(NotSelectOnly):
        service.ask(QUESTION, "delete from mart_sponsor_ranking")
    assert service.gate.records.list() == []
    with pytest.raises(MultipleStatements):
        service.ask(QUESTION, "select 1; select 2")


def test_decisions_cli_lists_records(
    tmp_path: Path,
    analytics_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from answers.cli import main as cli_main

    env = base_env(tmp_path, [scalar_case()])
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    service = make_service(env)
    result = service.ask(GOLDEN_QUESTION, GOLDEN_SQL, confirmed_by=CONFIRMER)

    assert cli_main(["decisions", "--limit", "10"]) == 0
    out = capsys.readouterr().out
    assert result["chp"]["decision_id"] in out
    assert '"integrity_valid": true' in out

    assert cli_main(["decisions", "--id", "promote-missing"]) == 0
    assert json.loads(capsys.readouterr().out) == []
