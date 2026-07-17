"""Tests for core/registry.py's fail-closed duplicate-id handling.

Registry.register() must refuse a second agent that tries to register under an id
already in the fleet, rather than silently overwriting the first one. These tests
call the Registry API directly — no subprocess, no network, no tokens.
"""

import copy

import pytest
import yaml

from registry import DuplicateAgentId, Registry


def _distinct(good_charter, aid):
    charter = copy.deepcopy(good_charter)
    charter["id"] = aid
    return charter


# --- 1. distinct ids register fine ------------------------------------------


def test_distinct_ids_register_fine(good_charter):
    reg = Registry()
    charter_a = _distinct(good_charter, "agent-a")
    charter_b = _distinct(good_charter, "agent-b")

    reg.register(charter_a)
    reg.register(charter_b)

    assert len(reg.all()) == 2
    assert reg.where("id", "agent-a")[0]["charter"]["id"] == "agent-a"
    assert reg.where("id", "agent-b")[0]["charter"]["id"] == "agent-b"


# --- 2. duplicate id raises DuplicateAgentId --------------------------------


def test_duplicate_id_raises(good_charter):
    reg = Registry()
    charter = _distinct(good_charter, "agent-dup")
    reg.register(charter)

    with pytest.raises(DuplicateAgentId):
        reg.register(copy.deepcopy(charter))


# --- 3. refusal message names the id and both sources -----------------------


def test_duplicate_message_names_id_and_both_sources(good_charter):
    reg = Registry()
    charter = _distinct(good_charter, "agent-dup")
    reg.register(charter, source="first.yaml")

    with pytest.raises(DuplicateAgentId) as exc_info:
        reg.register(copy.deepcopy(charter), source="second.yaml")

    message = str(exc_info.value)
    assert "agent-dup" in message
    assert "first.yaml" in message
    assert "second.yaml" in message


# --- 4. first registration survives (not clobbered) -------------------------


def test_first_registration_survives_duplicate_attempt(good_charter):
    reg = Registry()
    original = _distinct(good_charter, "agent-dup")
    original["owner"]["team"] = "original-team"
    reg.register(original, source="first.yaml")

    imposter = _distinct(good_charter, "agent-dup")
    imposter["owner"]["team"] = "imposter-team"

    with pytest.raises(DuplicateAgentId):
        reg.register(imposter, source="second.yaml")

    stored = reg.where("id", "agent-dup")[0]
    assert stored["source"] == "first.yaml"
    assert stored["charter"]["owner"]["team"] == "original-team"


# --- 5. load_dir surfaces a real collision -----------------------------------


def test_load_dir_surfaces_collision(tmp_path, good_charter):
    charter = _distinct(good_charter, "agent-dup")

    sub1 = tmp_path / "sub1"
    sub2 = tmp_path / "sub2"
    sub1.mkdir()
    sub2.mkdir()
    (sub1 / "charter.yaml").write_text(yaml.safe_dump(charter))
    (sub2 / "charter.yaml").write_text(yaml.safe_dump(charter))

    reg = Registry()
    with pytest.raises(DuplicateAgentId):
        reg.load_dir(str(tmp_path))


# --- 6. registering a fresh id after a rejected duplicate still works -------


def test_registry_usable_after_rejected_duplicate(good_charter):
    reg = Registry()
    charter_a = _distinct(good_charter, "agent-a")
    reg.register(charter_a)

    with pytest.raises(DuplicateAgentId):
        reg.register(copy.deepcopy(charter_a))

    charter_b = _distinct(good_charter, "agent-b")
    reg.register(charter_b)

    assert len(reg.all()) == 2
