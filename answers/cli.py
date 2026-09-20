"""CLI for the governed-answer surface: ``python -m answers ask|decisions``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from answers.chp import ChpRejection, DecisionLedger
from answers.guardrails import GuardrailError
from answers.service import AnswerService
from answers.settings import AnswerSettings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="answers",
        description="Governed answers over the CMS Open Payments marts (CHP-hardened).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ask_parser = sub.add_parser("ask", help="Promote an answer through the CHP gate.")
    ask_parser.add_argument("--question", required=True, help="The question being answered.")
    ask_parser.add_argument("--sql", required=True, help="Single read-only SELECT over the marts.")
    ask_parser.add_argument(
        "--confirmed-by", default=None, help="Named human confirmer (locks the decision)."
    )
    ask_parser.add_argument("--mart", default=None, help="Mart table name pinned in the scope.")

    decisions_parser = sub.add_parser(
        "decisions", help="Read the append-only CHP decision ledger."
    )
    decisions_parser.add_argument("--limit", type=int, default=20)
    decisions_parser.add_argument("--id", default=None, help="One decision by id.")

    args = parser.parse_args(argv)
    settings = AnswerSettings.from_env()

    if args.command == "ask":
        service = AnswerService(settings)
        try:
            result = service.ask(
                args.question, args.sql, confirmed_by=args.confirmed_by, mart=args.mart
            )
        except (ChpRejection, GuardrailError) as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, default=str))
        return 0

    ledger = DecisionLedger(settings.decisions_path)
    records = (
        [entry for entry in [ledger.get(args.id)] if entry]
        if args.id
        else ledger.list(limit=args.limit)
    )
    print(json.dumps(records, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
