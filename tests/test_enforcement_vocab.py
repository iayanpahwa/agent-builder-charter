"""Tests for the CHARTER enforcement vocabulary — the enum shared by
core/charter.schema.yaml's `x-enforced` tags and run_headless.py's report().

These tests lock in three guarantees:
  A. Schema lint    — every x-enforced tag in the schema uses a known token
                       (catches typos / free-text drift).
  B. Vocabulary      — every status report() emits is a known token.
  C. Coverage        — every field the schema tags x-enforced: block gets an
                       actual row from report() on a fully-populated charter
                       (catches "added a block field, forgot the report row").

No tokens, no network, no subprocess — report() and validate() are pure and
called directly.
"""

import copy
import sys

import yaml

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "builders" / "claude-headless"))
from run_headless import report  # noqa: E402
from loader import validate  # noqa: E402

SCHEMA_PATH = REPO_ROOT / "core" / "charter.schema.yaml"

ENUM = {"block", "gate", "alarm", "present", "declared", "none"}
DISPLAY_ONLY = {"—"}  # a report marker for a field that isn't set — not an enforcement class

FULLY_POPULATED_OVERRIDES = dict(
    tools=["WebFetch", "Read"],
    egress=["docs.python.org"],
    budget={"steps": 10, "wall_clock_seconds": 120, "tokens": 50000, "usd": 0.5},
    approval_tier={"auto": ["Read"], "human_approval": ["delete_record"]},
    data={"class": "pii", "retention_days": 7, "redact": ["token"]},
    credentials=[{"name": "db", "ref": "vault://x", "scope": "read-only"}],
    extensions={"human_verification": {"expected": True, "note": "x"}},
    mcp=[
        {
            "name": "docs",
            "server": {"type": "stdio", "command": "npx", "args": ["-y", "docs-mcp-server"]},
            "allow": ["*"],
        }
    ],
    skills=["new-agent"],
)


def _charter(good_charter, **overrides):
    charter = copy.deepcopy(good_charter)
    for key, value in overrides.items():
        charter[key] = value
    return charter


def _as_list(value):
    return value if isinstance(value, list) else [value]


def _find_x_enforced(node):
    """Recursively yield every x-enforced value found anywhere in the schema tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "x-enforced":
                yield value
            else:
                yield from _find_x_enforced(value)
    elif isinstance(node, list):
        for item in node:
            yield from _find_x_enforced(item)


# --- A. schema lint ----------------------------------------------------------


def test_schema_x_enforced_tokens_are_in_enum():
    with open(SCHEMA_PATH) as f:
        schema = yaml.safe_load(f)
    values = list(_find_x_enforced(schema))
    assert values, "expected to find x-enforced tags in the schema"
    for value in values:
        for token in _as_list(value):
            assert token in ENUM, f"unknown x-enforced token: {token!r}"


# --- B. report vocabulary conformance -----------------------------------------


def test_report_rows_use_known_vocabulary(good_charter):
    charter = _charter(good_charter, **FULLY_POPULATED_OVERRIDES)
    validate(charter)  # must be a VALID charter
    rows = report(charter)
    for status, field, _note in rows:
        assert status in ENUM | DISPLAY_ONLY, f"{field!r} row has unknown status {status!r}"


# --- C. reconciliation / coverage ---------------------------------------------


def test_block_tagged_fields_all_have_report_rows(good_charter):
    with open(SCHEMA_PATH) as f:
        schema = yaml.safe_load(f)
    block_fields = [
        name
        for name, spec in schema["properties"].items()
        if "block" in _as_list(spec.get("x-enforced"))
    ]
    assert block_fields, "expected at least one x-enforced: block field in the schema"

    charter = _charter(good_charter, **FULLY_POPULATED_OVERRIDES)
    validate(charter)  # must be a VALID charter
    rows = report(charter)
    fields = [field for _, field, _ in rows]
    for block_field in block_fields:
        assert any(
            f == block_field or f.startswith(block_field + ".") for f in fields
        ), f"no report row for block-tagged field {block_field!r} — fields present: {fields}"
