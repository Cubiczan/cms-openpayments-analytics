"""Deterministic identity for governed answers.

Same question in, same identifiers out — the promotion loop is
create-or-update by decision id, never blind insert.
"""

from __future__ import annotations

import hashlib


def normalize_question(question: str) -> str:
    """Lowercase and collapse whitespace so trivially different phrasings collide."""
    return " ".join(question.lower().split())


def question_hash(question: str) -> str:
    """Stable 12-hex identity of the question text."""
    return hashlib.sha256(normalize_question(question).encode("utf-8")).hexdigest()[:12]
