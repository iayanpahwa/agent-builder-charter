"""Regression test for save_output filename collisions in the same wall-clock second.

Bug: save_output built its filename as `<TS>.output.txt` with TS at SECOND resolution
(`datetime.now().strftime("%Y%m%dT%H%M%S")`). Two runs completing within the same second
produced identical filenames, so the second call's `open(p, "w")` silently overwrote the
first run's output.

The fix: if the target path already exists, save_output appends a counter --
`<TS>.1.output.txt`, `<TS>.2.output.txt`, ... -- until it finds a free path, and returns
the path actually written.

No tokens, no real network -- and no reliance on real timing: the clock is frozen via a
fake datetime so the same-second collision is deterministic rather than a timing-dependent
flake.
"""

import sys
from datetime import datetime

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "builders" / "claude-headless"))
import run_headless  # noqa: E402
from run_headless import save_output  # noqa: E402


class _FrozenDatetime:
    """Stand-in for the `datetime` class: `.now()` always returns the same instant."""

    @classmethod
    def now(cls):
        return datetime(2026, 7, 18, 12, 0, 0)


def test_save_output_same_second_calls_get_distinct_files(tmp_path, monkeypatch):
    monkeypatch.setattr(run_headless, "datetime", _FrozenDatetime)

    charter_dir = str(tmp_path)
    path_a = save_output(charter_dir, "first run's output")
    path_b = save_output(charter_dir, "second run's output")

    assert path_a != path_b

    with open(path_a) as f:
        assert f.read() == "first run's output"
    with open(path_b) as f:
        assert f.read() == "second run's output"
