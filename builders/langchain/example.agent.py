#!/usr/bin/env python3
"""langchain-docs-researcher — a CHARTER-governed agent on LangChain / LangGraph. GENERATED, not hand-written.

Run: ./example.agent.py manual   (or: python3 example.agent.py --dry-run)
Deps: pip install -r requirements.txt  (langgraph + langchain + langchain-anthropic).
Targets the LangGraph minimal harness (langgraph.prebuilt.create_react_agent) + langchain.chat_models.
init_chat_model, verified live against docs.langchain.com / reference.langchain.com on 2026-07-19.
The LangChain/LangGraph API drifts (create_react_agent vs LangChain 1.0 create_agent, recursion_limit
semantics) — the generator re-pins against live docs and adapts.

Real walls this runner enforces (see enforcement_report() / --dry-run for the honest per-field list):
  model                 init_chat_model('<provider>:<id>') pins the exact model. Real.
  tools                 only the bound tools exist — LangGraph has NO ambient tool registry, so nothing
                        else is callable. No disallowed-list is needed; the wall is "nothing else bound".
  egress                enforced INSIDE the fetch_url tool: it host-checks every URL against the charter's
                        egress list BEFORE requesting, and refuses otherwise. A cleaner wall than a hook —
                        BUT it only covers tools THIS builder generates; a third-party LangChain tool you
                        add reaches the network outside this guard (a container is required to fence it).
  budget.steps          recursion_limit (~2x steps for a ReAct loop) hard-stops the graph. Real.
  budget.wall_clock     asyncio.wait_for hard-kills the run. Real.
  credentials.env       a scoped process env keeps only declared env: secrets + OS essentials. Real.
  budget.usd            SOFT — a client-side estimate from token usage x a local price table, checked
                        AFTER the run; it cannot hard-stop a call mid-flight. The report says so plainly.
"""

# ---- stdlib only at import time; langchain/langgraph are imported LAZILY inside run() ----
import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))

# 1. THE CHARTER (embedded dict — the single source of truth; edit = regenerate + bump version)
CHARTER = {
    "charter": "0.2",
    "runtime": "langchain",
    "id": "langchain-docs-researcher",
    "version": "1",
    "owner": {"team": "demo", "on_call": "@owner", "escalation": "owner@example.com"},
    "status": "enabled",
    "context": {
        "trusted_sources": ["prompts/system.md"],
    },
    "model": {"provider": "anthropic", "id": "claude-haiku-4-5"},
    "sandbox": {"isolation": "none", "persist_state": False},
    "budget": {"usd": 0.20, "steps": 8, "wall_clock_seconds": 90},
    "tools": ["fetch_url"],
    "credentials": [
        {"name": "api-auth", "ref": "env:ANTHROPIC_API_KEY", "scope": "anthropic api"}
    ],
    "egress": ["docs.python.org"],
    "approval_tier": {"auto": ["fetch_url"], "human_approval": []},
    "data": {"class": "public", "retention_days": 7, "redact": []},
    "evals": {
        "suite": "evals/cases.yaml",
        "success_metric": {"name": "cites a docs.python.org URL", "slo": "100%"},
    },
}

# Approximate list prices in USD per MILLION tokens, used ONLY for the soft budget.usd estimate.
# VERIFY against current pricing before trusting a number — the report labels usd a client-side
# estimate. Prefix match so a pinned dated id (…-20251001) still resolves.
_PRICE_PER_MTOK = {
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
    "claude-opus-4-8": {"input": 15.0, "output": 75.0},
}


# ============================================================================
# 2. ENFORCEMENT HELPERS — pure, testable, NO langchain import needed
# ============================================================================

# OS essentials the child process needs; every other host secret is dropped unless the charter
# declares it as an env: credential. No provider key rides along unless it is declared.
_ESSENTIAL_VARS = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "TMPDIR",
    "TZ",
    "LANG",
)
_ESSENTIAL_PREFIXES = ("LC_",)


def scoped_env(charter, src=None):
    """The process environment for the run: OS essentials + LC_* + the charter's declared `env:`
    credential(s) — and nothing else. Every other host secret (other API keys, tokens) is dropped,
    so a poisoned page can't exfiltrate a credential the charter never granted.

    `src` defaults to os.environ; pass a plain dict in tests. Pure — does NOT touch os.environ.
    """
    src = os.environ if src is None else src
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


def _host_allowed(host, egress):
    for pattern in egress:
        bare = pattern[2:] if pattern.startswith("*.") else pattern
        if not bare:
            continue
        if host == pattern or host == bare or host.endswith("." + bare):
            return True
    return False


def fetch_egress_check(url, egress):
    """(allow: bool, reason: str) — the pure egress decision the fetch_url tool applies before any
    request. Mirrors builders/claude-headless/egress_guard.py's host-match rule: [any] opens, [none]
    closes, otherwise the URL's host must be on the allow-list (exact, or a real subdomain).
    """
    egress = egress or []
    if "any" in egress:
        return True, f"egress is open ([any]); {url!r} allowed"
    if "none" in egress:
        return False, "egress is [none] (no network); fetch refused"
    host = urlparse(url).hostname or ""
    if not host:
        return False, f"no host to check in url {url!r} against egress {egress}"
    if _host_allowed(host, egress):
        return True, f"egress to '{host}' allowed by allow-list {egress}"
    return False, f"egress to '{host}' not in allow-list {egress}"


def resolve_root(root, base_dir):
    """Resolve the charter's sandbox.filesystem into an absolute jail root. A relative root is
    taken relative to the agent dir (base_dir), so `./out` lands beside agent.py, not at the CWD
    the run happened to start in. Returns None if no root is declared."""
    if not root:
        return None
    if os.path.isabs(root):
        return os.path.realpath(root)
    return os.path.realpath(os.path.join(base_dir, root))


def jail_path(root, user_path):
    """(ok: bool, resolved: str|None, reason: str) — THE filesystem wall for the fs tools. Resolves
    user_path under `root` and refuses anything that escapes: `..` traversal, an absolute path
    outside the root, or a symlink pointing out (realpath resolves symlinks in the existing prefix
    before the prefix check). Same realpath + prefix-check pattern as _read_trusted_sources.

    `root` must already be absolute (see resolve_root). A relative user_path is joined under root;
    an absolute user_path is allowed only if it still resolves inside root."""
    if not root:
        return (
            False,
            None,
            "no filesystem root declared (sandbox.filesystem); fs tools refused",
        )
    base = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(base, user_path))
    if candidate != base and not candidate.startswith(base + os.sep):
        return False, None, f"path {user_path!r} escapes the filesystem jail {root}"
    return True, candidate, f"path {user_path!r} allowed inside {root}"


def estimate_cost(model_id, in_tok, out_tok):
    """A CLIENT-SIDE cost estimate (USD) from token counts x a local price table. Returns None if
    the model isn't in the table. This is the soft budget.usd number — not a metered hard wall.
    """
    price = None
    for k, v in _PRICE_PER_MTOK.items():
        if model_id.startswith(k):
            price = v
            break
    if not price:
        return None
    return round((in_tok / 1e6) * price["input"] + (out_tok / 1e6) * price["output"], 6)


def redact(text, patterns, secret_values):
    """Scrub declared regex patterns AND literal secret values from output/console/log before
    write. Mirrors run_headless.py's _redact. Only as complete as the patterns you declare.
    """
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
    return (m is None), (
        None if m is None else f"matched forbidden /{v}/: {m.group(0)!r}"
    )


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
        ln
        for ln in out.splitlines()
        if any(k in ln.lower() for k in kws) and not _URL.search(ln)
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


# ---- run-log helpers (mirror run_headless.py / the claude-sdk agent) --------------------------
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
    """Delete output files and runs.jsonl entries older than the retention window. Housekeeping
    runs when the agent runs — this is not a daemon."""
    if not retention_days:
        return
    d = _logdir()
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


def _read_trusted_sources(charter, base_dir):
    """Concatenate context.trusted_sources into the system prompt. Fails closed if a source
    escapes the agent dir or isn't on disk — a trusted source that isn't there should refuse,
    not silently run on a weaker prompt."""
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


def _text_of(content):
    """A message's .content may be a plain string or a list of content blocks — coerce to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text", "") or "")
            else:
                parts.append(str(b))
        return "".join(parts)
    return str(content)


# ============================================================================
# --dry-run report — does NOT import langchain
# ============================================================================
def enforcement_report(charter):
    """The honest --dry-run report: what this runner actually enforces, field by field. Uses the
    same block/declared/none vocabulary as the other builders, adapted to LangGraph's mechanisms.
    """
    tools = charter.get("tools") or []
    budget = charter.get("budget") or {}
    egress = charter.get("egress") or []
    data = charter.get("data") or {}
    model = charter.get("model") or {}
    creds = charter.get("credentials") or []
    cred_vars = [c["ref"][4:] for c in creds if c.get("ref", "").startswith("env:")]

    rows = [("block", "status", "run refused unless status is 'enabled'")]
    rows.append(
        (
            "block",
            "model",
            f"init_chat_model('{model.get('provider')}:{model.get('id')}') pins the exact model",
        )
    )
    rows.append(
        (
            "block",
            "tools",
            f"only {tools or '[]'} bound to the agent; LangGraph has no ambient tool registry, so "
            f"nothing else is callable (no disallowed-list needed — the wall is 'nothing else bound')",
        )
    )
    # filesystem tools: jailed to sandbox.filesystem via jail_path (realpath + prefix check)
    fs_tools = [t for t in tools if t in _FS_TOOLS]
    if fs_tools:
        fs_root = resolve_root((charter.get("sandbox") or {}).get("filesystem"), HERE)
        writes = "write_file" in fs_tools
        rows.append(
            (
                "block",
                "tools.fs",
                f"{fs_tools} jailed to sandbox.filesystem={fs_root!r} — every path is resolved with "
                f"realpath and must stay under the root, so '..'/absolute/symlink escapes are refused. "
                + (
                    "write_file is the ONE write capability (path-jailed, confirmed at build)."
                    if writes
                    else "read-only fs access."
                ),
            )
        )
        rows.append(
            (
                "none",
                "tools.fs.scope",
                "this is an APPLICATION-level jail across the tools this builder ships (like egress); "
                "it is not an OS sandbox — process-wide fs containment still needs a container",
            )
        )
    if budget.get("steps"):
        rl = 2 * budget["steps"] + 1
        rows.append(
            (
                "block",
                "budget.steps",
                f"recursion_limit={rl} (~2x steps for a ReAct loop) hard-stops the graph "
                f"(GraphRecursionError)",
            )
        )
    else:
        rows.append(("—", "budget.steps", "not set"))
    if budget.get("wall_clock_seconds"):
        rows.append(
            (
                "block",
                "budget.wall_clock",
                f"asyncio.wait_for(...,timeout={budget['wall_clock_seconds']}) hard-kills the run",
            )
        )
    else:
        rows.append(("—", "budget.wall_clock", "not set"))
    if budget.get("usd"):
        rows.append(
            (
                "declared",
                "budget.usd",
                f"${budget['usd']} — a CLIENT-SIDE estimate from token usage x a local price table, "
                f"checked AFTER the run; cannot hard-stop a call mid-flight. Verify.",
            )
        )
    else:
        rows.append(("—", "budget.usd", "not set"))
    if budget.get("tokens"):
        rows.append(
            (
                "declared",
                "budget.tokens",
                f"{budget['tokens']} — measured post-hoc from usage_metadata; not a mid-call wall",
            )
        )
    # egress: enforced inside fetch_url
    if "any" in egress:
        rows.append(
            (
                "none",
                "egress",
                "egress:[any] — fetch_url reaches any host; network is effectively open (not a wall)",
            )
        )
    elif "none" in egress:
        rows.append(
            (
                "block",
                "egress",
                "egress:[none] — fetch_url refuses every host; no network tool is effective",
            )
        )
    else:
        granted = "fetch_url" in tools
        how = (
            (
                "gates fetch_url: it host-checks each URL against the allow-list BEFORE requesting and "
                "refuses otherwise"
            )
            if granted
            else ("no network tool is granted, so egress is a non-issue for this agent")
        )
        rows.append(
            ("block", "egress", f"enforced inside the tool — {how}; egress={egress}")
        )
    rows.append(
        (
            "none",
            "egress.other",
            "any THIRD-PARTY LangChain tool you add reaches the network OUTSIDE this guard — only "
            "tools this builder generates are egress-checked; a container is required to fence others",
        )
    )
    rows.append(
        (
            "block",
            "credentials.env",
            f"scoped process env keeps {cred_vars or 'no'} declared secret(s) + OS essentials; "
            f"drops every other host secret",
        )
    )
    rows.append(
        (
            "none",
            "sandbox",
            "a real fs/net jail needs a container; this runs in-process on the host",
        )
    )
    rows.append(
        (
            "declared",
            "context",
            "trusted_sources concatenated into the system prompt; fetched pages are NOT auto-tagged "
            "untrusted by this runner",
        )
    )
    if charter.get("approval_tier", {}).get("human_approval"):
        rows.append(
            (
                "none",
                "approval_tier",
                "unattended run has no approval queue — such actions are contained by being left "
                "OUT of tools, not gated at run time",
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
            (
                "declared",
                "data.redact",
                "nothing to redact — no patterns, no env: credentials",
            )
        )
    if data.get("retention_days"):
        rows.append(
            (
                "block",
                "data.retention",
                f"output files & runs.jsonl entries older than {data['retention_days']}d pruned when "
                f"the agent runs (housekeeping, not a daemon)",
            )
        )
    else:
        rows.append(
            (
                "declared",
                "data.retention",
                "no retention window; logs kept indefinitely",
            )
        )
    evals = charter.get("evals") or {}
    rows.append(
        (
            "declared",
            "evals",
            f"suite={evals.get('suite')!r} — gated inline in run() if that file exists on disk",
        )
    )

    lines = [f"=== langchain enforcement: {charter['id']} v{charter['version']} ==="]
    lines.append(
        f"harness: LangGraph minimal (create_react_agent)   provider: {model.get('provider')}"
    )
    for status, field, note in rows:
        lines.append(f"  {status:<10} {field:<20} {note}")
    lines.append("")
    prov = model.get("provider")
    if prov == "anthropic":
        lines.append(
            "billing: anthropic api-key mode — bills your Anthropic API account per token "
            "(ANTHROPIC_API_KEY). budget.usd is a local estimate, not a metered cap."
        )
    else:
        lines.append(
            f"billing: provider '{prov}' — bills that provider's account per token; "
            "budget.usd is a local estimate, not a metered cap."
        )
    lines.append("\n[dry-run] not executed.")
    return "\n".join(lines)


# ============================================================================
# 3. tool builder + run() — imports langchain/langgraph LAZILY, builds the agent
#    FROM the charter, runs under a wall-clock + recursion cap, gates evals, logs
# ============================================================================
def make_fetch_url(egress):
    """Return an egress-guarded LangChain `fetch_url` tool. THE egress wall for this runtime: it
    host-checks every URL against `egress` BEFORE any request. Imports langchain lazily, so the
    module (and --dry-run) never pulls it in."""
    from langchain_core.tools import tool

    @tool
    def fetch_url(url: str) -> str:
        """Fetch the visible text of a web page by URL. Only hosts permitted by this agent's egress
        policy are reachable; any other host is refused."""
        allow, reason = fetch_egress_check(url, egress)
        if not allow:
            return f"[egress denied] {reason}"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "charter-langchain-agent"}
            )
            with urllib.request.urlopen(
                req, timeout=20
            ) as r:  # noqa: S310 - host already egress-checked
                raw = r.read(300_000)
            return raw.decode("utf-8", "replace")[:20000]
        except Exception as e:  # noqa: BLE001
            return f"[fetch error] {e}"

    return fetch_url


# ---- filesystem tools: each jailed to the charter's sandbox.filesystem root via jail_path() ----
# The jail (jail_path) is THE fs wall — written once, tested, reused. A poisoned page can steer a
# path, but not out of the root. Read tools are safe to grant; write_file is the one write
# capability (confirm it aloud). None of these exist unless the charter grants them AND declares a
# root, so a text-only or fetch-only agent has no filesystem reach at all.
_MAX_GREP_FILES = 500
_MAX_GREP_MATCHES = 200


def make_read_file(root):
    """Return a `read_file` tool jailed to `root`."""
    from langchain_core.tools import tool

    @tool
    def read_file(path: str) -> str:
        """Read a UTF-8 text file. Only paths inside this agent's declared filesystem root are
        allowed; anything escaping the root is refused."""
        ok, resolved, reason = jail_path(root, path)
        if not ok:
            return f"[fs denied] {reason}"
        try:
            with open(resolved, encoding="utf-8", errors="replace") as f:
                return f.read(200_000)
        except Exception as e:  # noqa: BLE001
            return f"[read error] {e}"

    return read_file


def make_list_dir(root):
    """Return a `list_dir` tool jailed to `root`."""
    from langchain_core.tools import tool

    @tool
    def list_dir(path: str = ".") -> str:
        """List the entries of a directory inside this agent's declared filesystem root."""
        ok, resolved, reason = jail_path(root, path)
        if not ok:
            return f"[fs denied] {reason}"
        try:
            return "\n".join(sorted(os.listdir(resolved))) or "(empty)"
        except Exception as e:  # noqa: BLE001
            return f"[list error] {e}"

    return list_dir


def make_grep(root):
    """Return a `grep` tool that regex-searches text files under `root` (bounded)."""
    from langchain_core.tools import tool

    @tool
    def grep(pattern: str, path: str = ".") -> str:
        """Search for a regex in UTF-8 text files under a path inside this agent's declared
        filesystem root. Returns matching `relative/path:line` entries (bounded)."""
        ok, resolved, reason = jail_path(root, path)
        if not ok:
            return f"[fs denied] {reason}"
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"[grep error] bad regex: {e}"
        base = os.path.realpath(root)
        hits, seen = [], 0
        walk = (
            os.walk(resolved)
            if os.path.isdir(resolved)
            else [(os.path.dirname(resolved), [], [os.path.basename(resolved)])]
        )
        for dirpath, _dirs, files in walk:
            for fn in files:
                if seen >= _MAX_GREP_FILES or len(hits) >= _MAX_GREP_MATCHES:
                    break
                seen += 1
                fp = os.path.join(dirpath, fn)
                try:
                    with open(fp, encoding="utf-8", errors="strict") as f:
                        for i, line in enumerate(f, 1):
                            if rx.search(line):
                                rel = os.path.relpath(fp, base)
                                hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                                if len(hits) >= _MAX_GREP_MATCHES:
                                    break
                except (UnicodeDecodeError, OSError):
                    continue  # skip binaries / unreadable files
        return "\n".join(hits) if hits else "(no matches)"

    return grep


def make_write_file(root):
    """Return a `write_file` tool jailed to `root` — the one WRITE capability. Creates parent dirs
    inside the jail; a path escaping the root is refused before any write."""
    from langchain_core.tools import tool

    @tool
    def write_file(path: str, content: str) -> str:
        """Write UTF-8 text to a file inside this agent's declared filesystem root, creating parent
        directories as needed. A path escaping the root is refused."""
        ok, resolved, reason = jail_path(root, path)
        if not ok:
            return f"[fs denied] {reason}"
        try:
            parent = os.path.dirname(resolved)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(content)
            rel = os.path.relpath(resolved, os.path.realpath(root))
            return f"wrote {len(content)} chars to {rel}"
        except Exception as e:  # noqa: BLE001
            return f"[write error] {e}"

    return write_file


# The vetted catalog: verb -> (factory, kind). NET tools are scoped by egress; FS tools by the
# jail root (sandbox.filesystem). To add a tool, add a guarded factory above and one row here —
# the generator selects from this catalog; it never hand-writes a guard.
_NET_TOOLS = {"fetch_url": make_fetch_url}
_FS_TOOLS = {
    "read_file": make_read_file,
    "list_dir": make_list_dir,
    "grep": make_grep,
    "write_file": make_write_file,
}


def build_tools(charter, egress, fs_root):
    """Map charter tool verbs -> guarded LangChain tool objects, from the vetted catalog. Net verbs
    are scoped by `egress`; fs verbs by `fs_root` (resolved sandbox.filesystem). Text-only is [].
    Fails closed: an unknown verb, or an fs tool without a declared root, refuses to build.
    """
    tools = []
    for n in charter.get("tools") or []:
        if n in _NET_TOOLS:
            tools.append(_NET_TOOLS[n](egress))
        elif n in _FS_TOOLS:
            if not fs_root:
                raise ValueError(
                    f"tools: {n!r} needs a filesystem root, but sandbox.filesystem is not set — "
                    "declare it (e.g. sandbox.filesystem: ./out) or drop the fs tool"
                )
            tools.append(_FS_TOOLS[n](fs_root))
        else:
            known = ", ".join(sorted({*_NET_TOOLS, *_FS_TOOLS}))
            raise ValueError(
                f"tools: unknown verb {n!r} — this builder's catalog is: {known} "
                "(or text-only: tools: [])"
            )
    return tools


async def run(trigger, prompt):
    charter = CHARTER
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731

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

    # Apply the scoped env by REPLACING the process env — langchain reads the provider key
    # (ANTHROPIC_API_KEY) straight from os.environ, so this .py must run as its own process.
    env = scoped_env(charter)
    os.environ.clear()
    os.environ.update(env)

    try:
        from langchain.chat_models import init_chat_model

        # Version-aware: LangChain 1.0 moved the minimal harness to langchain.agents.create_agent
        # (arg `system_prompt`); older LangGraph exposes langgraph.prebuilt.create_react_agent
        # (arg `prompt`, deprecated since V1). Prefer the new one; fall back so both lines work.
        try:
            from langchain.agents import create_agent as _make_agent

            _prompt_kw = "system_prompt"
        except ImportError:
            from langgraph.prebuilt import create_react_agent as _make_agent

            _prompt_kw = "prompt"
    except ImportError:
        print(
            "REFUSED: langchain / langgraph are not installed.\n"
            "  pip install -r builders/langchain/requirements.txt\n"
            "  (langgraph + langchain + langchain-anthropic)"
        )
        sys.exit(6)
    try:
        from langgraph.errors import GraphRecursionError
    except (
        Exception
    ):  # noqa: BLE001 - older/newer layouts; fall back to name-based detection
        GraphRecursionError = None

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

    model = charter["model"]
    params = model.get("params") or {}
    egress = charter.get("egress") or []
    budget = charter.get("budget") or {}
    fs_root = resolve_root((charter.get("sandbox") or {}).get("filesystem"), HERE)

    try:
        tools = build_tools(charter, egress, fs_root)
    except ValueError as e:
        print(f"REFUSED: {e}")
        sys.exit(2)

    llm = init_chat_model(f"{model['provider']}:{model['id']}", **params)
    agent = _make_agent(model=llm, tools=tools, **{_prompt_kw: system_prompt})

    recursion_limit = (2 * budget["steps"] + 1) if budget.get("steps") else 25
    wall_clock = budget.get("wall_clock_seconds") or 90
    inputs = {"messages": [{"role": "user", "content": prompt}]}

    data = charter.get("data") or {}
    patterns = data.get("redact") or []
    secret_values = [
        os.environ[c["ref"][4:]]
        for c in (charter.get("credentials") or [])
        if c.get("ref", "").startswith("env:") and c["ref"][4:] in os.environ
    ]

    run_ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    t0 = time.time()
    result = None
    err = None
    timed_out = False
    step_capped = False
    try:
        result = await asyncio.wait_for(
            agent.ainvoke(inputs, config={"recursion_limit": recursion_limit}),
            timeout=wall_clock,
        )
    except asyncio.TimeoutError:
        timed_out = True
    except Exception as e:  # noqa: BLE001
        err = e
        is_recursion = (
            GraphRecursionError is not None and isinstance(e, GraphRecursionError)
        ) or "recursion" in type(e).__name__.lower()
        step_capped = is_recursion
    elapsed = round(time.time() - t0, 1)

    msgs = (result or {}).get("messages", []) if isinstance(result, dict) else []
    result_text = _text_of(msgs[-1].content) if msgs else ""
    in_tok = out_tok = 0
    for m in msgs:
        um = getattr(m, "usage_metadata", None)
        if um:
            in_tok += um.get("input_tokens", 0) or 0
            out_tok += um.get("output_tokens", 0) or 0
    cost = estimate_cost(model["id"], in_tok, out_tok)
    tool_calls = sum(1 for m in msgs if getattr(m, "tool_calls", None))

    if timed_out:
        outcome = "killed"
        print(f"KILLED: wall-clock timeout {wall_clock}s hit")
    elif step_capped:
        outcome = "killed"
        print(
            f"KILLED: recursion_limit {recursion_limit} hit (budget.steps={budget.get('steps')})"
        )
    elif err is not None:
        outcome = "failed"
        print(f"FAILED: {type(err).__name__}: {err}")
    elif not msgs:
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
                for iname, ok, detail in check_all(
                    case.get("invariants", []), result_text
                ):
                    invariants.append(
                        {
                            "case": case.get("id"),
                            "invariant": iname,
                            "pass": ok,
                            "detail": detail,
                        }
                    )
                    allpass = allpass and ok
            if cases:
                outcome = "complete" if allpass else "failed"

    # redact declared patterns + declared secret values from everything persisted; evals above ran
    # on the RAW result so redaction can't mask (or break) a passing invariant
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
            "model": model["id"],
            "duration_s": elapsed,
            "outcome": outcome,
            "cost_usd": cost,
            "tokens": {"input": in_tok, "output": out_tok},
            "tool_calls": tool_calls,
            "invariants": invariants,
            "output_chars": len(safe_result),
            "output_file": os.path.basename(out_path),
        }
    )
    _prune_logs(data.get("retention_days"))

    print(f"\n--- output ({len(safe_result)} chars, saved to logs/) ---")
    print(safe_result[:800] + ("…" if len(safe_result) > 800 else ""))
    print(
        f"\ncost_usd~: {cost}   duration: {elapsed}s   outcome: {outcome}   "
        f"tokens: {in_tok}in/{out_tok}out   tool_calls: {tool_calls}"
    )
    if invariants:
        for r in invariants:
            print(
                f"  {'PASS' if r['pass'] else 'FAIL'}  {r['case']}:{r['invariant']}"
                + (f"  <- {r['detail']}" if r["detail"] else "")
            )
    if outcome == "failed":
        sys.exit(1)


# ============================================================================
# 4. main()
# ============================================================================
def main():
    ap = argparse.ArgumentParser(
        description="langchain-docs-researcher — a CHARTER-governed LangChain/LangGraph agent"
    )
    ap.add_argument("trigger", nargs="?", default="manual")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the enforcement report; do not run, no tokens spent",
    )
    ap.add_argument(
        "--prompt", default=None, help="task prompt; default is prompts/task.md"
    )
    args = ap.parse_args()

    if args.dry_run:
        print(enforcement_report(CHARTER))
        return

    asyncio.run(run(args.trigger, args.prompt))


if __name__ == "__main__":
    main()
