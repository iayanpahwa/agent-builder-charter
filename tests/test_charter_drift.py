"""A self-contained agent carries its charter twice; the two copies must agree.

`agent.py` embeds `CHARTER` so it can run standalone, and ships a sibling `charter.yaml` for
audit and regeneration. That is two sources of truth, and when they drift it is silent: the file
someone reviews says one thing while the process that runs says another, which makes the audit
worthless in precisely the case it exists for.

This is checked at run time inside every generated agent (`--dry-run`), not only here, because a
test in this repo can only ever see the agents shipped in this repo — and real agents live
somewhere else. These tests cover the checker itself.

Both drift cases below were observed in a real build, and both are the kind a shallow key-by-key
comparison waves through.
"""

import textwrap

import pytest
import yaml
from conftest import runner_modules

RUNNERS = runner_modules(prefix="drift")
EMBEDDING_RUNTIMES = ["claude-sdk", "langchain"]


@pytest.fixture(params=EMBEDDING_RUNTIMES)
def mod(request):
    return RUNNERS[request.param]


def _write(tmp_path, text):
    p = tmp_path / "charter.yaml"
    p.write_text(textwrap.dedent(text))
    return str(p)


def test_no_drift_is_silent(mod, tmp_path):
    charter = {"id": "a", "tools": ["WebFetch"], "budget": {"usd": 0.2}}
    path = _write(tmp_path, yaml.safe_dump(charter))
    assert mod.charter_drift(charter, path) == []


def test_a_missing_sibling_is_not_an_error(mod, tmp_path):
    """Not every deployment ships the yaml beside the .py; absence is not drift."""
    assert mod.charter_drift({"id": "a"}, str(tmp_path / "nope.yaml")) == []


def test_a_changed_value_is_reported(mod, tmp_path):
    path = _write(tmp_path, "id: a\nbudget:\n  usd: 5.00\n")
    out = "\n".join(mod.charter_drift({"id": "a", "budget": {"usd": 0.2}}, path))
    assert "DRIFT" in out and "budget.usd" in out


def test_a_key_only_in_the_embedded_copy_is_reported(mod, tmp_path):
    """The dangerous direction: the running agent has authority the audited file never mentions."""
    path = _write(tmp_path, "id: a\n")
    out = "\n".join(mod.charter_drift({"id": "a", "tools": ["Bash"]}, path))
    assert "tools" in out and "only in the embedded CHARTER" in out


def test_a_key_only_in_the_file_is_reported(mod, tmp_path):
    path = _write(tmp_path, "id: a\ntools: [Bash]\n")
    out = "\n".join(mod.charter_drift({"id": "a"}, path))
    assert "tools" in out and "only in charter.yaml" in out


def test_a_changed_list_length_is_reported(mod, tmp_path):
    path = _write(tmp_path, "id: a\negress: [x.com, y.com]\n")
    out = "\n".join(mod.charter_drift({"id": "a", "egress": ["x.com"]}, path))
    assert "egress" in out


# --- the two drifts that actually happened --------------------------------------------------
def test_trailing_newline_from_a_block_scalar_is_caught(mod, tmp_path):
    """YAML `>` keeps a trailing newline and `>-` strips it. The embedded Python string had no
    newline, the file's `>` block did, and nothing noticed."""
    path = _write(
        tmp_path,
        """\
        id: a
        extensions:
          note: >
            some text
        """,
    )
    on_disk = yaml.safe_load(open(path))
    assert on_disk["extensions"]["note"] == "some text\n"  # the YAML fact this rests on
    out = "\n".join(mod.charter_drift({"id": "a", "extensions": {"note": "some text"}}, path))
    assert "DRIFT" in out and "extensions.note" in out


def test_a_regex_that_survives_the_round_trip_differently_is_caught(mod, tmp_path):
    r"""Python's r"[\"']" keeps the backslash; YAML's ["'] does not. Same intent, different
    string, and a redaction pattern that differs between the audited file and the running agent
    is a real difference in what gets scrubbed."""
    path = _write(tmp_path, 'id: a\ndata:\n  redact: ["[\\"\']"]\n')
    on_disk = yaml.safe_load(open(path))
    embedded = {"id": "a", "data": {"redact": [r"[\"']"]}}
    assert on_disk["data"]["redact"][0] != embedded["data"]["redact"][0]
    out = "\n".join(mod.charter_drift(embedded, path))
    assert "DRIFT" in out and "redact" in out


# --- the checker must never break the thing it diagnoses ------------------------------------
def test_unreadable_yaml_is_a_note_not_a_crash(mod, tmp_path):
    """A --dry-run that dies because the sibling file is malformed helps nobody; it should say so
    and let the rest of the report through."""
    path = _write(tmp_path, "id: a\n  bad: [indent\n")
    out = mod.charter_drift({"id": "a"}, path)
    assert len(out) == 1 and out[0].startswith("NOTE:")


def test_long_drift_lists_are_truncated(mod, tmp_path):
    """A report nobody reads is a report that does not work."""
    embedded = {f"k{i}": i for i in range(30)}
    path = _write(tmp_path, yaml.safe_dump({f"k{i}": i + 1 for i in range(30)}))
    out = mod.charter_drift(embedded, path)
    assert len(out) <= 12
    assert "more" in out[-1]


def test_headless_needs_no_drift_check_because_it_has_one_copy():
    """run_headless.py reads charter.yaml directly. There is no second copy to disagree with,
    which is the stronger design — worth stating so nobody 'fixes' it by adding one."""
    assert not hasattr(RUNNERS["headless"], "charter_drift")
    assert not hasattr(RUNNERS["headless"], "CHARTER")
