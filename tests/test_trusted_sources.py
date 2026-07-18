"""Tests for the trusted_sources path-traversal confinement (fix/trusted-sources-confinement).

Two layers under test:

1. Schema (defense in depth): core/charter.schema.yaml's context.trusted_sources
   pattern rejects absolute paths and ".." segments, so validate() raises
   CharterInvalid before an agent is ever loaded.
2. Runtime (authoritative): run_headless.py's _system_prompt_file() resolves every
   source with realpath and raises CharterInvalid if it escapes the charter dir —
   including via a symlink whose name looks perfectly clean, which the schema
   pattern cannot see through.

No model calls, no network, no subprocess execution of agents themselves.
"""

import copy
import os
import sys

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "builders" / "claude-headless"))
from run_headless import _system_prompt_file  # noqa: E402
from loader import CharterInvalid, validate  # noqa: E402


# --- 1. Schema layer: invalid trusted_sources are rejected ------------------

INVALID_TRUSTED_SOURCES = [
    pytest.param(["/etc/passwd"], id="absolute_path"),
    pytest.param(["../x"], id="parent_escape"),
    pytest.param(["a/../b"], id="embedded_parent_escape"),
]


@pytest.mark.parametrize("trusted_sources", INVALID_TRUSTED_SOURCES)
def test_schema_rejects_invalid_trusted_source(good_charter, trusted_sources):
    charter = copy.deepcopy(good_charter)
    charter["context"] = {"trusted_sources": trusted_sources}
    with pytest.raises(CharterInvalid):
        validate(charter)


# --- 2. Schema layer: valid trusted_sources are accepted --------------------

VALID_TRUSTED_SOURCES = [
    pytest.param(["prompts/system.md"], id="relative_path"),
    pytest.param(["..foo.md"], id="dotdot_prefixed_filename_not_traversal"),
]


@pytest.mark.parametrize("trusted_sources", VALID_TRUSTED_SOURCES)
def test_schema_accepts_valid_trusted_source(good_charter, trusted_sources):
    charter = copy.deepcopy(good_charter)
    charter["context"] = {"trusted_sources": trusted_sources}
    validate(charter)  # must not raise


# --- 3. Runtime layer: absolute / parent escapes are rejected ---------------


def test_runtime_absolute_escape_raises(tmp_path):
    charter = {"context": {"trusted_sources": ["/etc/hostname"]}}
    with pytest.raises(CharterInvalid):
        _system_prompt_file(charter, str(tmp_path))


def test_runtime_parent_escape_raises(tmp_path):
    charter = {"context": {"trusted_sources": ["../../etc/hostname"]}}
    with pytest.raises(CharterInvalid):
        _system_prompt_file(charter, str(tmp_path))


# --- 4. Runtime layer: symlink escape (the case the schema can't catch) -----


def test_runtime_symlink_escape_raises(tmp_path, tmp_path_factory):
    outside_dir = tmp_path_factory.mktemp("outside")
    secret = outside_dir / "secret.txt"
    secret.write_text("outside-secret-content-should-never-be-read")

    link = tmp_path / "leak.md"
    os.symlink(secret, link)

    charter = {"context": {"trusted_sources": ["leak.md"]}}
    with pytest.raises(CharterInvalid):
        _system_prompt_file(charter, str(tmp_path))


# --- 5. Runtime layer: a genuinely valid source is read ---------------------


def test_runtime_valid_source_is_read(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    known_text = "KNOWN_SYSTEM_PROMPT_TEXT_12345"
    (prompts_dir / "system.md").write_text(known_text)

    charter = {"context": {"trusted_sources": ["prompts/system.md"]}}
    result = _system_prompt_file(charter, str(tmp_path))

    assert result is not None
    with open(result) as f:
        content = f.read()
    assert known_text in content
    os.remove(result)


# --- 6. Runtime layer: no sources means no system prompt file --------------


@pytest.mark.parametrize(
    "charter",
    [
        pytest.param({"context": {"trusted_sources": []}}, id="empty_trusted_sources"),
        pytest.param({}, id="missing_context"),
    ],
)
def test_runtime_no_sources_returns_none(tmp_path, charter):
    assert _system_prompt_file(charter, str(tmp_path)) is None


# --- 7. Runtime layer: a missing trusted source fails closed ----------------


def test_runtime_missing_source_raises(tmp_path, good_charter):
    """A trusted_sources entry that names a file not present on disk (but inside
    the charter dir, so it isn't caught by the escape check) must fail closed
    with CharterInvalid rather than silently dropping the source."""
    charter = copy.deepcopy(good_charter)
    charter["context"]["trusted_sources"] = ["prompts/does-not-exist.md"]
    with pytest.raises(CharterInvalid):
        _system_prompt_file(charter, str(tmp_path))


def test_runtime_existing_source_still_read(tmp_path, good_charter):
    """Regression: a trusted_sources file that DOES exist still works — the
    fail-closed fix for missing files must not break the happy path."""
    charter = copy.deepcopy(good_charter)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    known_text = "REGRESSION_TRUSTED_SOURCE_EXISTS_67890"
    (prompts_dir / "system.md").write_text(known_text)
    charter["context"]["trusted_sources"] = ["prompts/system.md"]

    result = _system_prompt_file(charter, str(tmp_path))
    try:
        assert result is not None
        with open(result) as f:
            content = f.read()
        assert known_text in content
    finally:
        os.remove(result)
