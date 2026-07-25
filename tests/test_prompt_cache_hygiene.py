"""Tests for prompt-cache hygiene — the system prompt must stay a byte-stable prefix.

Anthropic prompt caching is a PREFIX match: one changed byte anywhere in the prefix invalidates the
cache from that point on. Every builder assembles its system prompt the same way — concatenate
context.trusted_sources in charter order — and that assembly must inject NOTHING of its own. A
single interpolated timestamp, run id, uuid, or cwd would destroy cache reuse on every run, and it
would do so invisibly: no error, just cache_read_input_tokens stuck at 0.

This matters most for claude-sdk and claude-headless, where caching happens automatically inside the
`claude` CLI and we have no cache counters of our own to notice the regression from. Hence a test
rather than a comment.

Three guarantees, locked for all three builders:
  1. Purity      — output is EXACTLY the concatenated sources; nothing is injected.
  2. Determinism — two calls on the same inputs are byte-identical.
  3. Ordering    — charter list order is preserved (no set/dict iteration sneaking in).

Then the langchain-only prefix-vs-floor diagnostic, which is the one runtime where caching is ours
to turn on. No tokens, no network, no subprocess — every assembler is a pure file read.
"""

import importlib.util
import re
import sys

import pytest
from conftest import REPO_ROOT


# example.agent.py has a dot in its filename, so a normal import can't name it — same
# spec_from_file_location approach as test_sdk_agent.py / test_langchain_agent.py.
def _load(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sdk_agent = _load(
    "cache_sdk_agent", REPO_ROOT / "builders" / "claude-sdk" / "example.agent.py"
)
lc_agent = _load(
    "cache_lc_agent", REPO_ROOT / "builders" / "langchain" / "example.agent.py"
)

sys.path.insert(0, str(REPO_ROOT / "builders" / "claude-headless"))
from run_headless import _system_prompt_file  # noqa: E402


def _read_headless_prompt(charter, base_dir):
    """headless writes the prompt to a temp file for --append-system-prompt-file; read it back so
    all three assemblers share one signature."""
    path = _system_prompt_file(charter, base_dir)
    if path is None:
        return None
    with open(path) as f:
        return f.read()


ASSEMBLERS = [
    pytest.param(sdk_agent._read_trusted_sources, id="claude-sdk"),
    pytest.param(lc_agent._read_trusted_sources, id="langchain"),
    pytest.param(_read_headless_prompt, id="claude-headless"),
]

# Shapes an interpolated volatile value actually takes. A bare 4-digit year would false-positive on
# ordinary prose, so match structure instead.
VOLATILE_PATTERNS = [
    (r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", "an ISO timestamp"),
    (r"\d{8}T\d{6}", "a run-id timestamp (the run_ts format used for log filenames)"),
    (r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "a uuid"),
    (r"/(?:Users|home)/[^/\s]+/", "an absolute host path (cwd leakage)"),
]

FIRST = "FIRST source. Stable across runs."
SECOND = "SECOND source. Also stable."


@pytest.fixture
def sources(tmp_path):
    """Two trusted_source files plus a charter naming them, in a throwaway agent dir."""
    (tmp_path / "a.md").write_text(FIRST)
    (tmp_path / "b.md").write_text(SECOND)
    return {"context": {"trusted_sources": ["a.md", "b.md"]}}, tmp_path


# --- 1. Purity ---------------------------------------------------------------


@pytest.mark.parametrize("assemble", ASSEMBLERS)
def test_prompt_is_exactly_its_sources(assemble, sources):
    """The strongest form of the invariant: the output is the sources and nothing else. This is
    what fails if anyone ever prepends a date header or appends the trigger name."""
    charter, base = sources
    out = assemble(charter, str(base))
    assert out is not None
    assert FIRST in out and SECOND in out
    # Whatever separator or trailing whitespace a builder uses, nothing else may survive.
    leftover = out.replace(FIRST, "").replace(SECOND, "")
    assert (
        leftover.strip() == ""
    ), f"assembler injected content of its own: {leftover!r}"


@pytest.mark.parametrize("assemble", ASSEMBLERS)
def test_prompt_has_no_volatile_values(assemble, sources):
    charter, base = sources
    out = assemble(charter, str(base)) or ""
    for pattern, what in VOLATILE_PATTERNS:
        assert not re.search(pattern, out), (
            f"system prompt contains {what} — that changes the cached prefix on every run and "
            f"silently kills prompt caching. Keep volatile values out of the system prompt; put "
            f"them in the user message instead."
        )


# --- 2. Determinism ----------------------------------------------------------


@pytest.mark.parametrize("assemble", ASSEMBLERS)
def test_prompt_is_byte_identical_across_calls(assemble, sources):
    charter, base = sources
    assert assemble(charter, str(base)) == assemble(charter, str(base))


# --- 3. Ordering -------------------------------------------------------------


@pytest.mark.parametrize("assemble", ASSEMBLERS)
def test_source_order_follows_the_charter(assemble, sources):
    """Charter order must drive concatenation order. If a builder ever iterated a set, the order
    could vary between runs of the SAME charter — a silent cache miss every time."""
    charter, base = sources
    forward = assemble(charter, str(base))
    backward = assemble({"context": {"trusted_sources": ["b.md", "a.md"]}}, str(base))
    assert forward != backward
    assert forward.index("FIRST") < forward.index("SECOND")
    assert backward.index("SECOND") < backward.index("FIRST")


# --- 4. The langchain prefix-vs-floor diagnostic ------------------------------
# langchain builds the request in-process, so it is the only runtime where caching is ours to
# enable — and the only one carrying the diagnostic. Lock both branches of the decision.


def test_caching_note_says_would_not_engage_under_the_floor(sources):
    charter, base = sources
    charter["model"] = {"provider": "anthropic", "id": "claude-haiku-4-5"}  # floor 4096
    note = lc_agent.caching_note(charter, str(base))
    assert "would NOT engage" in note
    assert "4096" in note


def test_caching_note_says_would_engage_over_the_floor(sources):
    charter, base = sources
    charter["model"] = {"provider": "anthropic", "id": "claude-opus-5"}  # floor 512
    (base / "a.md").write_text("x" * 4000)  # ~1000 est. tokens, clears 512
    note = lc_agent.caching_note(charter, str(base))
    assert "WOULD engage" in note
    assert "512" in note


def test_caching_note_resolves_a_dated_model_id(sources):
    """Prefix match, like the price table — a pinned dated id must still find its floor."""
    charter, base = sources
    charter["model"] = {"provider": "anthropic", "id": "claude-haiku-4-5-20251001"}
    note = lc_agent.caching_note(charter, str(base))
    assert "4096" in note


def test_caching_note_handles_a_model_with_no_floor_on_record(sources):
    charter, base = sources
    charter["model"] = {"provider": "anthropic", "id": "claude-future-9"}
    note = lc_agent.caching_note(charter, str(base))
    assert "no floor on record" in note


def test_caching_note_does_not_claim_caching_is_enabled(sources):
    """Honesty guard, mirroring tests/test_report_honesty.py: langchain sets no cache_control, so
    the note must never read as though caching is on."""
    charter, base = sources
    charter["model"] = {"provider": "anthropic", "id": "claude-haiku-4-5"}
    note = lc_agent.caching_note(charter, str(base))
    assert "not set" in note
