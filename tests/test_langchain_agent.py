"""Tests for builders/langchain/example.agent.py's inlined enforcement helpers.

The agent's filename has a dot (example.agent.py) so a normal `import` statement can't name it —
it's loaded via importlib.util.spec_from_file_location, same effect as
`python3 builders/langchain/example.agent.py`. Most tests need no langchain/langgraph install:
importing the module, scoped_env, fetch_egress_check, estimate_cost, jail_path/resolve_root, the
build_tools fail-closed paths, and enforcement_report are all pure or lazily-imported by design
(the langchain imports live inside run() and the tool factories, not at module scope). The few
tests that invoke a tool factory (which decorates with @tool) are gated with
pytest.importorskip("langchain_core") — they run wherever langchain is installed and skip in a
bare environment. The filesystem WALL itself (jail_path) is tested purely, with no such gate.

No tokens, no network.
"""

import importlib.util
import os
import subprocess
import sys
import urllib.error as urllib_error
import urllib.request as urllib_request

import pytest
import yaml
from conftest import REPO_ROOT

AGENT_PATH = REPO_ROOT / "builders" / "langchain" / "example.agent.py"

_spec = importlib.util.spec_from_file_location("lc_example_agent", AGENT_PATH)
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)


def test_module_import_does_not_pull_in_langchain():
    assert "langchain" not in sys.modules
    assert "langgraph" not in sys.modules


# --- scoped_env: keep only declared env: credentials + OS essentials ---------


def test_scoped_env_keeps_declared_key_drops_stray_secret():
    # CHARTER declares env:ANTHROPIC_API_KEY; a stray second provider key must be dropped.
    src = {
        "ANTHROPIC_API_KEY": "sk-ant-real",
        "OPENROUTER_API_KEY": "leak",
        "PATH": "/usr/bin",
        "HOME": "/home/x",
    }
    result = agent.scoped_env(agent.CHARTER, src=src)
    assert result["ANTHROPIC_API_KEY"] == "sk-ant-real"
    assert "OPENROUTER_API_KEY" not in result
    assert result["PATH"] == "/usr/bin"


def test_scoped_env_undeclared_key_dropped():
    src = {"OPENAI_API_KEY": "sk-openai", "PATH": "/usr/bin"}
    result = agent.scoped_env(agent.CHARTER, src=src)
    assert "OPENAI_API_KEY" not in result


# --- fetch_egress_check: mirrors the headless egress_guard host-match rule ----


def test_egress_allows_allowlisted_host():
    allow, _ = agent.fetch_egress_check("https://docs.python.org/3/", ["docs.python.org"])
    assert allow


def test_egress_denies_other_host():
    allow, _ = agent.fetch_egress_check("https://evil.example.com/", ["docs.python.org"])
    assert not allow


def test_egress_denies_lookalike_prefix_host():
    # 'evildocs.python.org' shares a string suffix with 'docs.python.org' but is not a subdomain
    # of it (no dot before 'docs') — the dot-suffix rule must reject it.
    allow, _ = agent.fetch_egress_check("https://evildocs.python.org/", ["docs.python.org"])
    assert not allow


def test_egress_allows_real_subdomain():
    allow, _ = agent.fetch_egress_check("https://x.docs.python.org/", ["docs.python.org"])
    assert allow


def test_egress_any_opens():
    allow, _ = agent.fetch_egress_check("https://anything.example/", ["any"])
    assert allow


def test_egress_none_closes():
    allow, _ = agent.fetch_egress_check("https://docs.python.org/", ["none"])
    assert not allow


def test_egress_no_host_denied():
    allow, _ = agent.fetch_egress_check("not-a-url", ["docs.python.org"])
    assert not allow


# --- scheme wall: only http/https may be fetched (blocks file://, ftp://, SSRF-via-scheme) ----


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://docs.python.org/3/", True),
        ("http://docs.python.org/3/", True),
        ("HTTPS://docs.python.org/3/", True),  # scheme is case-insensitive
        ("file:///etc/passwd", False),
        ("ftp://docs.python.org/x", False),
        ("gopher://docs.python.org/", False),
    ],
)
def test_scheme_allowed(url, ok):
    assert agent.scheme_allowed(url) is ok


def test_fetch_url_refuses_file_scheme_even_when_egress_open():
    # #2: egress:[any] host-checks nothing, so without a scheme wall a file:// URL would read a
    # local file. The scheme check must refuse it before any open() happens — no network, no fs read.
    pytest.importorskip("langchain_core")
    f = agent.make_fetch_url(["any"])
    msg = f.invoke({"url": "file:///etc/passwd"})
    assert "[egress denied]" in msg
    assert "http/https" in msg


# --- redirect wall: every redirect hop is re-checked against egress (#1) ----------------------


def _redirect(handler, newurl, code=302):
    """Drive the handler's redirect_request the way urllib does mid-fetch. Returns the new Request
    on allow, or raises urllib.error.HTTPError on a denied hop."""
    import http.client

    req = urllib_request.Request("https://docs.python.org/3/")
    return handler.redirect_request(req, None, code, "Found", http.client.HTTPMessage(), newurl)


def test_redirect_to_offlist_host_is_refused():
    # #1: an allowed host that 302-redirects to another host must NOT be followed.
    handler = agent._EgressRedirectHandler(["docs.python.org"])
    with pytest.raises(urllib_error.HTTPError):
        _redirect(handler, "https://evil.example.com/steal")


def test_redirect_to_nonhttp_scheme_is_refused():
    handler = agent._EgressRedirectHandler(["docs.python.org"])
    with pytest.raises(urllib_error.HTTPError):
        _redirect(handler, "file:///etc/passwd")


def test_redirect_to_onlist_host_is_allowed():
    # an on-list (or subdomain) redirect target is still followed — the wall only stops escapes.
    handler = agent._EgressRedirectHandler(["docs.python.org"])
    new_req = _redirect(handler, "https://docs.python.org/3/whatsnew/")
    assert new_req.get_full_url() == "https://docs.python.org/3/whatsnew/"


# --- estimate_cost: the soft, client-side budget.usd number ------------------


def test_estimate_cost_prefix_matches_dated_id():
    # a pinned dated id (…-20251001) must still resolve via prefix match
    c = agent.estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    assert c == round(1.0 + 5.0, 6)


def test_estimate_cost_unknown_model_is_none():
    assert agent.estimate_cost("some-other-model", 100, 100) is None


# --- enforcement_report: the --dry-run honest report, no langchain import ----


def test_enforcement_report_is_honest_and_langchain_free():
    report = agent.enforcement_report(agent.CHARTER)
    assert isinstance(report, str)
    assert "block" in report
    assert "none" in report
    assert "recursion_limit" in report  # budget.steps -> recursion_limit wall
    assert "fetch_url" in report
    assert "langchain" not in sys.modules
    assert "langgraph" not in sys.modules


# --- the embedded CHARTER validates against core/charter.schema.yaml ---------


def test_embedded_charter_validates(tmp_path):
    charter_file = tmp_path / "langchain-docs-researcher.charter.yaml"
    charter_file.write_text(yaml.safe_dump(agent.CHARTER, sort_keys=False))

    result = subprocess.run(
        [sys.executable, "core/validate.py", str(charter_file)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "VALID" in result.stdout


# --- resolve_root / jail_path: THE filesystem wall (pure, no langchain) ------


def test_resolve_root_relative_is_under_base(tmp_path):
    base = str(tmp_path)
    assert agent.resolve_root("out", base) == os.path.realpath(os.path.join(base, "out"))


def test_resolve_root_none_when_unset():
    assert agent.resolve_root(None, "/whatever") is None
    assert agent.resolve_root("", "/whatever") is None


def test_jail_allows_real_subpath(tmp_path):
    root = str(tmp_path)
    ok, resolved, _ = agent.jail_path(root, "sub/file.txt")
    assert ok
    assert resolved.startswith(os.path.realpath(root) + os.sep)


def test_jail_rejects_dotdot_traversal(tmp_path):
    root = str(tmp_path / "jail")
    os.makedirs(root)
    ok, _, reason = agent.jail_path(root, "../escape.txt")
    assert not ok
    assert "escapes" in reason


def test_jail_rejects_absolute_outside(tmp_path):
    root = str(tmp_path / "jail")
    os.makedirs(root)
    ok, _, _ = agent.jail_path(root, "/etc/passwd")
    assert not ok


def test_jail_rejects_symlink_escape(tmp_path):
    # a symlink inside the root that points OUT must not let a read/write escape — realpath
    # resolves the link before the prefix check.
    root = tmp_path / "jail"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    (root / "link").symlink_to(outside, target_is_directory=True)
    ok, _, _ = agent.jail_path(str(root), "link/secret.txt")
    assert not ok


def test_jail_no_root_denied():
    ok, _, reason = agent.jail_path(None, "x")
    assert not ok
    assert "no filesystem root" in reason


# --- build_tools fail-closed paths (no langchain: these raise before any factory call) -------


def test_build_tools_unknown_verb_raises():
    with pytest.raises(ValueError):
        agent.build_tools({"tools": ["frobnicate"]}, [], None)


def test_build_tools_fs_tool_without_root_raises():
    with pytest.raises(ValueError) as e:
        agent.build_tools({"tools": ["write_file"]}, [], None)
    assert "sandbox.filesystem" in str(e.value)


def test_build_tools_empty_is_textonly():
    assert agent.build_tools({"tools": []}, [], None) == []


# --- enforcement_report surfaces the fs jail honestly (no langchain) ---------


def test_enforcement_report_shows_fs_jail():
    charter = dict(agent.CHARTER)
    charter["tools"] = ["fetch_url", "write_file"]
    charter["sandbox"] = {"isolation": "none", "filesystem": "./out"}
    report = agent.enforcement_report(charter)
    assert "tools.fs" in report
    assert "write_file is the ONE write capability" in report
    assert "not an OS sandbox" in report
    assert "langchain" not in sys.modules
    assert "langgraph" not in sys.modules


# --- the wired tool factories: guard behavior (gated on langchain being installed) -----------


def test_write_file_tool_writes_in_jail_and_refuses_escape(tmp_path):
    pytest.importorskip("langchain_core")
    wf = agent.make_write_file(str(tmp_path))
    ok_msg = wf.invoke({"path": "sub/out.md", "content": "hello"})
    assert "wrote" in ok_msg
    assert (tmp_path / "sub" / "out.md").read_text() == "hello"
    denied = wf.invoke({"path": "../evil.md", "content": "x"})
    assert "[fs denied]" in denied
    assert not (tmp_path.parent / "evil.md").exists()


def test_read_file_tool_reads_in_jail_and_refuses_escape(tmp_path):
    pytest.importorskip("langchain_core")
    (tmp_path / "a.txt").write_text("data")
    rf = agent.make_read_file(str(tmp_path))
    assert rf.invoke({"path": "a.txt"}) == "data"
    assert "[fs denied]" in rf.invoke({"path": "/etc/hosts"})
