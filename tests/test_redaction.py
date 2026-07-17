"""Tests for run_headless.py's log redaction + retention pruning.

_redact(text, patterns, secret_values) scrubs declared regex patterns (falling back to
a literal replace if a pattern won't compile) and declared secret values (len >= 4)
from text before it is persisted.

_declared_secret_values(charter) returns the values of the charter's declared `env:`
credentials that are actually set in the host environment.

prune_logs(charter_dir, retention_days) deletes *.output.txt files older than the
retention window and rewrites runs.jsonl dropping entries whose ISO `timestamp` falls
outside the window (unparseable lines are kept, since we'd rather keep than lose data).

No tokens, no network, no subprocess — all three are pure and called directly.
"""

import json
import os
import sys
import time

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "builders" / "claude-headless"))
from run_headless import _redact, _declared_secret_values, prune_logs, _logdir  # noqa: E402


# --- _redact -----------------------------------------------------------------


def test_redact_regex_pattern_matches():
    assert _redact("to a@b.com!", [r"[\w.]+@[\w.]+"], []) == "to [REDACTED]!"


def test_redact_invalid_regex_falls_back_to_literal():
    assert _redact("cost (x)", ["("], []) == "cost [REDACTED]x)"


def test_redact_secret_value_replaced():
    assert _redact("k=supersecret;", [], ["supersecret"]) == "k=[REDACTED];"


def test_redact_short_secret_not_replaced():
    assert _redact("ab ab", [], ["ab"]) == "ab ab"


def test_redact_empty_text_unchanged():
    assert _redact("", ["x"], ["y"]) == ""


def test_redact_no_patterns_no_secrets_unchanged():
    assert _redact("hello", [], []) == "hello"


# --- _declared_secret_values --------------------------------------------------


def test_declared_secret_values_env_ref_set(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "value123")
    charter = {"credentials": [{"name": "s", "ref": "env:MY_SECRET", "scope": "ro"}]}
    assert _declared_secret_values(charter) == ["value123"]


def test_declared_secret_values_vault_ref_not_returned():
    charter = {"credentials": [{"name": "v", "ref": "vault://db", "scope": "ro"}]}
    assert _declared_secret_values(charter) == []


def test_declared_secret_values_unset_env_ref_not_returned(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    charter = {"credentials": [{"name": "n", "ref": "env:NOPE", "scope": "ro"}]}
    assert _declared_secret_values(charter) == []


def test_declared_secret_values_empty_or_absent_credentials():
    assert _declared_secret_values({}) == []
    assert _declared_secret_values({"credentials": []}) == []


# --- prune_logs ----------------------------------------------------------------


def test_prune_logs_deletes_old_output_files_keeps_fresh(tmp_path):
    logs_dir = _logdir(str(tmp_path))
    old_path = os.path.join(logs_dir, "old.output.txt")
    fresh_path = os.path.join(logs_dir, "fresh.output.txt")
    with open(old_path, "w") as f:
        f.write("old")
    with open(fresh_path, "w") as f:
        f.write("fresh")
    old_time = time.time() - 30 * 86400
    os.utime(old_path, (old_time, old_time))

    prune_logs(str(tmp_path), 7)

    assert not os.path.exists(old_path)
    assert os.path.exists(fresh_path)


def test_prune_logs_trims_runs_jsonl_keeps_recent_and_unparseable(tmp_path):
    logs_dir = _logdir(str(tmp_path))
    runs_path = os.path.join(logs_dir, "runs.jsonl")
    with open(runs_path, "w") as f:
        f.write(json.dumps({"timestamp": "2020-01-01T00:00:00"}) + "\n")
        f.write(json.dumps({"timestamp": "2099-01-01T00:00:00"}) + "\n")
        f.write("garbage\n")

    prune_logs(str(tmp_path), 7)

    with open(runs_path) as f:
        lines = [line.rstrip("\n") for line in f if line.strip()]
    assert not any("2020-01-01" in line for line in lines)
    assert any("2099-01-01" in line for line in lines)
    assert "garbage" in lines


def test_prune_logs_noop_when_retention_days_falsy(tmp_path):
    logs_dir = _logdir(str(tmp_path))
    old_path = os.path.join(logs_dir, "old.output.txt")
    with open(old_path, "w") as f:
        f.write("old")
    old_time = time.time() - 30 * 86400
    os.utime(old_path, (old_time, old_time))

    prune_logs(str(tmp_path), 0)
    assert os.path.exists(old_path)

    prune_logs(str(tmp_path), None)
    assert os.path.exists(old_path)


def test_prune_logs_missing_runs_jsonl_and_empty_dir_no_crash(tmp_path):
    _logdir(str(tmp_path))  # creates an empty logs dir, no runs.jsonl
    prune_logs(str(tmp_path), 7)  # must not raise
