"""The redact patterns this repo ships are copied by everyone who reads an example.

A pattern that matches too much is not a harmless over-correction: a real build declared
`(?i)zyte[_-]?api[_-]?key\\s*[:=]\\s*\\S+`, which matched the shell VARIABLE REFERENCE
`$ZYTE_API_KEY:` and rewrote traces into unreadable `$[REDACTED]`. The trace is the thing you
read when a run goes wrong, so an over-matching pattern costs you the diagnosis while protecting
nothing that was not already protected — declared credential VALUES are scrubbed separately.

So the examples must match a key-shaped value, never a bare word.
"""

import re

import pytest
import yaml
from conftest import SHIPPED_CHARTERS

# Ordinary text a log or a fetched page might contain. None of it is a secret.
PROSE = [
    "The API returned a token and set a cookie.",
    "See the api_key docs for how authentication works.",
    "Set ANTHROPIC_API_KEY in your environment before running.",
    'curl -H "Authorization: Bearer $TOKEN" https://example.com/api_key/info',
    "token: the string used to authenticate; cookie: session state",
]

# Things that ARE secrets and must not survive.
SECRETS = [
    "api_key=A1b2C3d4E5f6G7h8J9k0",
    "API-Key: sk_live_9f8e7d6c5b4a3f2e1d0c",
    "token=ZZZZbbbbCCCCddddEEEEffff",
    "cookie=sess_aaaaaaaaaaaaaaaaaaaaaaaa",
]


def _patterns():
    for path in SHIPPED_CHARTERS:
        with open(path) as f:
            charter = yaml.safe_load(f)
        for pat in (charter.get("data") or {}).get("redact") or []:
            yield path.name, pat


CASES = list(_patterns())


@pytest.mark.parametrize("name,pat", CASES, ids=[f"{n}:{p[:30]}" for n, p in CASES])
def test_pattern_compiles(name, pat):
    """A pattern that will not compile falls back to a literal substring match, which is both
    silent and much broader than intended."""
    re.compile(pat)


@pytest.mark.parametrize("name,pat", CASES, ids=[f"{n}:{p[:30]}" for n, p in CASES])
def test_pattern_does_not_eat_ordinary_prose(name, pat):
    rx = re.compile(pat)
    for text in PROSE:
        assert not rx.search(text), f"{name}: {pat!r} matches non-secret text {text!r}"


@pytest.mark.parametrize("name,pat", CASES, ids=[f"{n}:{p[:30]}" for n, p in CASES])
def test_pattern_requires_a_value_not_just_a_name(name, pat):
    """The property that makes the above hold in general: a length floor on the value. Without
    one, `key\\s*[:=]\\s*\\S+` still matches `$MY_API_KEY:` in a shell command."""
    assert re.search(
        r"\{\d+,\d*\}", pat
    ), f"{name}: {pat!r} has no minimum length on the value; anchor it with e.g. {{16,}}"


def test_at_least_one_shipped_charter_demonstrates_redaction():
    """If every example ships `redact: []` there is nothing to copy, and the first person who
    needs it invents a bare-word pattern."""
    assert CASES, "no shipped charter demonstrates a redact pattern"


@pytest.mark.parametrize("secret", SECRETS)
def test_the_shipped_patterns_together_catch_real_secrets(secret):
    """Being narrow is only worth anything if they still work."""
    assert any(re.search(pat, secret) for _, pat in CASES), f"nothing redacts {secret!r}"
