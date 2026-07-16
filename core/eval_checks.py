#!/usr/bin/env python3
"""
eval_checks.py — deterministic invariant checks for an agent's output.

Pure functions. No model calls, no cost. Used by run_headless.py (the completion gate)
and by the run-evals skill. Each check reads the output text and returns (ok, detail).

cases.yaml invariant vocabulary (each invariant is a single-key dict):
  - contains: "<s>"         output must contain the substring
  - not_contains: "<s>"     output must NOT contain the substring
  - matches: "<regex>"      output must match the regex (search)
  - not_matches: "<regex>"  output must NOT match the regex
  - contains_url: true      output has at least one http(s):// URL
  - valid_json: true        output parses as JSON
  - claims_cited: [kw, ...]  every line mentioning one of these keywords must carry a URL
                             (catches an unsourced figure — the classic failure)
"""

import json
import re

_URL = re.compile(r"https?://", re.I)


def _contains(out, v):
    ok = str(v) in out
    return ok, (None if ok else f"missing substring {v!r}")


def _not_contains(out, v):
    ok = str(v) not in out
    return ok, (None if ok else f"contains forbidden substring {v!r}")


def _matches(out, v):
    ok = re.search(str(v), out) is not None
    return ok, (None if ok else f"no match for /{v}/")


def _not_matches(out, v):
    m = re.search(str(v), out)
    return (m is None), (None if m is None else f"matched forbidden /{v}/: {m.group(0)!r}")


def _contains_url(out, v):
    ok = bool(_URL.search(out))
    return ok, (None if ok else "no http(s):// URL in output")


def _valid_json(out, v):
    try:
        json.loads(out)
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, f"not valid JSON ({e})"


def _claims_cited(out, keywords):
    kws = [str(k).lower() for k in (keywords or [])]
    bad = [ln for ln in out.splitlines()
           if any(k in ln.lower() for k in kws) and not _URL.search(ln)]
    return (not bad), (None if not bad else f"uncited claim line: {bad[0].strip()!r}")


_CHECKS = {
    "contains": _contains,
    "not_contains": _not_contains,
    "matches": _matches,
    "not_matches": _not_matches,
    "contains_url": _contains_url,
    "valid_json": _valid_json,
    "claims_cited": _claims_cited,
}


def check_one(invariant, output):
    """invariant: a single-key dict like {'contains_url': True}. Returns (name, ok, detail)."""
    if not isinstance(invariant, dict) or len(invariant) != 1:
        return str(invariant), False, "malformed invariant (want a single-key mapping)"
    (name, value), = invariant.items()
    fn = _CHECKS.get(name)
    if fn is None:
        return name, False, f"unknown invariant type {name!r}"
    ok, detail = fn(output, value)
    return name, ok, detail


def check_all(invariants, output):
    """Returns [(name, ok, detail), ...] for each invariant in the list."""
    return [check_one(inv, output) for inv in (invariants or [])]
