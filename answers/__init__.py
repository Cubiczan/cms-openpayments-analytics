"""Governed answers over the CMS Open Payments marts.

A minimal answer surface for a dbt + DuckDB stack: ask a question, run the
SQL under read-only guardrails, check golden parity against dbt-computed
values, and promote the answer through the CHP-hardened gate into an
append-only decision ledger. Port of the erp-control-plane GenBI promotion
gate (commit 70678cc); module + CLI, no web framework.
"""
