#!/usr/bin/env python3
"""
CHARTER reference registry — the live picture of the whole fleet.

The charter file is the *desired* state. The registry is the *current* state: it
holds every agent's charter plus its live `status`, and it is what fleet-wide
questions are asked against. This is the piece that makes the headline promise
real: "pause every agent that touches customer data" is one query here, not a
thousand file edits.

A production version would be a database. This one is in memory, but the API is
the point: register (fail closed), query by any field, and set status in bulk.

Run:  python3 registry.py
"""

import copy
import os

from loader import load_charter, validate


class DuplicateAgentId(Exception):
    """A second agent tried to register under an id already in the fleet."""


class Registry:
    def __init__(self):
        self._agents = {}  # id -> {"charter", "status", "source"}

    # --- writes: getting charters in ---
    def register(self, charter, source="<memory>"):
        validate(charter)  # fail closed: a bad charter never enters the fleet
        aid = charter["id"]
        if aid in self._agents:  # fail closed: ids must be unique across the fleet
            existing = self._agents[aid]["source"]
            raise DuplicateAgentId(
                f"agent id {aid!r} is already registered (from {existing}); "
                f"refusing the duplicate from {source} — ids must be unique across the fleet"
            )
        self._agents[aid] = {
            "charter": charter,
            "status": charter["status"],  # seed live status from the declared start
            "source": source,
        }
        return aid

    def load_dir(self, root):
        """Register every *charter.yaml found under a directory tree."""
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if name.endswith("charter.yaml"):
                    path = os.path.join(dirpath, name)
                    self.register(load_charter(path), source=path)

    # --- reads: asking the fleet questions ---
    def all(self):
        return list(self._agents.values())

    def _read(self, rec, field_path):
        """Read a dotted field like 'data.class'. status comes from live state."""
        if field_path == "status":
            return rec["status"]
        node = rec["charter"]
        for part in field_path.split("."):
            if not isinstance(node, dict):
                return None
            node = node.get(part)
        return node

    def where(self, field_path, value):
        """Every agent whose field equals value."""
        return [r for r in self._agents.values() if self._read(r, field_path) == value]

    # --- writes: the live control plane ---
    def set_status(self, ids, status):
        if status not in ("enabled", "paused", "killed"):
            raise ValueError(f"bad status {status!r}")
        for aid in ids:
            self._agents[aid]["status"] = status

    def status(self, aid):
        return self._agents[aid]["status"]


# --- demo ------------------------------------------------------------------
def _variant(base, aid, team, data_class, model_id):
    v = copy.deepcopy(base)
    v["id"] = aid
    v["version"] = "1"
    v["owner"] = {"team": team, "on_call": f"@{team}-oncall", "escalation": f"{team}-lead@..."}
    v["data"] = {**v["data"], "class": data_class}
    v["model"] = {**v["model"], "id": model_id}
    return v


def _table(reg):
    rows = []
    for rec in reg.all():
        c = rec["charter"]
        rows.append((c["id"], c["owner"]["team"], c["data"]["class"],
                     c["model"]["id"], rec["status"]))
    w = [max(len(str(r[i])) for r in rows + [("id", "team", "data", "model", "status")])
         for i in range(5)]
    head = ("id", "team", "data.class", "model.id", "status")
    print("  " + "  ".join(h.ljust(w[i]) for i, h in enumerate(head)))
    print("  " + "  ".join("-" * w[i] for i in range(5)))
    for r in sorted(rows):
        print("  " + "  ".join(str(r[i]).ljust(w[i]) for i in range(5)))


def banner(text):
    print("\n" + text)
    print("-" * len(text))


HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "examples", "price-watch-scraper.charter.yaml")
OLD_MODEL = "claude-haiku-4-3"  # a retired model, for the drift query


def main():
    reg = Registry()

    # A real charter file, plus a few synthesized variants to make a varied fleet.
    base = load_charter(BASE)
    reg.register(base, source=BASE)
    reg.register(_variant(base, "lead-enricher",     "growth",  "pii",          "claude-haiku-4-5"))
    reg.register(_variant(base, "invoice-reconciler","finance", "confidential", "claude-opus-4-8"))
    reg.register(_variant(base, "support-triage",    "support", "pii",          OLD_MODEL))
    reg.register(_variant(base, "docs-indexer",      "docs",    "public",       "claude-haiku-4-5"))

    banner("The fleet")
    _table(reg)

    banner("Incident: pause every agent that touches customer data (data.class == pii)")
    hits = reg.where("data.class", "pii")
    ids = [r["charter"]["id"] for r in hits]
    print(f"  matched: {ids}")
    reg.set_status(ids, "paused")
    print("  -> set to paused in one write")
    _table(reg)

    banner(f"Audit: which agents still run the old model ({OLD_MODEL})?")
    stale = [r["charter"]["id"] for r in reg.where("model.id", OLD_MODEL)]
    print(f"  {stale}")

    banner("Ownership: who owns the finance agents?")
    for r in reg.where("owner.team", "finance"):
        o = r["charter"]["owner"]
        print(f"  {r['charter']['id']}: on_call {o['on_call']}, escalate {o['escalation']}")

    print()


if __name__ == "__main__":
    main()
