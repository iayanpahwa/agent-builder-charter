#!/usr/bin/env python3
"""
CHARTER reference loader — the smallest real thing that proves the bet.

The whole framework hinges on one claim: the loader is the ONLY door. An agent
gets a model, tools, secrets, network, or a place to run *only* through this code.
If it is not in the charter, the agent cannot do it.

This file enforces exactly three fields end to end, to settle three design
questions that could not be settled on paper:

  status  -> lives in a live REGISTRY, not the file. The charter declares the
             starting value; an operator flips the live value to pause a run
             mid-flight with no redeploy.
  tools   -> an allow-list. The one dispatcher below is the only way to call a
             tool, and it refuses anything not on the list.
  budget  -> hard per-run ceilings. The run is killed the moment one is hit.

Everything else in the charter is loaded and carried, but not yet enforced here.
The charter's full JSON Schema is enforced here too (fail closed): a charter that
violates the schema is refused before it can run.

Run:  python3 loader.py
"""

import copy
import os
import re

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

HERE = os.path.dirname(os.path.abspath(__file__))
CHARTER_PATH = os.path.join(HERE, "examples", "price-watch-scraper.charter.yaml")
SCHEMA_PATH = os.path.join(HERE, "charter.schema.yaml")

SCHEMA_VERSION = "0.2"

_EGRESS_HOST_RE = re.compile(
    r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*$"
)  # a domain label chain; '*.' prefix stripped before match

# Build the schema validator once. If the schema file can't be loaded, remember
# why and fail closed on every charter (in validate) instead of letting any through.
_VALIDATOR = None
_SCHEMA_ERROR = None
try:
    with open(SCHEMA_PATH) as _f:
        _VALIDATOR = Draft202012Validator(yaml.safe_load(_f))
except Exception as _e:  # noqa: BLE001 - any schema load failure must fail closed
    _SCHEMA_ERROR = _e


# --- fail-closed loader ---------------------------------------------------
class CharterInvalid(Exception):
    pass


def validate(doc):
    """The minimum bar. A charter that fails this does not run."""
    if _VALIDATOR is None:
        raise CharterInvalid(f"charter schema unavailable: {_SCHEMA_ERROR}")
    if not isinstance(doc, dict):
        raise CharterInvalid("charter is not a mapping")
    if doc.get("charter") != SCHEMA_VERSION:
        raise CharterInvalid(f"charter schema version must be {SCHEMA_VERSION!r}")
    # egress semantics not expressible in vanilla JSON Schema — checked BEFORE the
    # schema pass so an egress-specific friendly message wins over jsonschema's cryptic one.
    if "egress" in doc and isinstance(doc["egress"], list):
        egress = doc["egress"]
        if not egress:
            raise CharterInvalid(
                "egress must not be empty — give a domain list, or [any] (open) or [none] (no network)"
            )
        if "*" in egress:
            raise CharterInvalid(
                "egress may not contain a bare '*'; use 'any' to mean open, or list domains/patterns"
            )
        if "any" in egress and len(egress) != 1:
            raise CharterInvalid(
                "egress 'any' must be the only entry (a mixed list looks scoped but is open)"
            )
        if "none" in egress and len(egress) != 1:
            raise CharterInvalid(
                "egress 'none' must be the only entry (you can't declare no network and also allow domains)"
            )
        for e in egress:
            if e in ("any", "none"):
                continue
            host = e[2:] if e.startswith("*.") else e
            if not _EGRESS_HOST_RE.match(host):
                raise CharterInvalid(
                    f"egress entry {e!r} must be a domain like 'docs.python.org' "
                    f"or a wildcard like '*.example.com'"
                )
    error = best_match(_VALIDATOR.iter_errors(doc))
    if error is not None:
        raise CharterInvalid(f"{error.json_path}: {error.message}")


def load_charter(path):
    try:
        with open(path) as f:
            doc = yaml.safe_load(f)
    except FileNotFoundError:
        raise CharterInvalid(f"no charter at {path}")
    validate(doc)
    return doc


# --- the registry: where live status lives (NOT the file) -----------------
class Registry:
    def __init__(self):
        self._status = {}

    def register(self, charter):
        # seed live status from the charter's declared *starting* value
        self._status[charter["id"]] = charter["status"]

    def status(self, agent_id):
        return self._status[agent_id]

    def set_status(self, agent_id, value):
        # what an incident tool / operator calls to pause the whole fleet
        self._status[agent_id] = value


# --- the governed runtime: the only door ----------------------------------
class Paused(Exception):
    pass


class ToolDenied(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class GovernedRuntime:
    def __init__(self, charter, registry):
        self.c = charter
        self.reg = registry
        self.allowed = set(charter["tools"])
        self.budget = charter["budget"]
        self.spent_usd = 0.0
        self.spent_tokens = 0
        self.steps = 0
        self.on_step = None  # out-of-band hook the operator uses to intervene

    def _check_status(self):
        s = self.reg.status(self.c["id"])
        if s != "enabled":
            raise Paused(f"registry says status={s}")

    def _check_budget(self):
        b = self.budget
        if "steps" in b and self.steps > b["steps"]:
            raise BudgetExceeded(f"steps {self.steps} > {b['steps']}")
        if "usd" in b and self.spent_usd > b["usd"]:
            raise BudgetExceeded(f"usd {self.spent_usd:.2f} > {b['usd']}")
        if "tokens" in b and self.spent_tokens > b["tokens"]:
            raise BudgetExceeded(f"tokens {self.spent_tokens} > {b['tokens']}")

    def call_tool(self, name, usd=0.0, tokens=0):
        """The ONLY way the agent touches the outside world."""
        self._check_status()  # off switch, checked live, every step
        if name not in self.allowed:  # tools allow-list
            raise ToolDenied(f"'{name}' is not in the charter's tool allow-list")
        self.steps += 1
        self.spent_usd += usd
        self.spent_tokens += tokens
        self._check_budget()  # hard ceilings
        if self.on_step:  # operator intervention point
            self.on_step(self)
        return f"<result of {name}>"


# --- a fake agent that can ONLY act through the runtime -------------------
def demo_agent(rt):
    plan = [
        ("fetch_url", 0.06, 1500),
        ("parse_html", 0.02, 800),
        ("write_price_record", 0.01, 200),
    ]
    while True:
        for name, usd, tokens in plan:
            rt.call_tool(name, usd=usd, tokens=tokens)
            print(
                f"  step {rt.steps:>2}: {name:<20} ok   "
                f"(spent ${rt.spent_usd:.2f}, {rt.spent_tokens} tok)"
            )


def banner(text):
    print("\n" + text)
    print("-" * len(text))


def main():
    # Scenario 1 — the off switch actually switches, mid-run, from the registry
    banner("Scenario 1 - operator pauses the agent mid-run from the registry")
    charter = load_charter(CHARTER_PATH)
    reg = Registry()
    reg.register(charter)
    rt = GovernedRuntime(charter, reg)

    def operator(rt):
        if rt.steps == 3:
            rt.reg.set_status(rt.c["id"], "paused")
            print("  << operator sets status=paused in the registry >>")

    rt.on_step = operator
    try:
        demo_agent(rt)
    except Paused as e:
        print(f"  HALTED: {e} - run stopped cleanly, no file edit, no deploy")

    # Scenario 2 — a runaway agent hits its budget ceiling and is killed
    banner("Scenario 2 - a runaway agent hits its budget ceiling")
    charter = load_charter(CHARTER_PATH)
    reg = Registry()
    reg.register(charter)
    rt = GovernedRuntime(charter, reg)
    try:
        demo_agent(rt)
    except BudgetExceeded as e:
        print(f"  KILLED: {e} - the run is stopped on the spot")

    # Scenario 3 — the agent tries a tool that is not in its charter
    banner("Scenario 3 - the agent tries a tool that is not in its charter")
    charter = load_charter(CHARTER_PATH)
    reg = Registry()
    reg.register(charter)
    rt = GovernedRuntime(charter, reg)
    try:
        rt.call_tool("send_email")
    except ToolDenied as e:
        print(f"  DENIED: {e}")

    # Scenario 4 — a broken charter fails closed
    banner("Scenario 4 - a broken charter fails closed")
    broken = copy.deepcopy(load_charter(CHARTER_PATH))
    del broken["tools"]
    try:
        validate(broken)
        print("  started (THIS WOULD BE A BUG)")
    except CharterInvalid as e:
        print(f"  REFUSED TO START: {e}")

    print()


if __name__ == "__main__":
    main()
