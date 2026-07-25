#!/usr/bin/env python3
"""sdk-docs-researcher — a CHARTER-governed agent on the Claude Agent SDK. GENERATED, not hand-written.

Run: ./example.agent.py manual   (or: python3 example.agent.py --dry-run)
Deps: pip install claude-agent-sdk ; also needs the `claude` CLI (Node) on PATH + auth.
Targets claude-agent-sdk >=0.2.122 (docs at code.claude.com/docs/en/agent-sdk/{python,permissions,
hooks,user-input}, verified live 2026-07-18 — the version pinned in requirements.txt).

Real walls this runner enforces (see enforcement_report() / --dry-run for the honest per-field list):
  model, tools (disallowed_tools), budget.steps, budget.wall_clock, credentials.env — all real.
  egress — via a PreToolUse hook, NOT the can_use_tool permission callback. The docs' own permission
  evaluation order runs hooks BEFORE permission_mode, and unattended agents need permission_mode=
  bypassPermissions (can't prompt a human) — but bypassPermissions auto-approves at the permission-
  mode step, which is BEFORE can_use_tool is ever consulted. A can_use_tool callback would silently
  never fire. A PreToolUse hook runs first and its deny holds even under bypassPermissions — the
  same mechanism builders/claude-headless/egress_guard.py uses for the same reason.
"""

# ---- stdlib only at import time; the SDK is imported LAZILY inside run() ----
import argparse
import asyncio
import fcntl
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))

# 1. THE CHARTER (embedded dict — the single source of truth; edit = regenerate + bump version)
CHARTER = {
    "charter": "0.2",
    "runtime": "claude-sdk",
    "id": "sdk-docs-researcher",
    "version": "1",
    "owner": {"team": "demo", "on_call": "@owner", "escalation": "owner@example.com"},
    "status": "enabled",
    "context": {
        "trusted_sources": ["prompts/system.md"],
    },
    "model": {"provider": "anthropic", "id": "claude-haiku-4-5"},
    "sandbox": {"isolation": "none", "persist_state": False},
    "budget": {"usd": 0.20, "steps": 8, "wall_clock_seconds": 90},
    "tools": ["WebFetch"],
    "credentials": [{"name": "api-auth", "ref": "env:ANTHROPIC_API_KEY", "scope": "anthropic api"}],
    "egress": ["docs.python.org"],
    "approval_tier": {"auto": ["WebFetch"], "human_approval": []},
    "data": {"class": "public", "retention_days": 7, "redact": []},
    "evals": {
        "suite": "evals/cases.yaml",
        "success_metric": {"name": "cites a docs.python.org URL", "slo": "100%"},
    },
    # operational ergonomics (declared, never a wall): live loop echo + a per-run tool-call trace
    "extensions": {"observability": {"stream": False, "trace": True}},
}

DANGEROUS = ["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "Task"]

# A --dry-run note, not a wall. Prompt caching on this runtime belongs to the `claude` CLI: the SDK
# shells out to it and the CLI builds the actual API request, so caching is automatic and there is
# nothing to enable or tune from here (ClaudeAgentOptions exposes no cache_control). The one thing
# this agent DOES control is prefix hygiene — see builders/langchain/CLAUDE.md for the full note.
CACHING_NOTE = (
    "caching: handled by the `claude` CLI (it builds the request) — automatic, not charter-"
    "controlled, nothing to enable or tune here. What this agent controls is hygiene: keep volatile "
    "values (dates, run ids, uuids) OUT of context.trusted_sources, or the cached prefix changes "
    "every run and the CLI's caching silently stops paying off."
)


# ============================================================================
# 2. ENFORCEMENT HELPERS — pure, testable, NO SDK import needed
# ============================================================================

# OS essentials + Claude's own config-dir prefix. Deliberately NOT "ANTHROPIC_" — that would
# blanket-keep any stray ANTHROPIC_* var; the one auth var we forward comes only from the
# charter's declared credential, below, so a second undeclared ANTHROPIC_* can't ride along.
_ESSENTIAL_VARS = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TMPDIR", "TZ", "LANG")
_ESSENTIAL_PREFIXES = ("LC_", "CLAUDE_")


def auth_mode(charter):
    """Which auth the charter declares: "api-key", "subscription", or "none", read off the
    declared auth credential's ref. Drives scoped_env's cross-shadowing guard and the
    --dry-run billing note."""
    for cred in charter.get("credentials") or []:
        ref = cred.get("ref", "")
        if ref == "env:ANTHROPIC_API_KEY":
            return "api-key"
        if ref == "env:CLAUDE_CODE_OAUTH_TOKEN":
            return "subscription"
    return "none"


def _expand_tilde(path, src):
    """Expand a leading '~' using src['HOME'] — NOT os.path.expanduser, which reads the real
    process environment and would ignore a caller-supplied `src` dict in tests."""
    if not path or not path.startswith("~"):
        return path
    home = src.get("HOME")
    if not home:
        return path
    if path == "~":
        return home
    if path.startswith("~/"):
        return home + path[1:]
    return path  # '~otheruser/...' form: no per-user home table here, leave as-is


def scoped_env(charter, src=None):
    """The SDK subprocess environment: OS essentials + CLAUDE_*/LC_* + the charter's declared
    `env:` credential(s) — and nothing else. Every other host secret is dropped.

    Auth-mode-aware (the "no surprise bill" guard): api-key and subscription auth read from
    DIFFERENT env vars, and a stray ANTHROPIC_API_KEY silently outranks CLAUDE_CODE_OAUTH_TOKEN —
    so whichever mode the charter's credential declares, the OTHER mode's var is dropped even if
    it leaked in via CLAUDE_* (CLAUDE_CODE_OAUTH_TOKEN) or was set ambiently.

    `src` defaults to os.environ; pass a plain dict in tests. Pure — does NOT touch os.environ.
    Also does not expand '~' via os.path.expanduser for the same testability reason; see
    _expand_tilde above.
    """
    src = os.environ if src is None else src
    env = {k: src[k] for k in _ESSENTIAL_VARS if k in src}
    for k in src:
        if k.startswith(_ESSENTIAL_PREFIXES):
            env[k] = src[k]

    cfg = env.get("CLAUDE_CONFIG_DIR")
    if cfg and cfg.startswith("~"):
        env["CLAUDE_CONFIG_DIR"] = _expand_tilde(cfg, src)

    for cred in charter.get("credentials") or []:
        ref = cred.get("ref", "")
        if ref.startswith("env:"):
            var = ref[4:]
            if var in src:
                env[var] = src[var]

    mode = auth_mode(charter)
    if mode == "api-key":
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
    elif mode == "subscription":
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


def egress_decision(tool_name, tool_input, allowlist):
    """(allow: bool, reason: str) — the pure egress decision, wrapped by a PreToolUse hook at
    run time. Mirrors builders/claude-headless/egress_guard.py's exact host-match rule."""
    allowlist = allowlist or []
    if "any" in allowlist:
        return True, f"egress is open ([any]); '{tool_name}' allowed"
    if tool_name == "WebSearch":
        return False, (
            "WebSearch has no host to check against a scoped egress list; a search "
            "can't be confined to hosts — use egress:[any] to permit it or drop WebSearch"
        )
    if tool_name == "WebFetch":
        url = (tool_input or {}).get("url")
        if not url:
            return False, f"WebFetch call has no url to check against egress {allowlist}"
        host = urlparse(url).hostname or ""
        if _host_allowed(host, allowlist):
            return True, f"egress to '{host}' allowed by allow-list {allowlist}"
        return False, f"egress to '{host}' not in allow-list {allowlist}"
    return True, f"'{tool_name}' is not a network tool; this hook does not gate it"


def _host_allowed(host, egress):
    for pattern in egress:
        bare = pattern[2:] if pattern.startswith("*.") else pattern
        if not bare:
            continue
        if host == pattern or host == bare or host.endswith("." + bare):
            return True
    return False


def redact(text, patterns, secret_values):
    """Scrub declared regex patterns AND literal secret values from output/console/log before
    write. Mirrors run_headless.py's _redact. Only as complete as the patterns you declare."""
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


def observability(charter):
    """{'stream': bool, 'trace': bool} from extensions.observability — operational ergonomics,
    NOT walls (extensions is declared-only). Default both off: a quiet agent is the safe default
    for cron/logs; a human running by hand can opt in with --stream/--trace."""
    obs = ((charter.get("extensions") or {}).get("observability")) or {}
    return {"stream": bool(obs.get("stream", False)), "trace": bool(obs.get("trace", False))}


def trace_line(tool_name, tool_input, patterns, secret_values):
    """One compact, redacted JSON line describing a tool call, for the per-run trace log."""
    raw = json.dumps({"tool": tool_name, "input": tool_input}, default=str, sort_keys=True)
    return redact(raw, patterns, secret_values)


# ---- inline eval_checks (copied verbatim from core/eval_checks.py — a self-contained agent
# can't import core/) -------------------------------------------------------------------------
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
    bad = [
        ln for ln in out.splitlines() if any(k in ln.lower() for k in kws) and not _URL.search(ln)
    ]
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
    ((name, value),) = invariant.items()
    fn = _CHECKS.get(name)
    if fn is None:
        return name, False, f"unknown invariant type {name!r}"
    ok, detail = fn(output, value)
    return name, ok, detail


def check_all(invariants, output):
    """Returns [(name, ok, detail), ...] for each invariant in the list."""
    return [check_one(inv, output) for inv in (invariants or [])]


# ---- one run at a time (mirrors run_headless.py / the langchain agent) -----------------------
# Held for the life of the process on purpose: closing the fd releases the lock, so this must
# outlive the function that took it.
_RUN_LOCK_FD = None


def acquire_run_lock():
    """True if this process now owns the agent's run lock, False if another run holds it.

    An advisory flock on a file in the agent dir. The kernel drops it when this process dies —
    including SIGKILL and a hard timeout — so an interrupted run can never strand the lock the
    way a pidfile or a mkdir lock does. Matters once a schedule fires faster than a run finishes:
    without it, two runs share one budget, one log, and one output directory.
    """
    global _RUN_LOCK_FD
    _RUN_LOCK_FD = open(os.path.join(HERE, ".run.lock"), "w")
    try:
        fcntl.flock(_RUN_LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:  # already held — BlockingIOError on Linux, EAGAIN/EWOULDBLOCK on macOS
        _RUN_LOCK_FD.close()
        _RUN_LOCK_FD = None
        return False
    return True


# ---- run-log helpers (mirror run_headless.py) -------------------------------------------------
def _logdir():
    d = os.path.join(HERE, "logs")
    os.makedirs(d, exist_ok=True)
    return d


def _log_run(entry):
    with open(os.path.join(_logdir(), "runs.jsonl"), "a") as f:
        f.write(json.dumps(entry) + "\n")


def _unique_path(d, ts, suffix):
    """A collision-safe path — two runs in the same second must not clobber each other."""
    p = os.path.join(d, f"{ts}.{suffix}")
    n = 1
    while os.path.exists(p):
        p = os.path.join(d, f"{ts}.{n}.{suffix}")
        n += 1
    return p


def _save_output(text, ts):
    p = _unique_path(_logdir(), ts, "output.txt")
    with open(p, "w") as f:
        f.write(text)
    return p


def _prune_logs(retention_days):
    """Delete output/trace files and runs.jsonl entries older than the retention window.
    Housekeeping runs when the agent runs — this is not a daemon."""
    if not retention_days:
        return
    d = _logdir()
    cutoff = time.time() - retention_days * 86400
    for fname in os.listdir(d):
        if fname.endswith(".output.txt") or fname.endswith(".trace.jsonl"):
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
                except Exception:  # noqa: BLE001,S110 - a bad line must not lose the rest
                    pass  # keep unparseable lines rather than lose data
                kept.append(line)
        with open(runs, "w") as f:
            if kept:
                f.write("\n".join(kept) + "\n")


def _read_trusted_sources(charter, base_dir):
    """Concatenate context.trusted_sources into the system prompt. Fails closed (like
    run_headless.py's _system_prompt_file) if a source escapes the agent dir or isn't on disk —
    a trusted source that isn't there should refuse, not silently run on a weaker prompt."""
    srcs = (charter.get("context") or {}).get("trusted_sources", []) or []
    base = os.path.realpath(base_dir)
    text = ""
    for s in srcs:
        p = os.path.realpath(os.path.join(base_dir, s))
        if p != base and not p.startswith(base + os.sep):
            raise ValueError(
                f"context.trusted_sources: '{s}' escapes the agent directory {base_dir}"
            )
        if not os.path.exists(p):
            raise ValueError(f"context.trusted_sources: '{s}' does not exist at {p}")
        with open(p) as f:
            text += f.read() + "\n\n"
    return text.strip() or None


# ============================================================================
# --dry-run report — does NOT import the SDK
# ============================================================================
def enforcement_report(charter):
    """The honest --dry-run report: what this runner actually enforces, field by field.
    Mirrors run_headless.py's block/declared/none rows, adapted to the SDK's own mechanisms."""
    tools = charter.get("tools") or []
    budget = charter.get("budget") or {}
    egress = charter.get("egress") or []
    data = charter.get("data") or {}
    mode = auth_mode(charter)
    creds = charter.get("credentials") or []
    cred_var = next((c["ref"][4:] for c in creds if c.get("ref", "").startswith("env:")), "?")
    dropped_by_mode = {
        "api-key": "CLAUDE_CODE_OAUTH_TOKEN (and ANTHROPIC_AUTH_TOKEN)",
        "subscription": "ANTHROPIC_API_KEY (and ANTHROPIC_AUTH_TOKEN)",
        "none": "both ANTHROPIC_API_KEY and CLAUDE_CODE_OAUTH_TOKEN — no auth credential declared",
    }[mode]

    rows = [("block", "status", "run refused unless status is 'enabled'")]
    rows.append(("block", "model", f"ClaudeAgentOptions.model={charter['model']['id']!r} pins it"))
    denied = [t for t in DANGEROUS if t not in tools]
    rows.append(
        (
            "block",
            "tools",
            f"disallowed_tools={denied} removes those tool definitions outright (checked even "
            f"under bypassPermissions); allowed_tools={tools} is declarative only here — "
            f"bypassPermissions auto-approves anything NOT denied, so allowed_tools does not "
            f"itself narrow further",
        )
    )
    if budget.get("steps"):
        rows.append(("block", "budget.steps", f"max_turns={budget['steps']} hard-stops the run"))
    else:
        rows.append(("—", "budget.steps", "not set"))
    if budget.get("wall_clock_seconds"):
        rows.append(
            (
                "block",
                "budget.wall_clock",
                f"asyncio.wait_for(...,timeout={budget['wall_clock_seconds']}) hard-kills the run "
                f"and closes the SDK's generator",
            )
        )
    else:
        rows.append(("—", "budget.wall_clock", "not set"))
    if budget.get("usd"):
        rows.append(
            (
                "declared",
                "budget.usd",
                f"max_budget_usd={budget['usd']} — a CLIENT-SIDE cost ESTIMATE the SDK tracks, "
                f"not a metered hard wall; verify",
            )
        )
    else:
        rows.append(("—", "budget.usd", "not set"))
    # The PreToolUse hook is registered unconditionally in run(), so it gates WebFetch/WebSearch
    # to the egress policy whether or not the charter granted those tools — a scoped or [none]
    # egress is a real backstop even for a text-only agent (defense in depth).
    if "any" in egress:
        rows.append(
            (
                "none",
                "egress",
                "egress:[any] — the hook allows all hosts, and under bypassPermissions "
                "WebFetch/WebSearch aren't walled out, so network is effectively open (not a wall)",
            )
        )
    else:
        granted = {"WebFetch", "WebSearch"} & set(tools)
        scope = (
            (
                "gates WebFetch to the allow-list and denies WebSearch outright (a search can't be "
                "confined to hosts)"
            )
            if granted
            else (
                "denies any WebFetch/WebSearch attempt — none is granted, so this is a backstop, not "
                "the primary wall"
            )
        )
        rows.append(
            (
                "block",
                "egress",
                f"a PreToolUse hook (HookMatcher on WebFetch|WebSearch) {scope}; egress={egress}. "
                "NOT the can_use_tool callback — that is shadowed by permission_mode="
                "bypassPermissions (bypass approves BEFORE can_use_tool runs); a PreToolUse hook "
                "runs first and its deny holds regardless of mode",
            )
        )
    rows.append(
        (
            "block",
            "credentials.env",
            f"scoped subprocess env (replaces os.environ for this process): auth mode={mode}; "
            f"keeps {cred_var} + OS essentials + CLAUDE_*/LC_*; drops {dropped_by_mode} plus "
            f"every other host secret",
        )
    )
    rows.append(
        (
            "none",
            "sandbox",
            "a real fs/net jail needs a container; the `claude` CLI subprocess the SDK spawns "
            "is not filesystem-isolated",
        )
    )
    escapes = [t for t in tools if t == "Bash"]
    if escapes:
        rows.append(
            (
                "none",
                "egress.other",
                f"{', '.join(escapes)} reaches the network OUTSIDE this hook — a container is "
                f"required to fence it",
            )
        )
    rows.append(
        (
            "declared",
            "context",
            "trusted_sources concatenated into system_prompt; fetched pages/memory are NOT "
            "auto-tagged untrusted by this runner",
        )
    )
    if charter.get("approval_tier", {}).get("human_approval"):
        rows.append(
            (
                "none",
                "approval_tier",
                "unattended (permission_mode=bypassPermissions) has no approval queue",
            )
        )
    else:
        rows.append(("declared", "approval_tier", "no human-approval actions declared"))
    rows.append(("declared", "data.class", f"{data.get('class')} — advisory"))
    n_pat = len(data.get("redact") or [])
    if n_pat or creds:
        what = []
        if n_pat:
            what.append(f"{n_pat} declared pattern(s)")
        if creds:
            what.append("env: credential values")
        rows.append(
            (
                "block",
                "data.redact",
                f"{' + '.join(what)} scrubbed from saved output, console, and eval detail before "
                "write (only as complete as your patterns)",
            )
        )
    else:
        rows.append(
            ("declared", "data.redact", "nothing to redact — no patterns, no env: credentials")
        )
    if data.get("retention_days"):
        rows.append(
            (
                "block",
                "data.retention",
                f"output files & runs.jsonl entries older than {data['retention_days']}d pruned "
                f"when the agent runs (housekeeping, not a daemon)",
            )
        )
    else:
        rows.append(("declared", "data.retention", "no retention window; logs kept indefinitely"))
    evals = charter.get("evals") or {}
    rows.append(
        (
            "declared",
            "evals",
            f"suite={evals.get('suite')!r} — gated inline in run() if that file exists on disk",
        )
    )
    obs = observability(charter)
    rows.append(
        (
            "declared",
            "observability",
            f"stream={obs['stream']} trace={obs['trace']} — live agent-loop echo to the console "
            "and/or a per-run tool-call trace file (redacted); operational only, not a wall "
            "(extensions.observability; override at run time with --stream/--quiet/--trace/--no-trace)",
        )
    )

    lines = [f"=== claude-sdk enforcement: {charter['id']} v{charter['version']} ==="]
    lines.append(f"auth mode: {mode}")
    for status, field, note in rows:
        lines.append(f"  {status:<10} {field:<20} {note}")
    lines.append("")
    if mode == "api-key":
        lines.append(
            "billing/ToS: api-key mode — bills your Anthropic API account per token; "
            "the sanctioned, shareable mode for an unattended agent."
        )
    elif mode == "subscription":
        lines.append(
            "billing/ToS: subscription mode — rides your Claude Code seat via "
            "CLAUDE_CODE_OAUTH_TOKEN; check your plan's terms before running this "
            "unattended or on shared infrastructure."
        )
    else:
        lines.append(
            "billing/ToS: no api-key/subscription credential declared — scoped_env drops "
            "ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, and ANTHROPIC_AUTH_TOKEN; a real "
            "run will fail to authenticate."
        )
    lines.append(CACHING_NOTE)
    lines.append("\n[dry-run] not executed.")
    return "\n".join(lines)


# ============================================================================
# 3. run() — imports the SDK lazily, builds options FROM the charter,
#    runs under a wall-clock, gates evals, logs
# ============================================================================
async def run(trigger, prompt, stream=None, trace=None):
    charter = CHARTER
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731

    # observability: charter default (extensions.observability), overridable per run by the CLI
    obs = observability(charter)
    stream = obs["stream"] if stream is None else stream
    trace = obs["trace"] if trace is None else trace

    if charter.get("status") != "enabled":
        print(f"REFUSED: status={charter.get('status')} (not enabled)")
        sys.exit(3)

    if prompt is None:
        tp = os.path.join(HERE, "prompts", "task.md")
        if os.path.exists(tp):
            with open(tp) as f:
                prompt = f.read().strip()
    if not prompt:
        print("REFUSED: no task prompt (pass --prompt, or add prompts/task.md)")
        sys.exit(4)

    # After the cheap config gates, before anything is spent: one run at a time. Logged, because
    # a scheduler overlapping itself is exactly the thing you need the log to be able to show.
    if not acquire_run_lock():
        print("REFUSED: another run of this agent is already in progress")
        _log_run(
            {
                "id": charter["id"],
                "timestamp": now(),
                "trigger": trigger,
                "outcome": "refused",
                "reason": "another run in progress",
            }
        )
        sys.exit(8)

    # Apply the scoped env by REPLACING the process env — the `claude` CLI subprocess the SDK
    # spawns inherits this process's environment, so this .py must run as its own process (it
    # cannot be imported into a session whose own env needs to survive).
    env = scoped_env(charter)
    os.environ.clear()
    os.environ.update(env)

    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            HookMatcher,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
            query,
        )
    except ImportError:
        print(
            "REFUSED: claude-agent-sdk is not installed.\n"
            "  pip install -r builders/claude-sdk/requirements.txt   (or: pip install claude-agent-sdk)\n"
            "  Also needs the `claude` CLI (Node) on PATH, authenticated."
        )
        sys.exit(6)

    try:
        system_prompt = _read_trusted_sources(charter, HERE)
    except ValueError as e:
        print(f"REFUSED: {e}")
        _log_run(
            {
                "id": charter["id"],
                "timestamp": now(),
                "trigger": trigger,
                "outcome": "refused",
                "reason": str(e),
            }
        )
        sys.exit(2)

    tools = list(charter.get("tools") or [])
    disallowed = [t for t in DANGEROUS if t not in tools]
    budget = charter.get("budget") or {}
    egress = charter.get("egress") or []

    async def _egress_hook(input_data, tool_use_id, context):
        """PreToolUse hook — runs before permission_mode, so its deny holds even under
        bypassPermissions. See the module docstring for why this replaces can_use_tool."""
        if input_data.get("hook_event_name") != "PreToolUse":
            return {}
        allow, reason = egress_decision(
            input_data.get("tool_name", ""), input_data.get("tool_input") or {}, egress
        )
        if allow:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    options = ClaudeAgentOptions(
        model=charter["model"]["id"],
        system_prompt=system_prompt,
        allowed_tools=tools,
        disallowed_tools=disallowed,
        permission_mode="bypassPermissions",  # unattended: nobody is there to answer a prompt
        max_turns=budget.get("steps"),
        max_budget_usd=budget.get("usd"),
        hooks={"PreToolUse": [HookMatcher(matcher="WebFetch|WebSearch", hooks=[_egress_hook])]},
    )

    # secrets are needed up-front so streamed/traced tool calls are redacted live, not just at save
    data = charter.get("data") or {}
    patterns = data.get("redact") or []
    secret_values = [
        os.environ[cred["ref"][4:]]
        for cred in (charter.get("credentials") or [])
        if cred.get("ref", "").startswith("env:") and cred["ref"][4:] in os.environ
    ]

    run_ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    trace_fp = open(_unique_path(_logdir(), run_ts, "trace.jsonl"), "w") if trace else None
    trace_path = trace_fp.name if trace_fp else None

    def _on_event(kind, payload):
        """Optional observability — live console echo (stream) and/or per-run trace file. Both
        redacted; neither is a wall (they only observe what the enforced loop already did)."""
        if kind == "text" and stream:
            sys.stdout.write(redact(payload, patterns, secret_values))
            sys.stdout.flush()
        elif kind == "tool":
            name, inp = payload
            line = trace_line(name, inp, patterns, secret_values)
            if stream:
                print(f"\n  → {name} {line}")
            if trace_fp:
                trace_fp.write(line + "\n")
                trace_fp.flush()

    async def _collect(gen):
        """Iterate the SDK's async generator; accumulate assistant text + capture the final
        ResultMessage (result text, subtype, total_cost_usd); emit observability events per turn."""
        text_parts, result_msg, tool_calls = [], None, 0
        async for message in gen:
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
                        _on_event("text", block.text)
                    elif isinstance(block, ToolUseBlock):
                        tool_calls += 1
                        _on_event("tool", (block.name, block.input))
            elif isinstance(message, ResultMessage):
                result_msg = message
        return "".join(text_parts), result_msg, tool_calls

    wall_clock = budget.get("wall_clock_seconds") or 90
    gen = query(prompt=prompt, options=options)
    if stream:
        print(f"--- streaming {charter['id']} (trigger={trigger}) ---")
    t0 = time.time()
    killed = False
    tool_calls = 0
    try:
        assistant_text, result_msg, tool_calls = await asyncio.wait_for(
            _collect(gen), timeout=wall_clock
        )
    except asyncio.TimeoutError:
        killed = True
        assistant_text, result_msg = "", None
        try:
            await gen.aclose()  # cancel cleanly rather than leak the underlying subprocess
        except Exception:  # noqa: BLE001,S110 - best-effort cleanup on a dying run
            pass
    finally:
        if trace_fp:
            trace_fp.close()
    elapsed = round(time.time() - t0, 1)

    cost = result_msg.total_cost_usd if result_msg else None
    result_text = (result_msg.result if result_msg and result_msg.result else assistant_text) or ""
    if killed:
        outcome = "killed"
        print(f"KILLED: wall-clock timeout {wall_clock}s hit")
    elif result_msg is None:
        outcome = "failed"
    elif result_msg.subtype == "error_during_execution":
        outcome = "failed"
    else:
        outcome = "complete"

    invariants = []
    evals_cfg = charter.get("evals") or {}
    suite_rel = evals_cfg.get("suite")
    if outcome == "complete" and suite_rel:
        suite_path = os.path.join(HERE, suite_rel)
        if os.path.exists(suite_path):
            import yaml

            cases = (yaml.safe_load(open(suite_path)) or {}).get("cases", []) or []
            allpass = True
            for case in cases:
                for iname, ok, detail in check_all(case.get("invariants", []), result_text):
                    invariants.append(
                        {"case": case.get("id"), "invariant": iname, "pass": ok, "detail": detail}
                    )
                    allpass = allpass and ok
            if cases:
                outcome = "complete" if allpass else "failed"

    # redact declared patterns + declared secret values from everything persisted; evals above
    # ran on the raw result so redaction can't mask (or break) a passing invariant
    safe_result = redact(result_text, patterns, secret_values)
    for inv in invariants:
        if inv.get("detail"):
            inv["detail"] = redact(inv["detail"], patterns, secret_values)

    out_path = _save_output(safe_result, run_ts)
    _log_run(
        {
            "id": charter["id"],
            "timestamp": now(),
            "trigger": trigger,
            "model": charter["model"]["id"],
            "duration_s": elapsed,
            "outcome": outcome,
            "cost_usd": cost,
            "tool_calls": tool_calls,
            "invariants": invariants,
            "output_chars": len(safe_result),
            "output_file": os.path.basename(out_path),
            "trace_file": os.path.basename(trace_path) if trace_path else None,
        }
    )
    _prune_logs(data.get("retention_days"))

    if not stream:  # when streaming, the output was already echoed live
        print(f"\n--- output ({len(safe_result)} chars, saved to logs/) ---")
        print(safe_result[:800] + ("…" if len(safe_result) > 800 else ""))
    print(
        f"\ncost_usd: {cost}   duration: {elapsed}s   outcome: {outcome}   tool_calls: {tool_calls}"
    )
    if invariants:
        for r in invariants:
            print(
                f"  {'PASS' if r['pass'] else 'FAIL'}  {r['case']}:{r['invariant']}"
                + (f"  <- {r['detail']}" if r["detail"] else "")
            )
    if outcome == "killed":
        # Wall-clock or step cap: nothing completed. Exiting 0 here would report success to a
        # scheduler for the one failure it most needs to hear about. Matches run_headless.py.
        sys.exit(5)
    if outcome == "failed":
        sys.exit(1)


# ============================================================================
# 4. main()
# ============================================================================
def main():
    ap = argparse.ArgumentParser(
        description="sdk-docs-researcher — a CHARTER-governed Claude Agent SDK agent"
    )
    ap.add_argument("trigger", nargs="?", default="manual")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the enforcement report; do not run, no tokens spent",
    )
    ap.add_argument("--prompt", default=None, help="task prompt; default is prompts/task.md")
    ap.add_argument(
        "--stream",
        dest="stream",
        action="store_true",
        default=None,
        help="echo the agent loop live (overrides extensions.observability.stream)",
    )
    ap.add_argument(
        "--quiet", dest="stream", action="store_false", help="do not echo the loop live"
    )
    ap.add_argument(
        "--trace",
        dest="trace",
        action="store_true",
        default=None,
        help="write a per-run tool-call trace file (overrides extensions.observability.trace)",
    )
    ap.add_argument(
        "--no-trace", dest="trace", action="store_false", help="do not write a trace file"
    )
    args = ap.parse_args()

    if args.dry_run:
        print(enforcement_report(CHARTER))
        return

    asyncio.run(run(args.trigger, args.prompt, stream=args.stream, trace=args.trace))


if __name__ == "__main__":
    main()
