"""Shared fixtures for the charter schema-validation test suite.

Puts the repo's core/ directory on sys.path (independent of CWD) so tests can
`import loader` the same way core/validate.py does.
"""

import copy
import pathlib
import sys

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE_DIR = REPO_ROOT / "core"

if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

SENTIMENT_TAGGER_CHARTER = (
    REPO_ROOT / "builders" / "claude-headless" / "agents" / "sentiment-tagger" / "charter.yaml"
)

# The charters shipped in the repo today; every one of them must validate.
SHIPPED_CHARTERS = [
    REPO_ROOT / "core" / "examples" / "price-watch-scraper.charter.yaml",
    REPO_ROOT / "builders" / "claude-headless" / "examples" / "repo-researcher.charter.yaml",
    REPO_ROOT / "builders" / "langchain" / "examples" / "langchain-docs-researcher.charter.yaml",
    SENTIMENT_TAGGER_CHARTER,
]

with open(SENTIMENT_TAGGER_CHARTER) as _f:
    _GOOD_CHARTER = yaml.safe_load(_f)


@pytest.fixture
def good_charter():
    """A fresh, mutable copy of a known-valid charter for each test."""
    return copy.deepcopy(_GOOD_CHARTER)


# The three runners, for tests that assert all builders behave identically. Each is a script
# rather than an installed module, so it is loaded by path.
RUNNER_PATHS = {
    "headless": REPO_ROOT / "builders" / "claude-headless" / "run_headless.py",
    "claude-sdk": REPO_ROOT / "builders" / "claude-sdk" / "example.agent.py",
    "langchain": REPO_ROOT / "builders" / "langchain" / "example.agent.py",
}


def runner_modules(prefix="runner"):
    """{name: module} for all three runners. The module name is prefixed per caller so two test
    files loading the same script don't collide in sys.modules."""
    import importlib.util

    mods = {}
    for name, path in RUNNER_PATHS.items():
        mod_name = f"{prefix}_{name.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods
