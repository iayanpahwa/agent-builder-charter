"""The dangerous-tools deny list, on the two runtimes that have one.

Under `bypassPermissions` — which every unattended agent needs, because nobody is there to answer
a prompt — `disallowed_tools` is the ONLY list that removes a tool. `allowed_tools` is declarative
there and the enforcement report says so. That makes a stale name in DANGEROUS a silent hole: it
denies something that no longer exists while the tool it was meant to stop sails through.

The list drifted exactly that way. It denied `Task`, which the CLI now treats as an alias of
`Agent`, and never mentioned `Agent` itself — so a charter that granted no delegation could still
spawn subagents, each with its own tool access.

langchain has no equivalent and needs none: only the tools the builder binds exist at all.
"""

import pytest
from conftest import runner_modules

RUNNERS = runner_modules(prefix="dangerous")
CLAUDE_RUNTIMES = ["headless", "claude-sdk"]


@pytest.mark.parametrize("name", CLAUDE_RUNTIMES)
def test_delegation_is_denied_under_its_canonical_name(name):
    """`Agent` is what the CLI's tool registry actually calls it; `Task` is a back-compat alias.
    Denying only the alias is what left this open."""
    assert "Agent" in RUNNERS[name].DANGEROUS


@pytest.mark.parametrize("name", CLAUDE_RUNTIMES)
def test_the_legacy_alias_is_kept_too(name):
    """Belt and braces across CLI versions: on a build where `Task` is the live name the deny
    still lands, and on one where it is only an alias the extra entry is inert."""
    assert "Task" in RUNNERS[name].DANGEROUS


@pytest.mark.parametrize("name", CLAUDE_RUNTIMES)
def test_an_unattended_agent_cannot_stop_to_ask_a_human(name):
    """There is nobody to answer. Granting this buys a hang, not an answer."""
    assert "AskUserQuestion" in RUNNERS[name].DANGEROUS


@pytest.mark.parametrize("name", CLAUDE_RUNTIMES)
def test_the_shell_and_the_filesystem_writers_stay_denied(name):
    for tool in ("Bash", "Write", "Edit", "NotebookEdit"):
        assert tool in RUNNERS[name].DANGEROUS


def test_both_claude_runtimes_deny_exactly_the_same_set():
    """One charter must mean one thing. A tool denied on headless and permitted on claude-sdk
    would make the same charter describe two different agents."""
    assert RUNNERS["headless"].DANGEROUS == RUNNERS["claude-sdk"].DANGEROUS


@pytest.mark.parametrize("name", CLAUDE_RUNTIMES)
def test_no_duplicates(name):
    d = RUNNERS[name].DANGEROUS
    assert len(d) == len(set(d))


@pytest.mark.parametrize("name", CLAUDE_RUNTIMES)
def test_granting_a_tool_removes_it_from_the_denied_set(name):
    """The charter is the grant: a tool it lists must not also be denied, or the charter would be
    describing an agent that cannot do what it says it does."""
    mod = RUNNERS[name]
    granted = ["Bash", "Agent"]
    denied = [t for t in mod.DANGEROUS if t not in granted]
    assert "Bash" not in denied and "Agent" not in denied
    assert "Write" in denied  # everything ungranted stays denied


def test_langchain_has_no_deny_list_because_it_needs_none():
    """LangGraph has no ambient tool registry — the wall is that only bound tools exist. A deny
    list there would imply a wall that isn't the real one."""
    assert not hasattr(RUNNERS["langchain"], "DANGEROUS")
