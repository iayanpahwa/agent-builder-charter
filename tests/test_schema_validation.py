"""Tests for the CHARTER schema enforcement in core/loader.py.

Covers: shipped charters validate, each schema violation is refused with
CharterInvalid, the custom egress semantics, the validate.py CLI, and the
fail-closed basics. No model calls, no network, no subprocess execution of
agents themselves.
"""

import copy
import os
import subprocess
import sys

import pytest
import yaml

from conftest import REPO_ROOT, SHIPPED_CHARTERS
from loader import CharterInvalid, load_charter, validate

# --- 1. Shipped charters are valid -----------------------------------------


@pytest.mark.parametrize("charter_path", SHIPPED_CHARTERS, ids=lambda p: p.name)
def test_shipped_charter_is_valid(charter_path):
    charter = load_charter(str(charter_path))
    assert isinstance(charter, dict)


# --- 2. Schema violations raise CharterInvalid -----------------------------

SCHEMA_VIOLATIONS = [
    pytest.param(lambda c: c["data"].__setitem__("class", "bogus"), "class", id="bad_data_class"),
    pytest.param(
        lambda c: c["sandbox"].__setitem__("isolation", "teleporter"),
        "isolation",
        id="bad_sandbox_isolation",
    ),
    pytest.param(lambda c: c.__setitem__("owner", {"team": "x"}), "owner", id="owner_missing_required"),
    pytest.param(lambda c: c.__setitem__("foo", "bar"), "foo", id="unknown_top_level_key"),
    pytest.param(lambda c: c.__delitem__("tools"), "tools", id="missing_tools"),
    pytest.param(lambda c: c.__setitem__("version", "1.0"), "version", id="bad_version_pattern"),
    pytest.param(lambda c: c.__setitem__("model", {"provider": "anthropic"}), "model", id="model_missing_id"),
    pytest.param(
        lambda c: c.__setitem__("evals", {"suite": "x"}),
        "success_metric",
        id="evals_missing_success_metric",
    ),
    pytest.param(lambda c: c.__setitem__("charter", "0.1"), "0.2", id="wrong_charter_schema_version"),
]


@pytest.mark.parametrize("mutate,expected_substring", SCHEMA_VIOLATIONS)
def test_schema_violation_raises(good_charter, mutate, expected_substring):
    mutate(good_charter)
    with pytest.raises(CharterInvalid) as exc_info:
        validate(good_charter)
    assert expected_substring in str(exc_info.value)


# --- 3. Egress custom semantics --------------------------------------------


@pytest.mark.parametrize(
    "egress,expected_substring",
    [
        pytest.param(["*.example.com", "any"], "any", id="mixed_any_and_domain"),
        pytest.param(["*"], "*", id="bare_star"),
        pytest.param([], None, id="empty_list"),
    ],
)
def test_egress_invalid(good_charter, egress, expected_substring):
    good_charter["egress"] = egress
    with pytest.raises(CharterInvalid) as exc_info:
        validate(good_charter)
    if expected_substring is not None:
        assert expected_substring in str(exc_info.value)


@pytest.mark.parametrize(
    "egress",
    [
        pytest.param(["any"], id="any_only"),
        pytest.param(["*.example.com"], id="domain_only"),
    ],
)
def test_egress_valid(good_charter, egress):
    good_charter["egress"] = egress
    validate(good_charter)  # must not raise


# --- 4. CLI (validate.py) via subprocess -----------------------------------


def _run_cli(charter_path):
    env = dict(os.environ, _ZO_DOCTOR="0")
    return subprocess.run(
        [sys.executable, "core/validate.py", str(charter_path)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_cli_valid_charter(tmp_path, good_charter):
    charter_file = tmp_path / "good.yaml"
    charter_file.write_text(yaml.safe_dump(good_charter))

    result = _run_cli(charter_file)

    assert result.returncode == 0
    assert "VALID" in result.stdout


def test_cli_invalid_charter(tmp_path, good_charter):
    good_charter["data"]["class"] = "bogus"
    charter_file = tmp_path / "bad.yaml"
    charter_file.write_text(yaml.safe_dump(good_charter))

    result = _run_cli(charter_file)

    assert result.returncode == 1
    assert "INVALID" in result.stdout


# --- 5. Fail-closed basics --------------------------------------------------


def test_load_nonexistent_charter_fails_closed():
    with pytest.raises(CharterInvalid) as exc_info:
        load_charter("/nonexistent/path.yaml")
    assert "no charter" in str(exc_info.value)


def test_charter_invalid_is_an_exception():
    assert issubclass(CharterInvalid, Exception)
