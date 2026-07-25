"""Code the self-contained builders inline must stay identical to the core module it came from.

`agent.py` is a single file you can copy anywhere, so it cannot `import core/`. Two pieces are
therefore duplicated into it verbatim: the eval checks, and the Bash guard. Duplication is the
right call for the artifact, but it is a fork the moment either side is edited alone, and the
divergence is invisible — no import fails, no test names the missing behaviour, the two runtimes
simply start enforcing different things from the same charter.

That matters most for the Bash guard: a fix applied to `core/bash_guard.py` and not to the
inlined copy means the same `bash_allow` permits one set of commands on headless and another on
claude-sdk. One charter has to mean one thing.

The check is exact-match on the function bodies. If this fails, re-copy from core/ — do not edit
the copy to match.
"""

import pytest
from conftest import REPO_ROOT

SDK = REPO_ROOT / "builders" / "claude-sdk" / "example.agent.py"
LANGCHAIN = REPO_ROOT / "builders" / "langchain" / "example.agent.py"

# (source module, the line the copy starts at, the line it ends before, which agents carry it)
COPIES = [
    (
        "core/eval_checks.py",
        '_URL = re.compile(r"https?://", re.I)',
        "# ---- one run at a time",
        [SDK, LANGCHAIN],
    ),
    (
        "core/bash_guard.py",
        "# Flags that take no argument",
        "# ---- inline eval_checks",
        [SDK],
    ),
]


def _core_body(module, start):
    text = (REPO_ROOT / module).read_text()
    return text[text.index(start) :].strip()


def _inlined_body(path, start, end):
    text = path.read_text()
    i = text.index(start)
    return (text[i : text.index(end, i)] if end else text[i:]).strip()


CASES = [(module, start, end, agent) for module, start, end, agents in COPIES for agent in agents]


@pytest.mark.parametrize(
    "module,start,end,agent",
    CASES,
    ids=[f"{m.split('/')[-1]}->{a.parent.name}" for m, _, _, a in CASES],
)
def test_inlined_copy_matches_core(module, start, end, agent):
    core = _core_body(module, start)
    inlined = _inlined_body(agent, start, end)
    assert core == inlined, (
        f"{agent.relative_to(REPO_ROOT)} has drifted from {module}. Re-copy from core/; "
        f"do not hand-edit the inlined copy."
    )


def test_the_bash_guard_is_not_inlined_where_it_would_be_dead_code():
    """langchain binds its own tools and has no Bash, so a copy there would be an unused wall
    that reads as if it were doing something."""
    assert "def bash_decision" not in LANGCHAIN.read_text()
