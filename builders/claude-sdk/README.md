# claude-sdk builder

One builder in the CHARTER repo. It turns a charter into an agent on the **Claude Agent SDK**
(Claude Code as a library, programmable in Python) — the same engine as headless `claude -p`,
in-process instead of behind a CLI wrapper. The shared engine (schema, validators, eval checks)
lives in [`../../core/`](../../core/); the doctrine (the *why*) in
[`../../framework/`](../../framework/). This folder only adds the SDK generator and reference agent.

A charter lists the only things an agent may do. This builder's artifact is a single
self-contained `agent.py` — no wrapper script; it's directly runnable by a human or from cron.
Model, tools, turns, timeout, egress, and environment all come from the charter embedded in it.
**If it's not in the file, the agent can't do it.**

## Quickstart: build your own agent
You need Claude Code (to run the interview), a terminal, and Python 3.

The **interview lives at the repo root.** Open the repo root in Claude Code and say what you
want, e.g. *"Help me build a release-notes summarizer agent."* The framework-neutral
**new-agent** interview asks the name first, then which runtime — pick **`claude-sdk`** — then
plain questions (never silently deciding model, budget, capabilities, or network), and offers a
plug-in round (extra `.md` files, MCP servers, skills). It captures a **brief** and hands off to
**create-claude-sdk-agent** (this builder's generator, at repo-root
`.claude/skills/create-claude-sdk-agent/`), which re-confirms the concretized safety values and
generates a self-contained project at `agents/<id>/` here: `brief.yaml`, `charter.yaml`,
`agent.py` (the artifact — the charter embedded in it), `prompts/`, optional `evals/`, and
`requirements.txt`.

Then, from this folder:
```bash
pip install -r agents/<id>/requirements.txt   # also needs the `claude` CLI (Node) on PATH + auth
```
- See what's really enforced (no tokens): `python3 agents/<id>/agent.py --dry-run`
- Run it: `./agents/<id>/agent.py manual` — or `python3 agents/<id>/agent.py cron` from a
  scheduler. No `run.sh` wrapper: the artifact IS the runnable file.

## New here? Read `GUIDE.md`

## Auth: pick one mode, honestly
The interview asks which mode and records it as the charter's credential:
- **`api-key`** — `ANTHROPIC_API_KEY`, billed to your Anthropic console account. The sanctioned,
  shareable mode for an unattended agent.
- **`subscription`** — `CLAUDE_CODE_OAUTH_TOKEN`, rides your Claude Code seat. Individual use
  only, per Anthropic's SDK terms — don't put this on shared infrastructure or run it as
  someone else's agent.

A stray `ANTHROPIC_API_KEY` in the environment silently outranks `CLAUDE_CODE_OAUTH_TOKEN`, so
the agent's scoped env drops whichever mode's var you did NOT declare — the "no surprise bill"
guard. The `--dry-run` report's billing/ToS line tells you which mode is live.

## What's here
```
builders/claude-sdk/                 # the SDK builder: reference agent + your generated agents
├── README.md · GUIDE.md             # the map · the step-by-step
├── example.agent.py                  # THE REFERENCE AGENT: embedded charter, enforced options,
│                                     # PreToolUse egress hook, eval gate, logging — read it
├── requirements.txt                  # claude-agent-sdk pin (also needs the `claude` CLI + auth)
├── prompts/ · evals/                  # this reference agent's system/task prompts and eval cases
└── agents/                           # YOUR agents (generated; empty on a fresh clone)

shared, reused by every builder (one level up):
../../core/       charter.schema.yaml · CHARTER.md · validate.py · loader.py · registry.py · eval_checks.py
../../framework/  the one-page doctrine
```

The build skill (`create-claude-sdk-agent`) lives at the **repo root** `.claude/skills/`,
alongside the framework-neutral `new-agent` interview, so it's discoverable wherever you open
Claude Code.

## Enforced walls + honest limits
Parity with [`../claude-headless/`](../claude-headless/): `model`, `tools` (dangerous tools
denied via `disallowed_tools`), `budget.steps` (`max_turns`), the wall-clock timeout
(`asyncio.wait_for`), `egress`, env-scoped credentials, log redaction, retention pruning, the
eval gate, and a run log.

- **Egress is a `PreToolUse` hook, not the SDK's `can_use_tool` permission callback.** Under
  `permission_mode="bypassPermissions"` (required for an unattended agent — nobody is there to
  answer a prompt), the SDK's own permission order runs `bypassPermissions` BEFORE
  `can_use_tool` is ever consulted, so a `can_use_tool` callback would silently never fire. A
  `PreToolUse` hook runs earlier and its deny holds regardless of mode — the same mechanism
  `../claude-headless/egress_guard.py` uses, for the same reason.
- **`bypassPermissions` doesn't wall non-dangerous built-ins.** `allowed_tools` is declarative
  only under bypass; only `disallowed_tools` (the dangerous-tools deny list) actually removes a
  tool. Disclosed plainly in the `--dry-run` report.
- **No container, no fs/net jail.** The `claude` CLI subprocess the SDK spawns runs as your
  user; `Bash`/MCP reach the network *outside* the egress hook (which gates only
  `WebFetch`/`WebSearch`).
- **`budget.usd` (`max_budget_usd`) is a client-side cost estimate**, not a metered hard wall —
  verify against your actual bill.

## Good-to-have ergonomics (optional, not walls)
The interview also offers a couple of SDK-only conveniences, recorded under
`extensions.observability` (declared, never enforced) and overridable per run:
- **Stream the agent loop** — echo assistant text and each tool call live as the run happens
  (`--stream` / `--quiet`). On for hands-on runs, off for cron.
- **Tool-call trace** — a per-run `logs/<ts>.trace.jsonl`, one redacted line per tool call, for
  audit/debugging (`--trace` / `--no-trace`); the run-log entry also carries a `tool_calls` count.
Both are redacted like the saved output. Authority-expanding SDK features (subagents, sessions,
`add_dirs`) are deliberately not toggled from here — they belong in the safety interview.

## vs. headless
Same charter, same walls, same honesty. The difference is the shape of the artifact: the SDK
agent is a portable, single-file Python program (`agent.py`) you can run in-process, embed in
another app, or drop into CI; headless is a `claude -p` command behind `run.sh`. Pick whichever
shape fits where the agent needs to live. The doctrine (the *why*, the honest limits that hold
across every runtime) is one page: [`../../framework/README.md`](../../framework/README.md).
