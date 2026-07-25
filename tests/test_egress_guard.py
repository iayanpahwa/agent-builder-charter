"""Tests for egress_guard.py's runtime egress decisions and run_headless.py's dry-run report.

egress_guard only inspects the URL string in the tool event — it never fetches anything, so
these tests run via subprocess with no tokens and no real network calls.
"""

import copy
import json
import os
import subprocess
import sys

import yaml
import pytest

from conftest import REPO_ROOT

EGRESS_GUARD = REPO_ROOT / "builders" / "claude-headless" / "egress_guard.py"
RUN_HEADLESS = REPO_ROOT / "builders" / "claude-headless" / "run_headless.py"


def _write_charter(tmp_path, good_charter, *, tools, egress):
    charter = copy.deepcopy(good_charter)
    charter["tools"] = tools
    charter["egress"] = egress
    charter_file = tmp_path / "charter.yaml"
    charter_file.write_text(yaml.safe_dump(charter))
    # The runner fails closed on a missing trusted source, so materialize each one.
    for src in (charter.get("context") or {}).get("trusted_sources", []) or []:
        f = tmp_path / src
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("test system prompt\n")
    return charter_file


def _run_guard(charter_path, event):
    env = dict(os.environ, _ZO_DOCTOR="0")
    result = subprocess.run(
        [sys.executable, str(EGRESS_GUARD), "--charter", str(charter_path)],
        cwd=str(REPO_ROOT),
        env=env,
        input=json.dumps(event),
        capture_output=True,
        text=True,
    )
    decision = json.loads(result.stdout)
    return decision["hookSpecificOutput"]["permissionDecision"]


# --- 1. egress_guard egress decisions -------------------------------------------


@pytest.mark.parametrize(
    "egress,url,expected",
    [
        pytest.param(["none"], "https://docs.python.org/3/", "deny", id="none_denies_any_url"),
        pytest.param(["any"], "https://docs.python.org/3/", "allow", id="any_allows"),
        pytest.param(
            ["docs.python.org"], "https://docs.python.org/3/", "allow", id="on_list_allowed"
        ),
        pytest.param(
            ["docs.python.org"], "https://evil.example.com/", "deny", id="off_list_denied"
        ),
    ],
)
def test_webfetch_egress_decision(tmp_path, good_charter, egress, url, expected):
    charter_path = _write_charter(tmp_path, good_charter, tools=["WebFetch"], egress=egress)
    event = {"tool_name": "WebFetch", "tool_input": {"url": url}}
    assert _run_guard(charter_path, event) == expected


def test_non_network_tool_always_allowed(tmp_path, good_charter):
    # egress [none] would deny any network tool, but a non-network tool is never gated.
    charter_path = _write_charter(tmp_path, good_charter, tools=["WebFetch"], egress=["none"])
    event = {"tool_name": "Read", "tool_input": {}}
    assert _run_guard(charter_path, event) == "allow"


@pytest.mark.parametrize(
    "egress,expected",
    [
        pytest.param(["docs.python.org"], "deny", id="scoped_denies_websearch"),
        pytest.param(["any"], "allow", id="any_allows_websearch"),
        pytest.param(["none"], "deny", id="none_denies_websearch"),
    ],
)
def test_websearch_egress_decision(tmp_path, good_charter, egress, expected):
    # WebSearch's tool_input has "query", not "url" — it can't be confined to a host list.
    charter_path = _write_charter(tmp_path, good_charter, tools=["WebSearch"], egress=egress)
    event = {"tool_name": "WebSearch", "tool_input": {"query": "x"}}
    assert _run_guard(charter_path, event) == expected


# --- 2. run_headless.py --dry-run report -----------------------------------


def test_dry_run_reports_none_block_and_settings_hook(tmp_path, good_charter):
    charter_path = _write_charter(tmp_path, good_charter, tools=["WebFetch"], egress=["none"])
    env = dict(os.environ, _ZO_DOCTOR="0")
    result = subprocess.run(
        [
            sys.executable,
            str(RUN_HEADLESS),
            "--charter",
            str(charter_path),
            "--dry-run",
            "--prompt",
            "x",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    egress_line = next(line for line in result.stdout.splitlines() if "egress" in line)
    assert "block" in egress_line
    assert "denied" in egress_line
    assert "--settings" in result.stdout
