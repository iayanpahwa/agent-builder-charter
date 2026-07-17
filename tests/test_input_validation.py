"""Tests for the input-validation hardening on this branch.

Covers three surfaces:
  1. Loader egress-entry validation (core/loader.py `validate`) — malformed egress
     entries (empty string, bare wildcard, mid-token '*', trailing dot, leading dot,
     double dot) are rejected with CharterInvalid.
  2. Schema patterns enforced via `validate` — `tools[]` items must match
     `^[A-Za-z0-9_.*-]+$` (no commas/spaces) and `mcp[].name` must match
     `^[A-Za-z0-9_.-]+$`.
  3. cc_guard.host_allowed direct unit tests — the old `lstrip("*.")` bypass (which
     let a trailing-dot host slip through) is closed.
"""

import copy
import sys

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "builders" / "claude-headless"))
from loader import validate, CharterInvalid
from cc_guard import host_allowed

# --- 1. Egress: malformed entries are rejected -----------------------------

MALFORMED_EGRESS_ENTRIES = ["", "*.", "a*b.com", "evil.com.", ".x", "a..b"]


@pytest.mark.parametrize("entry", MALFORMED_EGRESS_ENTRIES)
def test_egress_malformed_entry_raises(good_charter, entry):
    good_charter["egress"] = [entry]
    with pytest.raises(CharterInvalid):
        validate(good_charter)


# --- 2. Egress: real domains/wildcards/localhost/sentinels are accepted ----

VALID_EGRESS_LISTS = [
    ["docs.python.org"],
    ["*.example.com"],
    ["localhost"],
    ["raw.githubusercontent.com"],
    ["any"],
    ["none"],
]


@pytest.mark.parametrize("egress", VALID_EGRESS_LISTS, ids=lambda e: e[0])
def test_egress_valid_entry_does_not_raise(good_charter, egress):
    good_charter["egress"] = egress
    validate(good_charter)  # must not raise


# --- 3. Schema: tools[] items must match ^[A-Za-z0-9_.*-]+$ ----------------


@pytest.mark.parametrize(
    "tools",
    [
        pytest.param(["WebFetch,Bash"], id="comma_joined"),
        pytest.param(["Bash Read"], id="space_joined"),
    ],
)
def test_tools_invalid_pattern_raises(good_charter, tools):
    good_charter["tools"] = tools
    with pytest.raises(CharterInvalid):
        validate(good_charter)


@pytest.mark.parametrize(
    "tools",
    [
        pytest.param(["WebFetch", "Bash"], id="separate_items"),
        pytest.param(["mcp__srv__tool"], id="mcp_tool_name"),
    ],
)
def test_tools_valid_pattern_does_not_raise(good_charter, tools):
    good_charter["tools"] = tools
    validate(good_charter)  # must not raise


# --- 4. Schema: mcp[].name must match ^[A-Za-z0-9_.-]+$ --------------------


def test_mcp_invalid_name_raises(good_charter):
    good_charter["mcp"] = [
        {
            "name": "bad,name",
            "server": {"type": "stdio", "command": "c", "args": []},
            "allow": ["*"],
        }
    ]
    with pytest.raises(CharterInvalid):
        validate(good_charter)


def test_mcp_valid_name_does_not_raise(good_charter):
    good_charter["mcp"] = [
        {
            "name": "good-srv",
            "server": {"type": "stdio", "command": "c", "args": []},
            "allow": ["*"],
        }
    ]
    validate(good_charter)  # must not raise


# --- 5. cc_guard.host_allowed: the lstrip("*.") bypass is closed -----------


def test_host_allowed_trailing_dot_bare_star_denied():
    assert host_allowed("evil.com.", ["*."]) is False


def test_host_allowed_trailing_dot_empty_pattern_denied():
    assert host_allowed("evil.com.", [""]) is False


# --- 6. cc_guard.host_allowed: sanity cases ---------------------------------


@pytest.mark.parametrize(
    "host,egress,expected",
    [
        pytest.param("a.example.com", ["*.example.com"], True, id="subdomain_matches_wildcard"),
        pytest.param("example.com", ["*.example.com"], True, id="bare_domain_matches_wildcard"),
        pytest.param("docs.python.org", ["docs.python.org"], True, id="exact_match"),
        pytest.param("evil.com", ["docs.python.org"], False, id="off_list_denied"),
    ],
)
def test_host_allowed_sanity(host, egress, expected):
    assert host_allowed(host, egress) is expected
