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
