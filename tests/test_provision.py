"""core/provision.py — the shim it writes is the thing under test.

The shim is what every agent is actually launched through, by hand and by cron alike, so the
properties that matter are structural: it resolves everything absolutely, it activates nothing,
and it never invokes a package manager. That last one is the wall — provisioning may reach the
network, a run may not, or the charter's `egress` stops being the whole story.

No venv is built here and nothing is installed; these exercise the pure path/shim logic only.
"""

import os
import pathlib
import re
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE_DIR = REPO_ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import provision  # noqa: E402


def _agent_dir(tmp_path, runtime, with_agent_py=True):
    d = tmp_path / "an-agent"
    d.mkdir()
    (d / "charter.yaml").write_text(f'charter: "0.2"\nruntime: {runtime}\nid: an-agent\n')
    if with_agent_py:
        (d / "agent.py").write_text("# stub\n")
    return d


def _shim(tmp_path, runtime, binaries=None):
    d = _agent_dir(tmp_path, runtime)
    req, _ = provision.find_requirements(str(d), runtime)
    provision.write_shim(str(d), runtime, req, binaries or {})
    return (d / "run").read_text()


# --- runtime detection ------------------------------------------------------------------
@pytest.mark.parametrize("runtime", sorted(provision.RUNTIMES))
def test_runtime_read_from_charter(tmp_path, runtime):
    assert provision.read_runtime(str(_agent_dir(tmp_path, runtime))) == runtime


def test_runtime_falls_back_to_builder_path(tmp_path):
    """A deployed copy may have no readable charter; the builder dir still identifies it."""
    d = tmp_path / "builders" / "langchain" / "agents" / "an-agent"
    d.mkdir(parents=True)
    assert provision.read_runtime(str(d)) == "langchain"


def test_unknown_runtime_is_none(tmp_path):
    d = tmp_path / "an-agent"
    d.mkdir()
    (d / "charter.yaml").write_text("runtime: bogus-harness\n")
    assert provision.read_runtime(str(d)) is None


def test_credential_vars_read_from_charter(tmp_path):
    d = _agent_dir(tmp_path, "langchain")
    (d / "charter.yaml").write_text(
        "runtime: langchain\ncredentials:\n"
        "  - name: a\n    ref: env:ANTHROPIC_API_KEY\n"
        "  - name: b\n    ref: env:OTHER_TOKEN\n"
    )
    assert provision.read_credential_vars(str(d)) == ["ANTHROPIC_API_KEY", "OTHER_TOKEN"]


# --- requirements resolution ------------------------------------------------------------
@pytest.mark.parametrize("runtime", sorted(provision.RUNTIMES))
def test_every_runtime_has_a_real_requirements_file(runtime):
    """A runtime whose default requirements path is wrong fails at provision time, per host."""
    assert (REPO_ROOT / provision.RUNTIMES[runtime]["requirements"]).exists()


def test_lock_preferred_over_txt_preferred_over_builder_default(tmp_path):
    """Precedence: the agent's lock, its txt, the builder's lock, the builder's txt. A lock is
    always installed hash-checked; floors cannot be."""
    d = _agent_dir(tmp_path, "langchain")

    req, hashed = provision.find_requirements(str(d), "langchain")
    assert req == str(REPO_ROOT / "builders" / "langchain" / "requirements.lock") and hashed

    (d / "requirements.txt").write_text("langchain==1.0.0\n")
    req, hashed = provision.find_requirements(str(d), "langchain")
    assert req == str(d / "requirements.txt") and not hashed

    (d / "requirements.lock").write_text("langchain==1.0.0 --hash=sha256:abc\n")
    req, hashed = provision.find_requirements(str(d), "langchain")
    assert req == str(d / "requirements.lock") and hashed


@pytest.mark.parametrize("runtime", sorted(provision.RUNTIMES))
def test_every_runtime_has_a_hash_pinned_lock(runtime):
    """Floors like `langgraph>=0.2` mean the same charter builds a different agent later. Every
    builder ships a lock so the default provisioning path is reproducible, not just possible."""
    txt = REPO_ROOT / provision.RUNTIMES[runtime]["requirements"]
    lock = txt.with_suffix(".lock")
    assert lock.exists(), f"{runtime} has no {lock.name}"
    assert "--hash=sha256:" in lock.read_text()


# --- the shim ---------------------------------------------------------------------------
@pytest.mark.parametrize("runtime", sorted(provision.RUNTIMES))
def test_shim_never_invokes_a_package_manager(tmp_path, runtime):
    """THE wall: a run installs nothing, so packaging can never be a smuggled egress path."""
    shim = _shim(tmp_path, runtime)
    for forbidden in (" pip ", "pip install", "uv pip", "uv run", "uv venv", "ensurepip"):
        assert forbidden not in shim, f"{runtime} shim reaches for a package manager: {forbidden}"


@pytest.mark.parametrize("runtime", sorted(provision.RUNTIMES))
def test_shim_never_activates_a_venv(tmp_path, runtime):
    """Activation is shell state you can forget to undo; exec'ing the interpreter has none."""
    shim = _shim(tmp_path, runtime)
    assert "bin/activate" not in shim
    # The one file it may source is the optional per-agent .env, for unattended runs. The
    # charter still filters what reaches the agent, so that cannot widen its authority.
    sourced = re.findall(r"^\s*(?:source|\.)\s+\"?([^\"\s]+)", shim, re.M)
    assert all(s.endswith("/.env") for s in sourced), sourced
    assert re.search(r"^exec ", shim, re.M), "the shim must exec, so signals hit the real process"


@pytest.mark.parametrize("runtime", sorted(provision.RUNTIMES))
def test_shim_sets_path_explicitly_and_uses_absolute_paths(tmp_path, runtime):
    """cron supplies no PATH and no CWD; nothing in the shim may depend on either."""
    shim = _shim(tmp_path, runtime, binaries={"claude": "/opt/tools/bin/claude"})
    assert re.search(r"^PATH='[^']+'$", shim, re.M)
    assert "/opt/tools/bin" in shim  # the resolved binary's dir is baked in, not looked up
    assert "cd " not in shim
    for var in ("AGENT_DIR", "VENV_PY"):
        m = re.search(rf"^{var}='([^']+)'$", shim, re.M)
        assert m and os.path.isabs(m.group(1)), f"{var} must be an absolute path"


@pytest.mark.parametrize("runtime", sorted(provision.RUNTIMES))
def test_shim_refuses_on_dependency_drift(tmp_path, runtime):
    shim = _shim(tmp_path, runtime)
    assert "exit 7" in shim
    req, _ = provision.find_requirements(str(tmp_path / "an-agent"), runtime)
    assert provision.sha256(req) in shim  # pinned to the file it was provisioned from


def test_headless_shim_translates_the_positional_trigger(tmp_path):
    """run_headless.py takes --trigger as a flag, but `run <trigger>` must work everywhere."""
    shim = _shim(tmp_path, "headless")
    assert "--trigger" in shim and "trigger=manual" in shim
    assert "run_headless.py" in shim
    assert "--eval" in shim  # a no-op without evals/cases.yaml, so evals stay optional


@pytest.mark.parametrize("runtime", ["claude-sdk", "langchain"])
def test_agent_py_shim_passes_args_straight_through(tmp_path, runtime):
    shim = _shim(tmp_path, runtime)
    assert 'exec "$VENV_PY" "$AGENT_DIR/agent.py" "$@"' in shim


def test_shim_is_executable(tmp_path):
    d = _agent_dir(tmp_path, "langchain")
    req, _ = provision.find_requirements(str(d), "langchain")
    run = pathlib.Path(provision.write_shim(str(d), "langchain", req, {}))
    assert os.access(run, os.X_OK)


def test_missing_agent_py_is_refused_not_silently_shimmed(tmp_path):
    d = _agent_dir(tmp_path, "langchain", with_agent_py=False)
    req, _ = provision.find_requirements(str(d), "langchain")
    with pytest.raises(SystemExit):
        provision.write_shim(str(d), "langchain", req, {})
