#!/usr/bin/env python3
"""
cc_guard — a PreToolUse hook that enforces a charter's EGRESS on network tools.

`run_headless.py` passes this to `claude -p` via `--settings`, with a matcher limited to
WebFetch|WebSearch. Because a headless run is its own isolated process, the hook is
process-scoped and clean: it gates only that agent's web fetches, with no parent session to
deadlock. The tool allow-list is enforced separately (by --allowedTools / --disallowedTools);
this guard deliberately does NOT touch tools — an earlier `matcher: "*"` tool-denying hook
deadlocked interactive sessions, so egress is all it does.

For network tools it denies URLs whose host is not in the charter's egress list; `egress: [any]`
means open (allow all). The settings block it is wired with:
  {"hooks": {"PreToolUse": [
    {"matcher": "WebFetch|WebSearch", "hooks": [
      {"type": "command",
       "command": "python3 /abs/cc_guard.py --charter /abs/agents/<id>/charter.yaml"}]}]}}
"""

import argparse
import json
import sys
from urllib.parse import urlparse

import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "core"))
from loader import load_charter   # noqa: E402

# Claude Code tools that reach the network, and where the URL sits in tool_input.
NET_TOOLS = {"WebFetch": "url", "WebSearch": None}


def decision(allow, reason):
    """Emit a Claude Code PreToolUse permission decision and exit."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow" if allow else "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def host_allowed(host, egress):
    for pattern in egress:
        bare = pattern[2:] if pattern.startswith("*.") else pattern
        if not bare:
            continue
        if host == pattern or host == bare or host.endswith("." + bare):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--charter", required=True)
    args = ap.parse_args()

    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        decision(True, "cc_guard: no parseable tool event; not blocking")

    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input", {}) or {}

    # Only network tools are gated. Everything else ALWAYS passes — this is what keeps
    # the hook from ever deadlocking the session (Bash/Read/Edit/Task are never denied).
    if tool not in NET_TOOLS:
        decision(True, f"cc_guard: '{tool}' is not a network tool; egress hook does not gate it")

    try:
        charter = load_charter(args.charter)   # fail closed if the charter is invalid
    except Exception as e:  # noqa: BLE001 - any load failure must fail closed
        decision(False, f"cc_guard: cannot load charter ({e}); failing closed on this network call")

    egress = charter.get("egress", []) or []
    if "any" in egress:
        decision(True, f"cc_guard: egress is open ([any]); '{tool}' allowed by charter '{charter['id']}'")
    if "none" in egress:
        decision(False, f"cc_guard: egress is [none] (no network); '{tool}' denied by charter '{charter['id']}'")

    key = NET_TOOLS[tool]
    url = tool_input.get(key) if key else None
    if not url:
        # Reached only with a scoped egress list ([any]/[none] handled above). A tool with no
        # checkable URL (WebSearch) can't be confined to an allow-list — deny it. Use egress:[any]
        # to permit open search, or drop WebSearch from tools.
        decision(False, f"cc_guard: '{tool}' has no host to check against egress {egress}; a scoped allow-list can't confine it — use egress:[any] to permit it or drop {tool}")

    host = urlparse(url).hostname or ""
    if host_allowed(host, egress):
        decision(True, f"cc_guard: egress to '{host}' allowed by charter '{charter['id']}'")
    decision(False, f"cc_guard: egress to '{host}' not in charter egress {egress}")


if __name__ == "__main__":
    main()
