"""One run at a time, and a killed run must not look like success.

Both matter only once something schedules an agent, which is exactly when nobody is watching:
overlapping runs quietly share one budget and one log, and a wall-clock kill that exits 0 tells
cron the run succeeded. Every builder has to behave the same way here, so these tests run the
same assertions across all three runners.

No tokens, no network — the lock is taken before anything is spent, so a locked-out run exits
without ever reaching a model.
"""

import fcntl
import importlib.util
import subprocess
import sys
import time

import pytest
from conftest import REPO_ROOT

RUNNERS = {
    "headless": REPO_ROOT / "builders" / "claude-headless" / "run_headless.py",
    "claude-sdk": REPO_ROOT / "builders" / "claude-sdk" / "example.agent.py",
    "langchain": REPO_ROOT / "builders" / "langchain" / "example.agent.py",
}


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(params=sorted(RUNNERS))
def runner(request):
    return request.param, _load(RUNNERS[request.param], f"lock_{request.param.replace('-', '_')}")


def _acquire(mod, runtime, agent_dir):
    """run_headless.py takes the agent dir as an argument; the self-contained agents use HERE."""
    if runtime == "headless":
        return mod.acquire_run_lock(str(agent_dir))
    mod.HERE = str(agent_dir)
    return mod.acquire_run_lock()


# --- the lock ---------------------------------------------------------------------------
def test_lock_is_acquired_when_free(runner, tmp_path):
    runtime, mod = runner
    assert _acquire(mod, runtime, tmp_path) is True
    assert (tmp_path / ".run.lock").exists()


def test_second_acquirer_is_refused(runner, tmp_path):
    """The whole point: a second run finds the lock held and backs off instead of doubling up."""
    runtime, mod = runner
    holder = open(tmp_path / ".run.lock", "w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert _acquire(mod, runtime, tmp_path) is False
    finally:
        holder.close()


def test_lock_is_released_when_the_holder_dies(runner, tmp_path):
    """An advisory flock is kernel-owned, so a SIGKILLed run cannot strand it. This is why the
    lock is not a pidfile or a mkdir: those survive the process and wedge the next run."""
    runtime, mod = runner
    lock = tmp_path / ".run.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import fcntl,time;f=open({str(lock)!r},'w');"
            "fcntl.flock(f,fcntl.LOCK_EX);time.sleep(60)",
        ]
    )
    try:
        # wait for the child to actually hold it before asserting the negative
        for _ in range(200):
            if not _acquire(mod, runtime, tmp_path):
                break
            mod._RUN_LOCK_FD.close()  # we won the race; let go and give the child a chance
            mod._RUN_LOCK_FD = None
            time.sleep(0.01)
        else:
            pytest.fail("child never took the lock")
    finally:
        holder.kill()
        holder.wait()
    assert _acquire(mod, runtime, tmp_path) is True  # kernel dropped it on death


def test_lock_file_lives_in_the_agent_dir(runner, tmp_path):
    """Per-agent, not global: two different agents must never block each other."""
    runtime, mod = runner
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert _acquire(mod, runtime, a) is True
    mod._RUN_LOCK_FD.close()  # release before taking the other, we only care about the paths
    mod._RUN_LOCK_FD = None
    assert _acquire(mod, runtime, b) is True
    assert (a / ".run.lock").exists() and (b / ".run.lock").exists()


# --- exit codes -------------------------------------------------------------------------
@pytest.mark.parametrize("runtime", sorted(RUNNERS))
def test_killed_run_exits_5_not_0(runtime):
    """A wall-clock or step-cap kill produced no output. Exiting 0 would report success to a
    scheduler for the one failure it most needs to hear about."""
    src = RUNNERS[runtime].read_text()
    assert "sys.exit(5)" in src, f"{runtime} has no exit-5 path for a killed run"


@pytest.mark.parametrize("runtime", sorted(RUNNERS))
def test_killed_outcome_uses_the_shared_word(runtime):
    """One vocabulary across builders, so a fleet-wide query over runs.jsonl needs no
    per-runtime special cases. Headless used to log this as "timeout"."""
    src = RUNNERS[runtime].read_text()
    assert '"outcome": "killed"' in src or 'outcome = "killed"' in src
    assert '"outcome": "timeout"' not in src


@pytest.mark.parametrize("runtime", sorted(RUNNERS))
def test_lock_refusal_exits_8(runtime):
    assert "sys.exit(8)" in RUNNERS[runtime].read_text()


@pytest.mark.parametrize("runtime", sorted(RUNNERS))
def test_lock_is_taken_before_the_model_is_reached(runtime):
    """Cheap config gates first (status/prompt), then the lock, then spend. A locked-out run
    must cost nothing."""
    src = RUNNERS[runtime].read_text()
    lock_at = src.index("if not acquire_run_lock")  # the call site, not the definition
    assert src.index("sys.exit(4)") < lock_at, "prompt gate must come first"
    assert lock_at < src.rindex("sys.exit(5)"), "lock must precede the run it guards"


def test_lock_file_is_gitignored():
    """A per-run artifact of one machine; committing it would be noise at best."""
    out = subprocess.run(
        ["git", "check-ignore", "builders/claude-headless/agents/sentiment-tagger/.run.lock"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    assert out.returncode == 0, ".run.lock is not gitignored"


def test_no_runner_leaks_the_lock_fd_to_a_local():
    """The fd must outlive the function that took it — a local would close on return and
    silently release the lock while the run continues."""
    for path in RUNNERS.values():
        src = path.read_text()
        assert "global _RUN_LOCK_FD" in src, f"{path.name} does not hold the lock fd globally"
