---
name: create-claude-sdk-agent
description: >
  Turn a framework-neutral agent brief into a CHARTER-governed agent on the Claude Agent SDK — a
  single self-contained, directly-runnable agent.py (embedded charter, enforced options, eval gate,
  logging). Invoked by the new-agent interview after the creator picks the claude-sdk runtime (or run
  standalone against an existing agents/<id>/brief.yaml). ALWAYS builds against the latest SDK docs.
---

# create-claude-sdk-agent

You receive a framework-neutral **brief** (from the new-agent interview) and turn it into a
self-contained **Claude Agent SDK** agent under `builders/claude-sdk/agents/<id>/`. The headline
artifact is a single **`agent.py`** — directly runnable (`./agent.py manual`, or
`python3 agent.py cron` from cron), no wrapper script. It embeds its own charter, builds
`ClaudeAgentOptions` from it, enforces the walls, gates evals, and logs.

## ALWAYS build against the latest SDK docs (do not trust memory)
The SDK's API drifts — the package itself was renamed (`claude-code-sdk` → `claude-agent-sdk`,
`ClaudeCodeOptions` → `ClaudeAgentOptions`). BEFORE generating, WebFetch and confirm the current
API, then record the version/date you targeted in the `agent.py` header. The pages:
- https://code.claude.com/docs/en/agent-sdk/python — `query()`, `ClaudeAgentOptions` fields,
  `ResultMessage.total_cost_usd`
- https://code.claude.com/docs/en/agent-sdk/permissions — the tool-permission + hooks model
- https://code.claude.com/docs/en/agent-sdk/overview — auth + capabilities

Confirm in particular the DENY mechanism (see "Critical" below). If a field or API changed,
adapt the generated `agent.py` and say plainly that you did.

## First: write the brief to disk
Create `builders/claude-sdk/agents/<id>/` and write the brief there as `brief.yaml` (the durable
chart + regeneration input). If run standalone, read an existing `agents/<id>/brief.yaml`
instead of re-interviewing.

## Ask the auth mode (safety + billing — confirm aloud)
The one genuinely SDK-specific interview question. Two modes:
- **api-key** — `ANTHROPIC_API_KEY` (console billing). The **sanctioned, shareable** mode:
  Anthropic's SDK terms permit API-key auth for products/agents you distribute. Recommended /
  default, and REQUIRED for anything that runs on someone else's behalf or on shared infra.
- **subscription** — `CLAUDE_CODE_OAUTH_TOKEN` (rides a Claude Code Pro/Max seat). Licensed for
  **individual use only**; Anthropic's own SDK docs say NOT to offer claude.ai login /
  subscription rate limits in third-party products. Fine for your own agents on your own
  machine; do NOT ship agents that run on other people's subscription.

Record the choice in the brief and express it in the charter as the auth **credential**:
- api-key → `credentials: [{name: api-auth, ref: env:ANTHROPIC_API_KEY, scope: "anthropic api"}]`
- subscription → `credentials: [{name: sub-auth, ref: env:CLAUDE_CODE_OAUTH_TOKEN, scope: subscription}]`

State the honest footgun: **a stray `ANTHROPIC_API_KEY` silently outranks
`CLAUDE_CODE_OAUTH_TOKEN`** — so in subscription mode the generated agent DROPS the API key from
its scoped env (and vice versa in api-key mode), the framework's "no surprise bill" guard. This
is enforced by `scoped_env` + `auth_mode` in `agent.py`.

## Re-confirm the concretized safety values (concretizing is where safety hides)
Same discipline as the headless generator. The brief holds neutral intents; you concretize and
RE-CONFIRM each aloud before writing:
- **model tier** → exact pinned id (cheap → `claude-haiku-4-5`; a stronger model only for
  genuinely hard reasoning). Say the id; get a yes. → `model.id`, and
  `ClaudeAgentOptions(model=...)`.
- **capabilities** → exact Claude Code tool names (`WebFetch`, `WebSearch`, `Read`, `Grep`,
  `Glob`, ...). Text-only → `tools: []`. Destructive tools (`Bash`, `Write`, `Edit`,
  `MultiEdit`, `NotebookEdit`, `Task`) are LEFT OUT and the agent puts them in
  `disallowed_tools` (removed from context, enforced even under bypassPermissions).
- **network policy** → exact `egress` list. **Cross-check WebSearch:** a scoped egress list
  denies WebSearch outright (a search can't be confined to hosts) — only `egress: [any]` makes
  it fire. If the brief wants both, surface the conflict and resolve it (drop WebSearch, or
  `[any]` and say plainly that's not a wall). Never ship a tool the runtime will silently deny.
- **spend ceiling** → `budget.usd` (→ `max_budget_usd`, note it's a client-side ESTIMATE), plus
  `budget.steps` (→ `max_turns`) and `budget.wall_clock_seconds` (→ `asyncio.wait_for` +
  generator close).

## Ask the good-to-have SDK ergonomics (optional — offer, don't impose)
The SDK runs the agent loop in-process, so it can surface things a bare `claude -p` can't. These
are **operational ergonomics, not walls** — they observe the enforced loop, they don't expand what
it may do. Offer them, default OFF (a quiet agent is the safe default for cron/logs), and record
the choice in the brief and under `extensions.observability` in the charter:
- **Stream the agent loop** (`stream`) — echo assistant text and each tool call to the console live
  as the run happens. Great for a human running by hand or developing; usually off for cron. The
  generated `agent.py` also takes `--stream` / `--quiet` to override per run.
- **Tool-call trace** (`trace`) — write a per-run `logs/<ts>.trace.jsonl`, one redacted line per
  tool call, for audit/debugging. Overridable with `--trace` / `--no-trace`. The run-log entry also
  gets a `tool_calls` count either way.
Both are redacted with the same patterns + credential values as the saved output, and both are
driven from the message loop the agent already consumes (no extra hook). Set them in the charter as
`extensions: {observability: {stream: <bool>, trace: <bool>}}` — `extensions` is declared-only, so
the `--dry-run` report lists observability as `declared`, never a wall.

**Deliberately NOT auto-offered here** (say so if asked — they change authority or state, so they
belong in the safety interview or a later iteration, not a casual toggle): subagents
(`agents=`/`AgentDefinition`) spawn more agents; sessions (`resume`/`continue_conversation`) persist
state across runs (interacts with `sandbox.persist_state`); `add_dirs`/`cwd`/`setting_sources`
widen filesystem and ambient-config reach. Don't wire these from a nice-to-have prompt.

## Critical: how the SDK actually enforces tools + egress (verify each build)
The non-obvious mechanic that MUST be preserved, and exactly why "latest docs" matters:
- Unattended agents can't answer a permission prompt, so they run
  `permission_mode="bypassPermissions"`.
- bypassPermissions AUTO-APPROVES at the permission-mode step, which runs BEFORE the
  `can_use_tool` permission callback — so a `can_use_tool` egress check would SILENTLY NEVER
  FIRE (the SDK even warns `CLAUDE_SDK_CAN_USE_TOOL_SHADOWED`).
- The documented per-call enforcement that holds under bypassPermissions is a **`PreToolUse`
  hook** (`hooks={"PreToolUse": [HookMatcher(matcher="WebFetch|WebSearch", hooks=[...])]}`),
  returning `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
  "permissionDecisionReason": ...}}`. This is the SAME mechanism
  `builders/claude-headless/egress_guard.py` uses.
- Honest tool-scope caveat to carry into the report: `disallowed_tools` reliably removes the
  dangerous set; but under bypassPermissions `allowed_tools` does NOT itself wall out other
  built-ins (Read/Glob/Grep) — the agent's `--dry-run` report says this plainly. Do not
  overclaim.
- Register the PreToolUse hook **unconditionally** (regardless of whether the charter grants
  WebFetch/WebSearch). It then denies any WebFetch/WebSearch attempt that a scoped or `[none]`
  egress forbids — a real network backstop even for a text-only agent. The report says "block"
  and names it a backstop when no net tool is granted; only `egress:[any]` leaves network open.

If the live docs show a newer/cleaner deny mechanism, use it and note the change.

## Map the rest of the brief to the charter
`owner`; `data` (class / redact / retention); `sandbox` (`none` for text-only/read-only work — a
real fs/net jail still needs a container, since the SDK spawns the `claude` CLI as a normal
subprocess, not isolated); `context.trusted_sources` (the instruction files, concatenated into
`system_prompt`; fetched pages/memory stay untrusted); `evals`; `extensions.human_verification`
only if a human must verify. Set `runtime: claude-sdk`; `charter:` = the schema's `const` (read
`core/charter.schema.yaml`); `status: enabled`; `version: "1"` (bump on change).

## Generate the agent project

```
builders/claude-sdk/agents/<id>/
├── brief.yaml         # the neutral chart (written first)
├── charter.yaml       # generated, then validated (audit + regen source)
├── agent.py           # THE ARTIFACT: self-contained, executable (chmod +x); embeds the charter
├── prompts/
│   ├── system.md      # REAL instructions (role, never-do, format, a good/bad example)
│   └── task.md        # the default task it runs each time
├── evals/cases.yaml   # optional — runnable invariants
├── requirements.txt   # claude-agent-sdk (+ the `claude` CLI/Node + auth; the Python SDK does NOT bundle the binary)
└── logs/              # created on first run (gitignored)
```

Generate `agent.py` in the exact shape of `builders/claude-sdk/example.agent.py` — study that
file directly, it is the reference artifact:
- the module header docstring, rewritten for THIS agent: the first line reads `<id> — a
  CHARTER-governed agent ...`, and the `Run:` line references `agent.py` (this agent's filename),
  NOT `example.agent.py`. Rewrite every `example.agent.py` occurrence in the header to `agent.py`,
  and set the `argparse` description to `<id>`. Leave no `example.agent.py` or the reference name
  behind.
- embedded `CHARTER` dict, `DANGEROUS` list
- the pure helpers: `auth_mode`, `scoped_env` (with the auth-mode cross-shadow guard + `~`
  expansion), `egress_decision` + `_host_allowed`, `redact`, `observability` + `trace_line`,
  the inlined eval_checks (`check_one`/`check_all`, copied verbatim from `core/eval_checks.py` —
  a self-contained agent can't import `core/`), `enforcement_report`
- the async `run()`: lazy SDK import that fails closed with a pip hint, options built from the
  charter, the PreToolUse egress hook, `asyncio.wait_for` wall-clock with clean generator close
  (`gen.aclose()`), the observability event handler (live stream + trace file, both redacted),
  eval gate on the RAW result before redaction, jsonl run-log + retention prune + collision-safe
  output save
- `main()` with `--dry-run` that prints the report WITHOUT importing the SDK, plus
  `--stream/--quiet/--trace/--no-trace` overrides

The charter is embedded (the `.py` runs standalone) AND written as a sibling `charter.yaml`
(audit + regeneration) — write `charter.yaml` in the field order of
`builders/claude-headless/examples/repo-researcher.charter.yaml`.

### cases.yaml (if evals opted in)
Same invariant vocabulary as the headless generator: `contains` / `not_contains` / `matches` /
`not_matches` / `contains_url` / `valid_json` / `claims_cited: [...]`:
```yaml
success_metric: "cites a docs.python.org URL"
slo: "100%"
cases:
  - id: cites-a-source
    input: "What does str.removeprefix() do, and since which version?"
    invariants:
      - contains_url: true
      - claims_cited: [removeprefix, "3.9", version, returns, string]
    human_check: "click the cited docs.python.org page; confirm the version and behavior are real"
```

## Validate and finish
1. `python3 core/validate.py builders/claude-sdk/agents/<id>/charter.yaml` (from the REPO ROOT)
   → loop until `VALID`.
2. Read the SAFETY fields back in plain English (auth mode + which env var is kept and which
   DROPPED; model + why; budget; tools; egress).
3. Dry-run (no SDK import, no tokens):
   `python3 builders/claude-sdk/agents/<id>/agent.py --dry-run` → walk the honest
   block/declared/none report.
4. Offer to run it once — needs `pip install -r builders/claude-sdk/agents/<id>/requirements.txt`,
   the `claude` CLI on PATH, and the chosen auth env var set —
   `./builders/claude-sdk/agents/<id>/agent.py manual`. For deeper repeatable evals, mention the
   standalone gen-evals skill. A human still confirms any factual claim.
5. To change anything, re-run the new-agent interview (or re-run this generator against the
   edited `brief.yaml`) — never hand-edit `charter.yaml` or `agent.py`.
