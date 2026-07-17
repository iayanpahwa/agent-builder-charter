#!/usr/bin/env python3
"""
run_headless.py — turn a CHARTER charter into an ENFORCED headless `claude -p` run.

This IS the loader for the headless runtime: the charter is the only source of the
agent's model, tools, network, turns, timeout, and (scoped) environment. The process
this script launches is the agent. The agent's own `run.sh` just calls this.

Real walls in headless (this is why headless beats the interactive-subagent path):
  model                      -> --model
  tools                      -> --allowedTools + --disallowedTools (dangerous tools denied)
  budget.steps               -> --max-turns   (hard stop)
  budget.wall_clock_seconds  -> subprocess timeout (hard kill)
  egress (specific list)     -> --settings PreToolUse hook (cc_guard); process-scoped, clean
  context.trusted_sources    -> --append-system-prompt-file (concatenated)
  status                     -> refuses to run unless 'enabled'
Honest limits (flags can't; a container can):
  budget.usd  -> --max-budget-usd, but MAY be a no-op under subscription auth (verify).
  credentials -> only declared creds injected; TRUE isolation needs a clean/container env.
  sandbox     -> a real fs/net jail needs a container.

Flags follow the current docs — VERIFY periodically, they drift:
  https://code.claude.com/docs/en/cli-reference.md
  https://code.claude.com/docs/en/headless.md

Usage:
  python3 run_headless.py --charter <path> --trigger <str> [--prompt <task>] [--eval] [--dry-run]
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "core"))   # shared runtime-neutral engine
from loader import CharterInvalid, load_charter   # noqa: E402
import eval_checks                                 # noqa: E402

DANGEROUS = ["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "Task"]
NET_TOOLS = {"WebFetch", "WebSearch"}


# --- logging --------------------------------------------------------------
def _logdir(charter_dir):
    d = os.path.join(charter_dir, "logs")
    os.makedirs(d, exist_ok=True)
    return d


def log_run(charter_dir, entry):
    with open(os.path.join(_logdir(charter_dir), "runs.jsonl"), "a") as f:
        f.write(json.dumps(entry) + "\n")


def save_output(charter_dir, text):
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    p = os.path.join(_logdir(charter_dir), f"{ts}.output.txt")
    with open(p, "w") as f:
        f.write(text)
    return p


# --- building the enforced command ---------------------------------------
def _system_prompt_file(charter, charter_dir):
    srcs = (charter.get("context") or {}).get("trusted_sources", []) or []
    text = ""
    for s in srcs:
        p = os.path.join(charter_dir, s)
        if os.path.exists(p):
            with open(p) as f:
                text += f.read() + "\n\n"
    if not text.strip():
        return None
    fd, path = tempfile.mkstemp(suffix=".sysprompt.txt")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return path


def _mcp_config_file(charter):
    """Write ONLY the charter's MCP servers; return (path, allowed-tool-patterns)."""
    mcp = charter.get("mcp") or []
    if not mcp:
        return None, []
    servers, allowed = {}, []
    for s in mcp:
        servers[s["name"]] = s.get("server", {})
        allow = s.get("allow") or ["*"]
        if "*" in allow:
            allowed.append(f"mcp__{s['name']}__*")
        else:
            allowed += [f"mcp__{s['name']}__{t}" for t in allow]
    fd, path = tempfile.mkstemp(suffix=".mcp.json")
    with os.fdopen(fd, "w") as f:
        json.dump({"mcpServers": servers}, f)
    return path, allowed


def _settings_file(charter, charter_dir):
    """One --settings file merging the egress hook and the skills allow-list."""
    settings = {}
    egress = charter.get("egress", []) or []
    tools = charter.get("tools", []) or []
    if (NET_TOOLS & set(tools)) and egress and "any" not in egress:
        guard = os.path.join(HERE, "cc_guard.py")
        charter_abs = os.path.join(charter_dir, "charter.yaml")
        settings["hooks"] = {"PreToolUse": [
            {"matcher": "WebFetch|WebSearch", "hooks": [
                {"type": "command", "command": f"python3 {guard} --charter {charter_abs}"}]}]}
    if charter.get("skills"):
        settings["availableSkills"] = charter["skills"]   # restrict to ONLY these
    if not settings:
        return None
    fd, path = tempfile.mkstemp(suffix=".settings.json")
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f)
    return path


def build_command(charter, charter_dir, prompt):
    tools = list(charter.get("tools", []) or [])
    budget = charter.get("budget") or {}
    cmd = ["claude", "-p", prompt,
           "--model", charter["model"]["id"],
           "--permission-mode", "bypassPermissions",
           "--output-format", "json"]
    mcp_file, mcp_allowed = _mcp_config_file(charter)
    if mcp_file:
        cmd += ["--mcp-config", mcp_file, "--strict-mcp-config"]   # ONLY charter servers
    allowed = tools + mcp_allowed
    if allowed:
        cmd += ["--allowedTools", ",".join(allowed)]
        denied = [t for t in DANGEROUS if t not in tools]
        if denied:
            cmd += ["--disallowedTools", ",".join(denied)]
    else:
        cmd += ["--disallowedTools", "*"]     # text-only agent: no tools at all
    if budget.get("steps"):
        cmd += ["--max-turns", str(budget["steps"])]
    sys_file = _system_prompt_file(charter, charter_dir)
    if sys_file:
        cmd += ["--append-system-prompt-file", sys_file]
    settings_file = _settings_file(charter, charter_dir)
    if settings_file:
        cmd += ["--settings", settings_file]
    if budget.get("usd"):
        cmd += ["--max-budget-usd", str(budget["usd"])]
    return cmd, [f for f in (sys_file, settings_file, mcp_file) if f]


def report(charter):
    tools = charter.get("tools", []) or []
    egress = charter.get("egress", []) or []
    budget = charter.get("budget") or {}
    net = bool(NET_TOOLS & set(tools))
    rows = [
        ("WALL", "model", "--model pins it"),
        ("WALL", "tools", "--allowedTools + --disallowedTools (dangerous tools denied)"),
    ]
    rows.append(("WALL", "budget.steps", "--max-turns hard-stops the run")
                if budget.get("steps") else ("—", "budget.steps", "not set"))
    rows.append(("WALL", "budget.wall_clock", "subprocess timeout hard-kills the run")
                if budget.get("wall_clock_seconds") else ("—", "budget.wall_clock", "not set (default 120s)"))
    if egress == ["none"]:
        rows.append(("WALL", "egress", "[none]: no network permitted — all WebFetch/WebSearch denied"))
    elif net and egress and "any" not in egress:
        rows.append(("WALL", "egress", "--settings hook denies off-list WebFetch (process-scoped, clean)"))
    elif "any" in egress:
        rows.append(("NOT-ENFORCED", "egress", "open ([any]) — nothing to gate"))
    else:
        rows.append(("NOT-ENFORCED", "egress", "no network tool — nothing to gate"))
    if charter.get("mcp"):
        names = ", ".join(s.get("name", "?") for s in charter["mcp"])
        rows.append(("WALL", "mcp", f"only [{names}] loaded (--strict-mcp-config); each runs its OWN code/secrets — you're trusting it"))
    if charter.get("skills"):
        rows.append(("WALL", "skills", f"restricted to {charter['skills']} (settings.availableSkills; version-dependent)"))
    if budget.get("usd"):
        rows.append(("ADVISORY", "budget.usd", f"--max-budget-usd {budget['usd']} — MAY be a no-op under subscription auth; verify"))
    rows += [
        ("DECLARED", "credentials", "only declared creds injected; TRUE isolation needs a clean/container env"),
        ("UNAVAILABLE", "sandbox", "a real fs/net jail needs a container; flags can't"),
    ]
    return rows


def _shellish(s):
    return shlex.quote(s) if (not s or any(c in s for c in ' "\'\n\t')) else s


def _cleanup(paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


# --- the run --------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--charter", required=True)
    ap.add_argument("--trigger", default="manual")
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    charter_path = os.path.abspath(args.charter)
    charter_dir = os.path.dirname(charter_path)
    now = lambda: datetime.now().isoformat(timespec="seconds")  # noqa: E731

    try:
        charter = load_charter(charter_path)
    except CharterInvalid as e:
        print(f"REFUSED: invalid charter ({e})")
        sys.exit(2)
    name = charter["id"]

    if charter.get("status") != "enabled":
        print(f"REFUSED: status={charter.get('status')} (not enabled)")
        log_run(charter_dir, {"name": name, "timestamp": now(), "trigger": args.trigger,
                              "outcome": "refused", "reason": f"status={charter.get('status')}"})
        sys.exit(3)

    prompt = args.prompt
    if prompt is None:
        tp = os.path.join(charter_dir, "prompts", "task.md")
        if os.path.exists(tp):
            with open(tp) as f:
                prompt = f.read().strip()
    if not prompt:
        print("REFUSED: no task prompt (pass --prompt, or add prompts/task.md)")
        sys.exit(4)

    cmd, tmpfiles = build_command(charter, charter_dir, prompt)

    print(f"\n=== headless run: {name} (trigger={args.trigger}) ===")
    print("what this run enforces:")
    for status, field, note in report(charter):
        print(f"  {status:<13} {field:<20} {note}")
    print("\ncommand:")
    print("  " + " ".join(_shellish(c) for c in cmd))

    if args.dry_run:
        print("\n[dry-run] not executed.")
        _cleanup(tmpfiles)
        return

    timeout = (charter.get("budget") or {}).get("wall_clock_seconds", 120)
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              env=os.environ.copy())  # claude needs its auth env; container = real isolation
        elapsed = round(time.time() - t0, 1)
    except subprocess.TimeoutExpired:
        elapsed = round(time.time() - t0, 1)
        print(f"\nKILLED: wall-clock timeout {timeout}s hit")
        log_run(charter_dir, {"name": name, "timestamp": now(), "trigger": args.trigger,
                              "model": charter["model"]["id"], "outcome": "timeout", "duration_s": elapsed})
        _cleanup(tmpfiles)
        sys.exit(5)
    except FileNotFoundError:
        print("REFUSED: `claude` CLI not found on PATH.")
        _cleanup(tmpfiles)
        sys.exit(6)
    _cleanup(tmpfiles)

    result, cost = "", None
    try:
        data = json.loads(proc.stdout)
        result = data.get("result", "") or ""
        cost = data.get("total_cost_usd")
    except Exception:  # noqa: BLE001
        result = (proc.stdout or "").strip()

    out_path = save_output(charter_dir, result)

    invariants, outcome = [], "ran"
    if args.eval:
        cases_path = os.path.join(charter_dir, "evals", "cases.yaml")
        if os.path.exists(cases_path):
            import yaml
            cases = (yaml.safe_load(open(cases_path)) or {}).get("cases", []) or []
            allpass = True
            for case in cases:
                for iname, ok, detail in eval_checks.check_all(case.get("invariants", []), result):
                    invariants.append({"case": case.get("id"), "invariant": iname, "pass": ok, "detail": detail})
                    allpass = allpass and ok
            outcome = "complete" if (allpass and cases) else ("failed" if cases else "ran")

    log_run(charter_dir, {"name": name, "timestamp": now(), "trigger": args.trigger,
                          "model": charter["model"]["id"], "duration_s": elapsed,
                          "outcome": outcome, "cost_usd": cost, "invariants": invariants,
                          "output_chars": len(result), "output_file": os.path.basename(out_path)})

    print(f"\n--- output ({len(result)} chars, saved to logs/) ---")
    print(result[:800] + ("…" if len(result) > 800 else ""))
    print(f"\ncost_usd: {cost}   duration: {elapsed}s")
    if args.eval and invariants:
        for r in invariants:
            print(f"  {'PASS' if r['pass'] else 'FAIL'}  {r['case']}:{r['invariant']}"
                  + (f"  ← {r['detail']}" if r["detail"] else ""))
        print(f"\nTASK {'COMPLETE ✅' if outcome == 'complete' else 'FAILED ❌'}  (logged to logs/runs.jsonl)")
    else:
        print("\n(ran; no eval gate — add evals + pass --eval to gate on invariants)")
    if proc.returncode != 0:
        print(f"[claude exit {proc.returncode}] {(proc.stderr or '')[:300]}")
    if outcome == "failed":
        sys.exit(1)   # eval gate failed → non-zero so cron/callers can tell


if __name__ == "__main__":
    main()
