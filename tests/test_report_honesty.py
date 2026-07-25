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
from run_headless import report

REQUIRED_FIELDS = [
    "status",
    "context",
    "model",
    "sandbox",
    "budget",
    "tools",
    "credentials",
    "egress",
    "approval_tier",
    "data",
    "evals",
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
        assert any(
            f == required or f.startswith(required + ".") for f in fields
        ), f"no row for required field {required!r} — fields present: {fields}"


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
    charter = _charter(
        good_charter, approval_tier={"auto": [], "human_approval": ["delete_record"]}
    )
    rows = report(charter)
    row = next(r for r in rows if r[1] == "approval_tier")
    assert row[0] == "none"


def test_approval_tier_declared_without_human_approval(good_charter):
    charter = _charter(good_charter, approval_tier={"auto": [], "human_approval": []})
    rows = report(charter)
    row = next(r for r in rows if r[1] == "approval_tier")
    assert row[0] == "declared"


# --- 5. data.redact/retention ---------------------------------------------------


def test_data_redact_and_retention_rows(good_charter):
    # redact patterns present -> data.redact is "block"
    charter = _charter(good_charter, data={"class": "pii", "redact": ["token"]})
    rows = report(charter)
    row = next(r for r in rows if r[1] == "data.redact")
    assert row[0] == "block"

    # no redact patterns and no env: credentials -> data.redact is "declared"
    charter = _charter(good_charter, data={"class": "internal"}, credentials=[])
    rows = report(charter)
    row = next(r for r in rows if r[1] == "data.redact")
    assert row[0] == "declared"

    # retention_days set -> data.retention is "block"
    charter = _charter(good_charter, data={"class": "pii", "retention_days": 7})
    rows = report(charter)
    row = next(r for r in rows if r[1] == "data.retention")
    assert row[0] == "block"

    # no retention_days -> data.retention is "declared"
    charter = _charter(good_charter, data={"class": "pii"})
    rows = report(charter)
    row = next(r for r in rows if r[1] == "data.retention")
    assert row[0] == "declared"

    # env: credential alone (no redact patterns) -> data.redact is "block"
    charter = _charter(
        good_charter,
        data={"class": "pii"},
        credentials=[{"name": "k", "ref": "env:X", "scope": "ro"}],
    )
    rows = report(charter)
    row = next(r for r in rows if r[1] == "data.redact")
    assert row[0] == "block"


# --- 6. status is a block --------------------------------------------------------


def test_status_is_block(good_charter):
    rows = report(good_charter)
    row = next(r for r in rows if r[1] == "status")
    assert row[0] == "block"


# --- 7. extensions ---------------------------------------------------------------


def test_extensions_row_present_when_declared(good_charter):
    charter = _charter(
        good_charter, extensions={"human_verification": {"expected": True, "note": "x"}}
    )
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
    assert "denied" in row[2]
    assert "no network" in row[2].lower()


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


def test_egress_scoped_list_notes_websearch_denied(good_charter):
    charter = _charter(good_charter, tools=["WebFetch"], egress=["docs.python.org"])
    rows = report(charter)
    row = next(r for r in rows if r[1] == "egress")
    assert row[0] == "block"
    assert "WebSearch denied" in row[2]


# --- 9. egress.other — Bash/MCP escape network egress entirely ------------------


def test_bash_without_bash_allow_is_reported_as_denied_not_as_an_escape(good_charter):
    """This used to be an `egress.other` row saying Bash reached the network outside the hook and
    only a container could fence it. That was honest but unfixable. Bash is now gated by the same
    hook: with no bash_allow declared, every command is denied, so the row says the field is a
    block and the tool is unusable rather than that the wall has a hole in it."""
    charter = _charter(good_charter, tools=["Bash"], egress=["none"])
    rows = report(charter)
    assert not any(r[1] == "egress.other" for r in rows), "Bash is no longer an unfenced escape"
    row = next(r for r in rows if r[1] == "bash_allow")
    assert row[0] == "block"
    assert "denies EVERY command" in row[2]


def test_bash_with_bash_allow_names_the_permitted_endpoints(good_charter):
    """A reader has to be able to see, from the report alone, exactly where a granted Bash can
    reach — otherwise the field is a claim rather than a description."""
    charter = _charter(
        good_charter,
        tools=["Bash"],
        egress=["none"],
        bash_allow=[{"host": "api.example.com", "path": "/search", "methods": ["GET"]}],
    )
    rows = report(charter)
    row = next(r for r in rows if r[1] == "bash_allow")
    assert row[0] == "block"
    assert "api.example.com/search" in row[2]
    assert "not a sandbox" in row[2] or "still unisolated" in row[2]


def test_no_bash_allow_row_when_bash_is_not_granted(good_charter):
    """The field is meaningless without the tool; a row for it would be noise."""
    charter = _charter(good_charter, tools=["WebFetch"], egress=["docs.python.org"])
    assert not any(r[1] == "bash_allow" for r in report(charter))


def test_egress_other_row_absent_without_bash_or_mcp(good_charter):
    charter = _charter(good_charter, tools=["WebFetch"], egress=["docs.python.org"])
    rows = report(charter)
    assert not any(r[1] == "egress.other" for r in rows)


def test_egress_other_row_present_with_mcp(good_charter):
    charter = _charter(
        good_charter,
        mcp=[
            {"name": "x", "server": {"type": "stdio", "command": "c", "args": []}, "allow": ["*"]}
        ],
    )
    rows = report(charter)
    row = next(r for r in rows if r[1] == "egress.other")
    assert row[0] == "none"
    assert "MCP" in row[2]
