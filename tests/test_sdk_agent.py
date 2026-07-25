"""Tests for builders/claude-sdk/example.agent.py's inlined enforcement helpers.

The agent's filename has a dot (example.agent.py) so a normal `import` statement can't
name it — it's loaded via importlib.util.spec_from_file_location, same effect as
`python3 builders/claude-sdk/example.agent.py`. None of these tests need
claude-agent-sdk installed: importing the module, scoped_env, egress_decision, and
enforcement_report are all pure or lazily-imported by design (see the module's own
docstring on why the SDK import lives inside run(), not at module scope).

No tokens, no network, no subprocess execution of the SDK.
"""

import importlib.util
import subprocess
import sys

import yaml

from conftest import REPO_ROOT

AGENT_PATH = REPO_ROOT / "builders" / "claude-sdk" / "example.agent.py"

_spec = importlib.util.spec_from_file_location("sdk_example_agent", AGENT_PATH)
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)


def test_module_import_does_not_pull_in_the_sdk():
    assert "claude_agent_sdk" not in sys.modules


# --- scoped_env: auth-mode-aware cross-shadowing guard -----------------------


def test_api_key_mode_keeps_key_drops_oauth_token():
    # Both an API key and a stray OAuth token are present; the charter's declared
    # credential is env:ANTHROPIC_API_KEY, so api-key mode wins and the OAuth token
    # (which would otherwise ride along via the CLAUDE_ prefix) must be dropped.
    src = {
        "ANTHROPIC_API_KEY": "sk-ant-real",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth-tok",
        "PATH": "/usr/bin",
        "HOME": "/home/x",
    }
    result = agent.scoped_env(agent.CHARTER, src=src)
    assert result["ANTHROPIC_API_KEY"] == "sk-ant-real"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in result


def test_subscription_mode_keeps_oauth_token_drops_api_key():
    charter = dict(agent.CHARTER)
    charter["credentials"] = [
        {"name": "sub-auth", "ref": "env:CLAUDE_CODE_OAUTH_TOKEN", "scope": "subscription"}
    ]
    # A stray ANTHROPIC_API_KEY would otherwise silently outrank the OAuth token —
    # the real footgun scoped_env exists to close.
    src = {
        "ANTHROPIC_API_KEY": "sk-ant-stray",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth-tok",
        "PATH": "/usr/bin",
        "HOME": "/home/x",
    }
    result = agent.scoped_env(charter, src=src)
    assert result["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-tok"
    assert "ANTHROPIC_API_KEY" not in result


def test_config_dir_tilde_expanded():
    src = {"HOME": "/home/x", "CLAUDE_CONFIG_DIR": "~/.claude-zyte-work", "ANTHROPIC_API_KEY": "k"}
    result = agent.scoped_env(agent.CHARTER, src=src)
    assert result["CLAUDE_CONFIG_DIR"] == "/home/x/.claude-zyte-work"
    assert not result["CLAUDE_CONFIG_DIR"].startswith("~")


def test_ambient_non_essential_secret_dropped():
    src = {"OPENROUTER_API_KEY": "leak", "PATH": "/usr/bin", "ANTHROPIC_API_KEY": "k"}
    result = agent.scoped_env(agent.CHARTER, src=src)
    assert "OPENROUTER_API_KEY" not in result


# --- egress_decision: mirrors builders/claude-headless/egress_guard.py's host-match rule ----


def test_egress_allows_webfetch_to_allowlisted_host():
    allow, _ = agent.egress_decision(
        "WebFetch", {"url": "https://docs.python.org/3/"}, ["docs.python.org"]
    )
    assert allow


def test_egress_denies_webfetch_to_other_host():
    allow, _ = agent.egress_decision(
        "WebFetch", {"url": "https://evil.example.com/"}, ["docs.python.org"]
    )
    assert not allow


def test_egress_denies_lookalike_prefix_host():
    # 'evildocs.python.org' shares a string suffix with 'docs.python.org' but is not a
    # subdomain of it (no dot before 'docs') — the dot-suffix rule must reject it.
    allow, _ = agent.egress_decision(
        "WebFetch", {"url": "https://evildocs.python.org/"}, ["docs.python.org"]
    )
    assert not allow


def test_egress_allows_real_subdomain():
    allow, _ = agent.egress_decision(
        "WebFetch", {"url": "https://x.docs.python.org/"}, ["docs.python.org"]
    )
    assert allow


def test_egress_denies_websearch_under_scoped_list():
    allow, _ = agent.egress_decision("WebSearch", {}, ["docs.python.org"])
    assert not allow


def test_egress_allows_websearch_under_any():
    allow, _ = agent.egress_decision("WebSearch", {}, ["any"])
    assert allow


# --- observability: opt-in ergonomics, off by default ----------------------


def test_observability_defaults_off():
    # no extensions block at all -> both off (a quiet agent is the safe default)
    assert agent.observability({}) == {"stream": False, "trace": False}


def test_observability_reads_extensions():
    charter = {"extensions": {"observability": {"stream": True, "trace": False}}}
    assert agent.observability(charter) == {"stream": True, "trace": False}


def test_trace_line_redacts_secret_values():
    line = agent.trace_line(
        "WebFetch", {"url": "https://docs.python.org/?k=sk-SECRET123"}, [], ["sk-SECRET123"]
    )
    assert "sk-SECRET123" not in line
    assert "[REDACTED]" in line
    assert "WebFetch" in line


# --- enforcement_report: the --dry-run honest report, no SDK import ---------


def test_enforcement_report_is_honest_and_does_not_import_the_sdk():
    report = agent.enforcement_report(agent.CHARTER)
    assert isinstance(report, str)
    assert "block" in report
    assert "none" in report
    assert "WebSearch" in report
    assert "api-key" in report  # CHARTER's declared auth mode (env:ANTHROPIC_API_KEY)
    assert "claude_agent_sdk" not in sys.modules


# --- the embedded CHARTER validates against core/charter.schema.yaml --------


def test_embedded_charter_validates(tmp_path):
    charter_file = tmp_path / "sdk-docs-researcher.charter.yaml"
    charter_file.write_text(yaml.safe_dump(agent.CHARTER, sort_keys=False))

    result = subprocess.run(
        [sys.executable, "core/validate.py", str(charter_file)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "VALID" in result.stdout
