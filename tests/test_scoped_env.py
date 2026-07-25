"""Tests for run_headless.py's scoped_env() — the subprocess environment builder.

scoped_env(charter) must build the subprocess environment from OS essentials +
Claude auth (CLAUDE_*/ANTHROPIC_*/LC_* prefixes) + the charter's declared `env:`
credential refs — and nothing else. Every other host secret must be dropped.

These tests control os.environ deterministically via monkeypatch; they never rely
on the ambient environment.

No tokens, no network, no subprocess — scoped_env() is pure and called directly.
"""

import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "builders" / "claude-headless"))
from run_headless import scoped_env, _ESSENTIAL_VARS, _ESSENTIAL_PREFIXES  # noqa: E402


def test_ambient_secret_dropped(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "leak")
    assert "OPENROUTER_API_KEY" not in scoped_env({})


def test_os_essentials_kept(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/x")
    result = scoped_env({})
    assert result["PATH"] == "/usr/bin"
    assert result["HOME"] == "/home/x"


def test_claude_auth_kept_via_prefixes(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/cfg")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    result = scoped_env({})
    assert result["CLAUDE_CONFIG_DIR"] == "/cfg"
    assert result["ANTHROPIC_API_KEY"] == "k"
    assert result["ANTHROPIC_AUTH_TOKEN"] == "t"


def test_config_dir_tilde_expanded(monkeypatch):
    # A subprocess doesn't expand a leading '~'; forwarding it verbatim makes the child
    # claude create a literal './~/...' dir under its cwd. scoped_env must expand it.
    monkeypatch.setenv("HOME", "/home/x")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/.claude-zyte-work")
    result = scoped_env({})
    assert result["CLAUDE_CONFIG_DIR"] == "/home/x/.claude-zyte-work"
    assert not result["CLAUDE_CONFIG_DIR"].startswith("~")


def test_absolute_config_dir_left_alone(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/cfg")
    assert scoped_env({})["CLAUDE_CONFIG_DIR"] == "/cfg"


def test_declared_env_ref_passed_through(monkeypatch):
    monkeypatch.setenv("MY_DB_PW", "secret")
    charter = {"credentials": [{"name": "db", "ref": "env:MY_DB_PW", "scope": "ro"}]}
    result = scoped_env(charter)
    assert result["MY_DB_PW"] == "secret"


def test_vault_ref_not_injected(monkeypatch):
    charter = {"credentials": [{"name": "v", "ref": "vault://db", "scope": "ro"}]}
    result = scoped_env(charter)
    assert "db" not in result
    assert "vault://db" not in result.values()


def test_env_ref_naming_unset_var(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    charter = {"credentials": [{"name": "n", "ref": "env:NOPE", "scope": "ro"}]}
    result = scoped_env(charter)
    assert "NOPE" not in result


def test_charter_without_credentials_key():
    result = scoped_env({})
    assert isinstance(result, dict)


def test_no_stray_keys_leak(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "leak1")
    monkeypatch.setenv("SOME_OTHER_SECRET", "leak2")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("MY_DB_PW", "secret")
    charter = {"credentials": [{"name": "db", "ref": "env:MY_DB_PW", "scope": "ro"}]}
    result = scoped_env(charter)
    for key in result:
        assert (
            key in _ESSENTIAL_VARS or key.startswith(_ESSENTIAL_PREFIXES) or key == "MY_DB_PW"
        ), f"stray key leaked into scoped env: {key!r}"
