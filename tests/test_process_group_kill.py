"""Tests for run_headless.py's _kill_process_group() helper.

The runner starts `claude` with start_new_session=True so the child is its own
process-group leader. On a wall-clock timeout, subprocess.Popen.kill()/terminate()
only reaps the direct child — any MCP/Bash grandchildren the child spawned would
be orphaned and keep running. _kill_process_group() guards against that by
signaling the whole process group (os.killpg), not just the direct child.

These tests spawn only local `sh`/`sleep` processes to stand in for that
process tree shape (parent -> grandchild) and verify the grandchild is reaped
too. No tokens, no network, no `claude` invoked.
"""

import os
import signal
import subprocess
import time

import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "builders" / "claude-headless"))
from run_headless import _kill_process_group  # noqa: E402


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_until(predicate, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_kill_process_group_reaps_grandchild(tmp_path):
    marker = tmp_path / "gpid"
    proc = subprocess.Popen(
        ["sh", "-c", f"sleep 300 & echo $! > {marker}; wait"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert _wait_until(
            lambda: marker.exists() and marker.read_text().strip() != ""
        ), "grandchild never wrote its pid to the marker file"
        gpid = int(marker.read_text().strip())

        assert _alive(gpid), "grandchild should be alive before the kill"

        _kill_process_group(proc)

        assert _wait_until(
            lambda: not _alive(gpid)
        ), "grandchild (sleep 300) was not reaped by _kill_process_group — orphan leaked"
        assert proc.poll() is not None, "parent process was not reaped by _kill_process_group"
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def test_kill_process_group_on_already_exited_process_is_noop():
    proc = subprocess.Popen(
        ["sh", "-c", "true"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        proc.communicate()  # let it exit and reap it

        _kill_process_group(proc)  # must not raise: getpgid raises ProcessLookupError, swallowed
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
