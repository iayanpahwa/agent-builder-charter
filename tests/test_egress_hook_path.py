"""Regression tests for the egress_guard hook's charter path in run_headless.py's _settings_file.

Bug: `_settings_file` used to hardcode the hook's --charter argument as
`<charter_dir>/charter.yaml`, regardless of what the charter file was actually named. A charter
saved under any other name got the hook wired to a nonexistent path, egress_guard's `load_charter`
raised, and the guard failed closed -- denying ALL web fetches, even ones the charter's egress
list explicitly allowed.

The fix threads the real charter path through: `_settings_file(charter, charter_path)` now
writes `charter_path` (not a fabricated `charter.yaml` join) into the hook command.

No tokens, no real network -- egress_guard only parses the URL string in the tool event.
"""

import copy
import json
import os
import shlex
import subprocess
import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "builders" / "claude-headless"))
from run_headless import _settings_file  # noqa: E402

EGRESS_GUARD = REPO_ROOT / "builders" / "claude-headless" / "egress_guard.py"


def _hook_charter_arg(settings_file_path):
    """Read a --settings file and return the --charter token from its PreToolUse hook command."""
    with open(settings_file_path) as f:
        settings = json.load(f)
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    tokens = shlex.split(command)
    return tokens[tokens.index("--charter") + 1]


def _run_guard(argv, url):
    env = dict(os.environ, _ZO_DOCTOR="0")
    event = {"tool_name": "WebFetch", "tool_input": {"url": url}}
    result = subprocess.run(
        argv,
        cwd=str(REPO_ROOT),
        env=env,
        input=json.dumps(event),
        capture_output=True,
        text=True,
    )
    decision = json.loads(result.stdout)
    return decision["hookSpecificOutput"]["permissionDecision"]


def _wired_charter(good_charter):
    charter = copy.deepcopy(good_charter)
    charter["tools"] = ["WebFetch"]
    charter["egress"] = ["docs.python.org"]
    return charter


def test_hook_points_at_given_path_for_differently_named_charter(tmp_path, good_charter):
    charter = _wired_charter(good_charter)
    charter_file = tmp_path / "my-agent.charter.yaml"
    charter_file.write_text(json.dumps(charter))

    sf = _settings_file(charter, str(charter_file.resolve()))
    try:
        charter_arg = _hook_charter_arg(sf)
        assert charter_arg == str(charter_file.resolve())
        assert os.path.exists(charter_arg)
        assert not charter_arg.endswith("/charter.yaml")
        assert os.path.basename(charter_arg) == "my-agent.charter.yaml"
    finally:
        os.remove(sf)


def test_end_to_end_chain_catches_the_regression(tmp_path, good_charter):
    charter = _wired_charter(good_charter)
    charter_file = tmp_path / "my-agent.charter.yaml"
    charter_file.write_text(json.dumps(charter))

    sf = _settings_file(charter, str(charter_file.resolve()))
    try:
        with open(sf) as f:
            settings = json.load(f)
        command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        argv = shlex.split(command)

        # Before the fix, the hook's --charter path was bogus (a nonexistent charter.yaml next
        # to a differently-named file), so egress_guard's load_charter raised and it failed closed --
        # this on-list URL would come back "deny" instead of "allow".
        assert _run_guard(argv, "https://docs.python.org/3/") == "allow"
        assert _run_guard(argv, "https://evil.example.com/") == "deny"
    finally:
        os.remove(sf)


def test_hook_still_works_for_literally_named_charter_yaml(tmp_path, good_charter):
    charter = _wired_charter(good_charter)
    charter_file = tmp_path / "charter.yaml"
    charter_file.write_text(json.dumps(charter))

    sf = _settings_file(charter, str(charter_file.resolve()))
    try:
        charter_arg = _hook_charter_arg(sf)
        assert charter_arg == str(charter_file.resolve())
        assert os.path.exists(charter_arg)
    finally:
        os.remove(sf)


def test_no_hook_wired_when_not_needed(tmp_path, good_charter):
    charter = copy.deepcopy(good_charter)
    charter["tools"] = []
    charter["egress"] = ["none"]
    charter.pop("skills", None)

    sf = _settings_file(charter, str(tmp_path / "charter.yaml"))
    assert sf is None
