"""The credential preflight — `required_env` / `missing_credentials`, on all three runtimes.

The failure this closes is specific and was observed in a real build: a launcher tested a
HARDCODED credential name before sourcing the environment, a second credential was added later,
and the run started believing it held a key it did not have. Nothing crashed. The agent simply
got 401s it was not written to expect and reported a confident empty answer.

So the property under test is not "it checks credentials" but "the list it checks is DERIVED from
the charter" — a hardcoded name is what goes stale, and no test of a hardcoded name would have
caught the original bug either.
"""

import pytest
from conftest import runner_modules

RUNNERS = runner_modules()


def _charter(*refs):
    return {"credentials": [{"name": f"c{i}", "ref": r} for i, r in enumerate(refs)]}


@pytest.mark.parametrize("mod", RUNNERS.values(), ids=list(RUNNERS))
def test_required_env_is_derived_from_the_charter(mod):
    charter = _charter("env:ZYTE_API_KEY", "env:SERPAPI_KEY")
    assert [v for v, _ in mod.required_env(charter)] == ["ZYTE_API_KEY", "SERPAPI_KEY"]


@pytest.mark.parametrize("mod", RUNNERS.values(), ids=list(RUNNERS))
def test_a_second_credential_is_picked_up_without_touching_code(mod):
    """The exact regression: adding a credential must extend the check by itself."""
    one = mod.required_env(_charter("env:ZYTE_API_KEY"))
    two = mod.required_env(_charter("env:ZYTE_API_KEY", "env:LATE_ADDITION"))
    assert len(two) == len(one) + 1
    assert "LATE_ADDITION" in [v for v, _ in two]


@pytest.mark.parametrize("mod", RUNNERS.values(), ids=list(RUNNERS))
def test_non_env_refs_are_not_claimed_as_env_vars(mod):
    """`vault://` is declared but resolved by nobody here; it must not become an env check."""
    assert mod.required_env(_charter("vault://secrets/key")) == []


@pytest.mark.parametrize("mod", RUNNERS.values(), ids=list(RUNNERS))
def test_missing_non_auth_credential_is_fatal(mod):
    fatal, _ = mod.missing_credentials(_charter("env:ZYTE_API_KEY"), src={})
    assert fatal == ["ZYTE_API_KEY"]


@pytest.mark.parametrize("mod", RUNNERS.values(), ids=list(RUNNERS))
def test_present_credential_is_not_reported(mod):
    fatal, warn = mod.missing_credentials(_charter("env:ZYTE_API_KEY"), src={"ZYTE_API_KEY": "k"})
    assert fatal == [] and warn == []


@pytest.mark.parametrize("mod", RUNNERS.values(), ids=list(RUNNERS))
def test_empty_string_counts_as_missing(mod):
    """`export FOO=` in a .env is the same practical failure as never setting it."""
    fatal, _ = mod.missing_credentials(_charter("env:ZYTE_API_KEY"), src={"ZYTE_API_KEY": ""})
    assert fatal == ["ZYTE_API_KEY"]


@pytest.mark.parametrize("mod", RUNNERS.values(), ids=list(RUNNERS))
def test_missing_credentials_does_not_read_the_real_environment(mod, monkeypatch):
    """Pure, so a host that happens to have the var set can't make the test lie."""
    monkeypatch.setenv("ZYTE_API_KEY", "real-value-on-this-host")
    fatal, _ = mod.missing_credentials(_charter("env:ZYTE_API_KEY"), src={})
    assert fatal == ["ZYTE_API_KEY"]


# --- the auth-var carve-out ---------------------------------------------------------------
# The Claude runtimes shell out to the `claude` CLI, which falls back to a stored login, so a
# missing auth var is a warning. langchain reads its provider key straight from the environment
# with no fallback, so there it is fatal like anything else.
@pytest.mark.parametrize("var", ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"])
@pytest.mark.parametrize("name", ["headless", "claude-sdk"])
def test_claude_runtimes_warn_but_do_not_refuse_on_a_missing_auth_var(name, var):
    """Refusing here would break every setup that authenticates via `claude login` — which is the
    common case, and was the case on the machine where this was found."""
    fatal, warn = RUNNERS[name].missing_credentials(_charter(f"env:{var}"), src={})
    assert fatal == [] and warn == [var]


def test_langchain_has_no_stored_login_so_its_auth_var_is_fatal():
    fatal, warn = RUNNERS["langchain"].missing_credentials(
        _charter("env:ANTHROPIC_API_KEY"), src={}
    )
    assert fatal == ["ANTHROPIC_API_KEY"] and warn == []


@pytest.mark.parametrize("name", ["headless", "claude-sdk"])
def test_a_non_auth_credential_is_still_fatal_alongside_a_warned_auth_var(name):
    """The mixed case is the real one: the run may authenticate, but it cannot do its job."""
    charter = _charter("env:CLAUDE_CODE_OAUTH_TOKEN", "env:ZYTE_API_KEY")
    fatal, warn = RUNNERS[name].missing_credentials(charter, src={})
    assert fatal == ["ZYTE_API_KEY"]
    assert warn == ["CLAUDE_CODE_OAUTH_TOKEN"]
