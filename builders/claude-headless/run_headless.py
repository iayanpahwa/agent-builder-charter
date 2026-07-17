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
  egress (specific list)     -> --settings PreToolUse hook (egress_guard); process-scoped, clean
  context.trusted_sources    -> --append-system-prompt-file (concatenated)
  status                     -> refuses to run unless 'enabled'
  data.redact/retention      -> declared patterns + env: cred values scrubbed from logs; old logs pruned past retention_days.
Honest limits (flags can't; a container can):
  budget.usd  -> --max-budget-usd, but MAY be a no-op under subscription auth (verify).
  credentials -> host env scoped to Claude auth + declared env: refs; all other secrets dropped; fs NOT isolated (needs container).
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
import re
import shlex
import signal
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


def _declared_secret_values(charter):
    """Values of the charter's declared env: credentials that are set in the host
    environment — so an echoed secret never lands in a log."""
    vals = []
    for cred in charter.get("credentials") or []:
        ref = cred.get("ref", "")
        if ref.startswith("env:"):
            var = ref[4:]
            if var in os.environ:
                vals.append(os.environ[var])
    return vals


def _redact(text, patterns, secret_values):
    """Scrub declared patterns and declared secret values from text before it is
    persisted. Each pattern is a regex (falls back to a literal if it will not
    compile). Redaction is only as complete as the patterns you declare."""
    if not text:
        return text
    for pat in patterns or []:
        try:
            text = re.compile(pat).sub("[REDACTED]", text)
        except re.error:
            text = text.replace(pat, "[REDACTED]")
    for val in secret_values or []:
        if val and len(val) >= 4:
            text = text.replace(val, "[REDACTED]")
    return text


def prune_logs(charter_dir, retention_days):
    """Delete output files and runs.jsonl entries older than the retention window.
    Housekeeping runs when the agent runs — this is not a daemon."""
    if not retention_days:
        return
    d = _logdir(charter_dir)
    cutoff = time.time() - retention_days * 86400
    for fname in os.listdir(d):
        if fname.endswith(".output.txt"):
            p = os.path.join(d, fname)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
            except OSError:
                pass
    runs = os.path.join(d, "runs.jsonl")
    if os.path.exists(runs):
        kept = []
        with open(runs) as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = entry.get("timestamp")
                    if ts and datetime.fromisoformat(ts).timestamp() < cutoff:
                        continue  # too old — drop
                except Exception:  # noqa: BLE001
                    pass  # keep unparseable lines rather than lose data
                kept.append(line)
        with open(runs, "w") as f:
            if kept:
                f.write("\n".join(kept) + "\n")


# --- building the enforced command ---------------------------------------
def _system_prompt_file(charter, charter_dir):
    srcs = (charter.get("context") or {}).get("trusted_sources", []) or []
    base = os.path.realpath(charter_dir)
    text = ""
    for s in srcs:
        p = os.path.realpath(os.path.join(charter_dir, s))
        if p != base and not p.startswith(base + os.sep):
            raise CharterInvalid(
                f"context.trusted_sources: '{s}' escapes the charter directory; "
                f"sources must be files inside {charter_dir}"
            )
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


def _settings_file(charter, charter_path):
    """One --settings file merging the egress hook and the skills allow-list."""
    settings = {}
    egress = charter.get("egress", []) or []
    tools = charter.get("tools", []) or []
    if (NET_TOOLS & set(tools)) and egress and "any" not in egress:
        guard = os.path.join(HERE, "egress_guard.py")
        settings["hooks"] = {"PreToolUse": [
            {"matcher": "WebFetch|WebSearch", "hooks": [
                {"type": "command", "command": f"python3 {guard} --charter {charter_path}"}]}]}
    if charter.get("skills"):
        settings["availableSkills"] = charter["skills"]   # restrict to ONLY these
    if not settings:
        return None
    fd, path = tempfile.mkstemp(suffix=".settings.json")
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f)
    return path


def build_command(charter, charter_dir, charter_path, prompt):
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
    settings_file = _settings_file(charter, charter_path)
    if settings_file:
        cmd += ["--settings", settings_file]
    if budget.get("usd"):
        cmd += ["--max-budget-usd", str(budget["usd"])]
    return cmd, [f for f in (sys_file, settings_file, mcp_file) if f]


def report(charter):
    """Every enforcement-relevant field gets one honest row — nothing hidden."""
    tools = charter.get("tools", []) or []
    egress = charter.get("egress", []) or []
    budget = charter.get("budget") or {}
    net = bool(NET_TOOLS & set(tools))
    rows = [
        ("block", "status", "run refused unless status is 'enabled'"),
        ("declared", "context", "trusted_sources concatenated into the system prompt; fetched pages & memory are NOT auto-tagged or cleaned in headless"),
        ("block", "model", "--model pins it"),
        ("none", "sandbox", "a real fs/net jail needs a container; flags can't"),
    ]
    rows.append(("block", "budget.steps", "--max-turns hard-stops the run")
                if budget.get("steps") else ("—", "budget.steps", "not set"))
    rows.append(("block", "budget.wall_clock", "subprocess timeout hard-kills the run")
                if budget.get("wall_clock_seconds") else ("—", "budget.wall_clock", "not set (default 120s)"))
    if budget.get("tokens"):
        rows.append(("none", "budget.tokens", "no token-ceiling flag in headless; bounded by steps/wall-clock/usd"))
    if budget.get("usd"):
        rows.append(("declared", "budget.usd", f"--max-budget-usd {budget['usd']} — MAY be a no-op under subscription auth; verify"))
    rows.append(("block", "tools", "--allowedTools + --disallowedTools (dangerous tools denied)"))
    rows.append(("block", "credentials.env", "host env scoped to Claude's auth (CLAUDE_*/ANTHROPIC_*) + OS essentials + declared env: refs; all other host secrets dropped"))
    rows.append(("none", "credentials.fs", "filesystem NOT isolated — file-stored secrets (dotfiles, ~/.aws, the config dir) stay readable; a container is required"))
    if egress == ["none"]:
        rows.append(("block", "egress", "WebFetch/WebSearch denied (no network via web tools)"))
    elif net and egress and "any" not in egress:
        rows.append(("block", "egress", "WebFetch gated to the allow-list; WebSearch denied (a search can't be confined to hosts)"))
    elif "any" in egress:
        rows.append(("none", "egress", "open ([any]) — nothing to gate"))
    else:
        rows.append(("none", "egress", "no WebFetch/WebSearch tool — nothing to gate"))
    _escapes = [t for t in tools if t == "Bash"]
    if charter.get("mcp"):
        _escapes.append("MCP servers")
    if _escapes:
        rows.append(("none", "egress.other", f"{', '.join(_escapes)} reach the network OUTSIDE egress — the hook gates only WebFetch/WebSearch; a container is required to fence these"))
    if charter.get("mcp"):
        names = ", ".join(s.get("name", "?") for s in charter["mcp"])
        rows.append(("block", "mcp", f"only [{names}] loaded (--strict-mcp-config); each runs its OWN code/secrets — you're trusting it"))
    if charter.get("skills"):
        rows.append(("block", "skills", f"restricted to {charter['skills']} (settings.availableSkills; version-dependent)"))
    if charter.get("approval_tier", {}).get("human_approval"):
        rows.append(("none", "approval_tier", "headless is unattended (bypassPermissions); destructive actions are contained by omission from tools, not an approval queue"))
    else:
        rows.append(("declared", "approval_tier", "no human-approval actions declared"))
    rows.append(("declared", "data.class", "recorded; sensitivity is advisory in headless"))
    _data = charter.get("data") or {}
    _has_env_creds = any((c.get("ref", "") or "").startswith("env:") for c in (charter.get("credentials") or []))
    if _data.get("redact") or _has_env_creds:
        _what = "declared patterns + env: credential values" if _data.get("redact") else "declared env: credential values"
        rows.append(("block", "data.redact", f"{_what} scrubbed from saved output, console, and eval detail before write (only as complete as your patterns)"))
    else:
        rows.append(("declared", "data.redact", "nothing to redact — no patterns and no env: credentials declared"))
    if _data.get("retention_days"):
        rows.append(("block", "data.retention", f"output files & runs.jsonl entries older than {_data['retention_days']}d pruned when the agent runs (housekeeping, not a daemon)"))
    else:
        rows.append(("declared", "data.retention", "no retention window; logs kept indefinitely"))
    rows.append(("declared", "evals", "run with --eval to gate on invariants; the suite/SLO are checked outside this runner"))
    if charter.get("extensions"):
        rows.append(("declared", "extensions", "ignored by the loader; declared only"))
    return rows


def _shellish(s):
    return shlex.quote(s) if (not s or any(c in s for c in ' "\'\n\t')) else s


def _cleanup(paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _kill_process_group(proc, grace=3):
    """Kill the whole process group, not just `claude` — subprocess reaps only the
    direct child on timeout, so MCP/Bash grandchildren would orphan and keep running."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return                       # already gone
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.communicate(timeout=grace)   # let it exit gracefully; drains the pipes
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()                # reap


_ESSENTIAL_VARS = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TMPDIR", "TZ", "LANG")
_ESSENTIAL_PREFIXES = ("LC_", "ANTHROPIC_", "CLAUDE_")  # locale + Claude Code's own auth/config


def scoped_env(charter):
    """The subprocess environment: Claude's own auth + OS essentials + the charter's
    declared `env:` credentials — and nothing else. Every other host secret is dropped.
    This scopes the ENVIRONMENT only; it is NOT filesystem isolation (a container is
    required for that)."""
    src = os.environ
    env = {k: src[k] for k in _ESSENTIAL_VARS if k in src}
    for k in src:
        if k.startswith(_ESSENTIAL_PREFIXES):
            env[k] = src[k]
    for cred in charter.get("credentials") or []:
        ref = cred.get("ref", "")
        if ref.startswith("env:"):
            var = ref[4:]
            if var in src:
                env[var] = src[var]
    return env


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

    try:
        cmd, tmpfiles = build_command(charter, charter_dir, charter_path, prompt)
    except CharterInvalid as e:
        print(f"REFUSED: {e}")
        log_run(charter_dir, {"name": name, "timestamp": now(), "trigger": args.trigger,
                              "outcome": "refused", "reason": str(e)})
        sys.exit(2)

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
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=scoped_env(charter),  # scoped: Claude's auth + declared env: creds only; fs isolation still needs a container
                                start_new_session=True)  # own process group so timeout can reap grandchildren
    except FileNotFoundError:
        print("REFUSED: `claude` CLI not found on PATH.")
        _cleanup(tmpfiles)
        sys.exit(6)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        elapsed = round(time.time() - t0, 1)
    except subprocess.TimeoutExpired:
        elapsed = round(time.time() - t0, 1)
        _kill_process_group(proc)  # kill claude AND anything it spawned, not just the direct child
        print(f"\nKILLED: wall-clock timeout {timeout}s hit")
        log_run(charter_dir, {"name": name, "timestamp": now(), "trigger": args.trigger,
                              "model": charter["model"]["id"], "outcome": "timeout", "duration_s": elapsed})
        _cleanup(tmpfiles)
        sys.exit(5)
    _cleanup(tmpfiles)

    result, cost = "", None
    try:
        data = json.loads(stdout)
        result = data.get("result", "") or ""
        cost = data.get("total_cost_usd")
    except Exception:  # noqa: BLE001
        result = (stdout or "").strip()

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

    # redact declared patterns + declared secret values from everything we persist;
    # evals above ran on the raw result so redaction can't break a passing invariant
    _data = charter.get("data") or {}
    patterns = _data.get("redact") or []
    secret_values = _declared_secret_values(charter)
    safe_result = _redact(result, patterns, secret_values)
    for inv in invariants:
        if inv.get("detail"):
            inv["detail"] = _redact(inv["detail"], patterns, secret_values)

    out_path = save_output(charter_dir, safe_result)

    log_run(charter_dir, {"name": name, "timestamp": now(), "trigger": args.trigger,
                          "model": charter["model"]["id"], "duration_s": elapsed,
                          "outcome": outcome, "cost_usd": cost, "invariants": invariants,
                          "output_chars": len(safe_result), "output_file": os.path.basename(out_path)})

    prune_logs(charter_dir, _data.get("retention_days"))

    print(f"\n--- output ({len(safe_result)} chars, saved to logs/) ---")
    print(safe_result[:800] + ("…" if len(safe_result) > 800 else ""))
    print(f"\ncost_usd: {cost}   duration: {elapsed}s")
    if args.eval and invariants:
        for r in invariants:
            print(f"  {'PASS' if r['pass'] else 'FAIL'}  {r['case']}:{r['invariant']}"
                  + (f"  ← {r['detail']}" if r["detail"] else ""))
        print(f"\nTASK {'COMPLETE ✅' if outcome == 'complete' else 'FAILED ❌'}  (logged to logs/runs.jsonl)")
    else:
        print("\n(ran; no eval gate — add evals + pass --eval to gate on invariants)")
    if proc.returncode != 0:
        print(f"[claude exit {proc.returncode}] {(stderr or '')[:300]}")
    if outcome == "failed":
        sys.exit(1)   # eval gate failed → non-zero so cron/callers can tell


if __name__ == "__main__":
    main()
