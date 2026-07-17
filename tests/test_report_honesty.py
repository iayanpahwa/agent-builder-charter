"""Tests for run_headless.py's report() — the honest, TOTAL enforcement summary.

report(charter) returns a list of (status, field, note) tuples with one row per
enforcement-relevant field. These tests lock in two guarantees:
  1. Totality — every field a charter can carry gets a row; nothing is silently
     dropped from the report.
  2. Honesty — the status/note wording doesn't overstate what headless actually
     enforces (e.g. credentials are inherited from the host, not isolated).

No tokens, no network, no subprocess — report() is pure and called directly.
"""

import copy
import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "builders" / "claude-headless"))
from run_headless import report  # noqa: E402

REQUIRED_FIELDS = [
    "status", "context", "model", "sandbox", "budget", "tools", "credentials",
    "egress", "approval_tier", "data", "evals",
]

FULLY_POPULATED_OVERRIDES = dict(
    tools=["WebFetch", "Read"],
    egress=["docs.python.org"],
    budget={"steps": 10, "wall_clock_seconds": 120, "tokens": 50000, "usd": 0.5},
    approval_tier={"auto": ["Read"], "human_approval": ["delete_record"]},
    data={"class": "pii", "retention_days": 7, "redact": ["token"]},
    credentials=[{"name": "db", "ref": "vault://x", "scope": "read-only"}],
    extensions={"human_verification": {"expected": True, "note": "x"}},
)


def _charter(good_charter, **overrides):
    charter = copy.deepcopy(good_charter)
    for key, value in overrides.items():
        charter[key] = value
    return charter


def _assert_total(rows):
    fields = [field for _, field, _ in rows]
    for required in REQUIRED_FIELDS:
        assert any(f == required or f.startswith(required + ".") for f in fields), (
            f"no row for required field {required!r} — fields present: {fields}"
        )


# --- 1. Totality -------------------------------------------------------------


def test_totality_good_charter(good_charter):
    _assert_total(report(good_charter))


def test_totality_fully_populated_charter(good_charter):
    charter = _charter(good_charter, **FULLY_POPULATED_OVERRIDES)
    _assert_total(report(charter))


# --- 2. Credentials truthfulness ---------------------------------------------


def test_credentials_rows_are_honest(good_charter):
    rows = report(good_charter)

    env_row = next(r for r in rows if r[1] == "credentials.env")
    assert env_row[0] == "block"
    assert "dropped" in env_row[2]

    fs_row = next(r for r in rows if r[1] == "credentials.fs")
    assert fs_row[0] == "none"
    assert "filesystem" in fs_row[2]
    assert "container" in fs_row[2]

    assert not any("full host environment" in note for _, _, note in rows)


# --- 3. budget.tokens ---------------------------------------------------------


def test_budget_tokens_not_enforced_when_set(good_charter):
    charter = _charter(good_charter, budget={"steps": 1, "wall_clock_seconds": 60, "tokens": 50000})
    rows = report(charter)
    row = next(r for r in rows if r[1] == "budget.tokens")
    assert row[0] == "none"


def test_budget_tokens_absent_when_not_set(good_charter):
    charter = _charter(good_charter, budget={"steps": 1, "wall_clock_seconds": 60})
    rows = report(charter)
    assert not any(r[1] == "budget.tokens" for r in rows)


# --- 4. approval_tier ----------------------------------------------------------


def test_approval_tier_not_enforced_with_human_approval(good_charter):
    charter = _charter(good_charter, approval_tier={"auto": [], "human_approval": ["delete_record"]})
    rows = report(charter)
    row = next(r for r in rows if r[1] == "approval_tier")
    assert row[0] == "none"


def test_approval_tier_declared_without_human_approval(good_charter):
    charter = _charter(good_charter, approval_tier={"auto": [], "human_approval": []})
    rows = report(charter)
    row = next(r for r in rows if r[1] == "approval_tier")
    assert row[0] == "declared"


# --- 5. data.redact/retention ---------------------------------------------------


def test_data_redact_retention_not_enforced(good_charter):
    rows = report(good_charter)
    matches = [r for r in rows if r[1].startswith("data") and r[0] == "none"]
    assert len(matches) == 1
    note = matches[0][2].lower()
    assert "redact" in note
    assert "auto-deleted" in note


# --- 6. status is a block --------------------------------------------------------


def test_status_is_block(good_charter):
    rows = report(good_charter)
    row = next(r for r in rows if r[1] == "status")
    assert row[0] == "block"


# --- 7. extensions ---------------------------------------------------------------


def test_extensions_row_present_when_declared(good_charter):
    charter = _charter(good_charter, extensions={"human_verification": {"expected": True, "note": "x"}})
    rows = report(charter)
    assert any(r[1] == "extensions" for r in rows)


def test_extensions_row_absent_when_not_declared(good_charter):
    rows = report(good_charter)
    assert not any(r[1] == "extensions" for r in rows)


# --- 8. egress rows preserved ------------------------------------------------------


def test_egress_none_is_block(good_charter):
    charter = _charter(good_charter, egress=["none"])
    rows = report(charter)
    row = next(r for r in rows if r[1] == "egress")
    assert row[0] == "block"
    assert "no network permitted" in row[2]


def test_egress_domain_list_with_webfetch_is_wall(good_charter):
    charter = _charter(good_charter, tools=["WebFetch"], egress=["docs.python.org"])
    rows = report(charter)
    row = next(r for r in rows if r[1] == "egress")
    assert row[0] == "block"


def test_egress_any_is_none(good_charter):
    charter = _charter(good_charter, egress=["any"])
    rows = report(charter)
    row = next(r for r in rows if r[1] == "egress")
    assert row[0] == "none"
