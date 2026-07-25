"""Every way a run can end must leave a record.

A scheduled agent is watched by nobody, so the run log is the only account of what happened. The
gap this closes was observed twice in a real build: a run cut short by Ctrl-C produced a trace
file and NOTHING else — no output, no runs.jsonl line — which made the two runs anyone actually
wanted to explain the two runs with no record of them.

These are source-level structural assertions rather than live runs: reaching the real interrupt
path costs tokens and needs a model, while the property that matters ("no exit path skips the
log") is a property of the code's shape. They are the cheap half of the guarantee; the expensive
half is covered by actually running an agent.
"""

import re

import pytest
from conftest import RUNNER_PATHS, runner_modules

RUNNERS = runner_modules(prefix="interrupt")
SOURCES = {name: path.read_text() for name, path in RUNNER_PATHS.items()}


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_keyboard_interrupt_is_caught(name):
    """KeyboardInterrupt is a BaseException, so a bare `except Exception` does NOT catch it. That
    is exactly why the original code let it through and lost the run record."""
    assert "KeyboardInterrupt" in SOURCES[name]


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_interrupted_is_the_shared_word(name):
    """One vocabulary across builders, so a query over runs.jsonl needs no per-runtime cases."""
    assert '"interrupted"' in SOURCES[name]


def _handler_body(src, header_fragment):
    """The lines of an except block, found by indentation — so a test can ask what the handler
    actually does rather than guessing at a character window."""
    lines = src.splitlines()
    start = next(i for i, ln in enumerate(lines) if header_fragment in ln)
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = []
    for ln in lines[start + 1 :]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
            break
        body.append(ln)
    return "\n".join(body)


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_an_interrupted_run_is_logged(name):
    """The whole point: the run record gets written. The two builder shapes reach it differently
    and both are correct — headless logs inside the handler because it exits there; the
    self-contained agents set a flag and fall through to the ONE shared logging tail, which is
    better precisely because it cannot drift from the normal path."""
    src = SOURCES[name]
    body = _handler_body(src, "KeyboardInterrupt")
    if re.search(r"_?log_run\(", body):
        return  # inline design (headless): logs before exiting
    # fall-through design: the handler must not short-circuit past the shared log
    assert "sys.exit" not in body, f"{name} exits inside the handler, skipping the shared log"
    assert "return" not in body, f"{name} returns inside the handler, skipping the shared log"
    assert "interrupted = True" in body
    tail = src[src.index("KeyboardInterrupt") :]
    assert re.search(r"_?log_run\(", tail), f"{name} never reaches a log after the signal"


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_interrupted_does_not_exit_zero(name):
    """Exiting 0 would report success to cron for a run that produced nothing."""
    src = SOURCES[name]
    assert re.search(r'outcome in \("killed", "interrupted"\)', src) or re.search(
        r'"outcome": "interrupted"', src
    )
    # 5 is the shared "nothing completed" code; the cause is distinguished in runs.jsonl, not here
    assert "sys.exit(5)" in src


# --- partial output must survive the kill ------------------------------------------------
def test_sdk_accumulates_text_outside_the_cancelled_coroutine():
    """asyncio.wait_for CANCELS _collect on timeout; anything held only in its locals dies with
    it. The old code then explicitly discarded the partial text, so a killed run saved nothing
    even though it had produced words. Both halves must stay fixed."""
    src = SOURCES["claude-sdk"]
    # the accumulator is declared in run(), before _collect is defined
    decl = src.index("text_parts, tool_calls, result_msg = [], 0, None")
    collect = src.index("async def _collect(gen):")
    assert decl < collect, "text_parts must outlive the coroutine that fills it"
    assert "nonlocal tool_calls, result_msg" in src
    assert 'assistant_text, result_msg = "", None' not in src, "partial text is being discarded"


def test_sdk_closes_the_generator_on_every_path():
    """A leaked generator leaves the underlying `claude` subprocess running after the runner that
    was supposed to bound it has exited."""
    src = SOURCES["claude-sdk"]
    finally_block = src[src.index("    finally:", src.index("except asyncio.TimeoutError:")) :]
    assert "gen.aclose()" in finally_block[:600]


def test_headless_kills_the_process_group_on_interrupt():
    """Same reaping a timeout does: otherwise `claude` and anything it spawned outlive the run."""
    src = SOURCES["headless"]
    idx = src.index("except KeyboardInterrupt:")
    assert "_kill_process_group(proc)" in src[idx : idx + 800]


# --- assistant reasoning must be recoverable after a dead run -----------------------------
def test_sdk_mirrors_assistant_text_into_the_trace():
    """The live stream goes to stdout and is never persisted. A run that dies mid-flight used to
    leave its tool calls but not a word of its reasoning, and the reasoning is what explains why
    it was still going when the clock ran out."""
    src = SOURCES["claude-sdk"]
    handler = src[src.index('if kind == "text":') : src.index('elif kind == "tool":')]
    assert "trace_fp.write" in handler
    assert "redact(" in handler, "traced text must be redacted like everything else persisted"


def test_traced_text_is_redacted_before_it_is_written():
    """Redaction is applied once, to the value that both stdout and the trace receive — so the
    two can't drift into one being scrubbed and the other not."""
    mod = RUNNERS["claude-sdk"]
    assert mod.redact("key=SECRETVALUE", [r"key=\S+"], []) == "[REDACTED]"
    assert mod.redact("literal SECRETVALUE here", [], ["SECRETVALUE"]) == "literal [REDACTED] here"
