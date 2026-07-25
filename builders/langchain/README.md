# langchain builder

One builder in the CHARTER repo. It turns a charter into an agent on **LangGraph** (the minimal
`create_react_agent` harness, the enforceable substrate under LangChain and Deep Agents) — a
single self-contained Python program. The shared engine (schema, validators, eval checks) lives
in [`../../core/`](../../core/); the doctrine (the *why*) in [`../../framework/`](../../framework/).
This folder only adds the LangChain generator and reference agent.

A charter lists the only things an agent may do. This builder's artifact is a single
self-contained `agent.py` — no wrapper script; it's directly runnable by a human or from cron.
Model, tools, steps, timeout, egress, and environment all come from the charter embedded in it.
**If it's not in the file, the agent can't do it.**

## Why this builder is different
The Claude runtimes lean on a permission system (headless `--allowedTools` + a hook; the SDK's
`disallowed_tools` + a hook). LangGraph gives you almost none of that — **you build the agent
loop yourself.** That cuts both ways, and it's the whole point of adding it:

- **The tool wall is stronger here.** LangGraph has no ambient tool registry, so the *only* tools
  that exist are the ones this builder binds. There is nothing to "disallow" — the wall is
  "nothing else is bound." A text-only agent (`tools: []`) literally cannot call anything.
- **Egress is cleaner here.** It lives *inside* the `fetch_url` tool: the tool host-checks every
  URL against the charter's `egress` list before it makes a request. No hook, no permission-mode
  ordering puzzle.
- **`budget.usd` is softer here.** LangGraph has no built-in spend cap, so the agent estimates
  cost after the run from token usage times a local price table. It can report a breach; it
  cannot hard-stop a call mid-flight. The `--dry-run` report says so plainly.

## Quickstart: build your own agent
You need Claude Code (to run the interview), a terminal, and Python 3.

The **interview lives at the repo root.** Open the repo root in Claude Code and say what you want,
e.g. *"Help me build a docs researcher agent."* The framework-neutral **new-agent** interview
asks the name first, then which runtime — pick **`langchain`** — then plain questions (never
silently deciding model, budget, capabilities, or network). It captures a **brief** and hands off
to **create-langchain-agent** (this builder's generator, at repo-root
`.claude/skills/create-langchain-agent/`), which re-confirms the concretized safety values and
generates a self-contained project at `agents/<id>/` here: `brief.yaml`, `charter.yaml`,
`agent.py` (the artifact — the charter embedded in it), `prompts/`, optional `evals/`, and
`requirements.txt`.

Then provision it once per machine, from the repo root:
```bash
python3 core/provision.py builders/langchain/agents/<id>
export ANTHROPIC_API_KEY=sk-...               # the provider key the charter declares
```
That builds `agents/<id>/.venv` (nothing is installed outside the agent's own directory) and
writes `agents/<id>/run`.
- See what's really enforced (no tokens): `./agents/<id>/run --dry-run`
- Run it: `./agents/<id>/run` — or `./agents/<id>/run cron` from a scheduler. The shim resolves
  every path absolutely and activates nothing, so a manual run and a cron run take the same path.

## New here? Read `GUIDE.md`

## Tool catalog
The generator selects an agent's tools from a **vetted, guarded catalog** in `example.agent.py` —
it never hand-writes a tool's guard (that's where path-traversal and host-check bugs hide). Each
selected tool is copied into the generated `agent.py`, so it stays self-contained.

| verb | class | scope | guard |
|------|-------|-------|-------|
| `fetch_url` | read (net) | `egress` | host-checked before the request |
| `read_file` | read (fs) | `sandbox.filesystem` | path jailed to the root |
| `list_dir` | read (fs) | `sandbox.filesystem` | path jailed to the root |
| `grep` | read (fs) | `sandbox.filesystem` | path jailed to the root |
| `write_file` | **write (fs)** | `sandbox.filesystem` | path jailed to the root |

Any filesystem tool requires a declared `sandbox.filesystem` root (e.g. `./out`); the agent fails
closed at build without one. The path jail (`jail_path`) resolves with `realpath` and refuses
`..`, absolute, and symlink escapes — one primitive, reused, unit-tested. There is deliberately
**no `bash`**: an arbitrary shell defeats every wall (that's the "you need a container"
conversation). To extend the catalog, add a guarded factory + a test in `example.agent.py`.

## Provider & auth
This builder ships **Anthropic** wired (default `claude-haiku-4-5`, key from `ANTHROPIC_API_KEY`),
with `model.provider` read from the charter so a second provider is a small follow-up. The
agent's scoped process environment keeps only the declared `env:` credential(s) plus OS
essentials and drops every other host secret, so a poisoned page can't exfiltrate a key the
charter never granted.

## What's here
```
builders/langchain/                   # the LangChain builder: reference agent + your generated agents
├── README.md · GUIDE.md · CLAUDE.md   # the map · the step-by-step · how to work in here
├── example.agent.py                   # THE REFERENCE AGENT: embedded charter, pinned model,
│                                       # egress-guarded fetch_url tool, eval gate, logging — read it
├── requirements.txt                   # langgraph + langchain + langchain-anthropic (pin vs live docs)
├── prompts/ · evals/                   # this reference agent's system/task prompts and eval cases
├── examples/                           # a validated reference charter
└── agents/                            # YOUR agents (generated; empty on a fresh clone)

shared, reused by every builder (one level up):
../../core/       charter.schema.yaml · CHARTER.md · validate.py · loader.py · registry.py · eval_checks.py
../../framework/  the one-page doctrine
```

The build skill (`create-langchain-agent`) lives at the **repo root** `.claude/skills/`,
alongside the framework-neutral `new-agent` interview, so it's discoverable wherever you open
Claude Code.

## Enforced walls + honest limits
Real walls: `model` (pinned via `init_chat_model`), `tools` (only bound tools exist),
`budget.steps` (`recursion_limit`), the wall-clock timeout (`asyncio.wait_for`), `egress`
(host-checked inside `fetch_url`), the filesystem jail (`jail_path`, for the fs tools), env-scoped
credentials, log redaction, retention pruning, the eval gate, and a run log.

- **Egress and the fs jail only cover tools this builder generates.** `fetch_url` and the fs tools
  are guarded; any third-party LangChain tool you add reaches the network / filesystem *outside*
  those guards. Same class of caveat as `Bash`/MCP in the other builders — a container is required
  to fence it. The fs jail is an application-level path check, not an OS sandbox.
- **No container, no fs/net jail.** The agent runs in-process on the host.
- **`budget.usd` is a client-side estimate**, checked after the run, not a metered hard wall.
  Verify against your actual bill.

## Deep Agents (opt-in, not the default)
LangChain's Deep Agents (`create_deep_agent`) is the batteries-included harness on top of this
one: planning, **sub-agents**, a **virtual filesystem**, bundled skills. Those features expand
authority and state, which is exactly what a contained agent wants to keep off by default — so
this builder defaults to the minimal harness and treats Deep Agents as a deliberate, re-confirmed
opt-in (recorded under `extensions`, declared, never a silent default). It is **not yet wired**
in this builder version; the minimal harness is the enforcement-tier default.

## vs. the Claude builders
Same charter, same honesty spine, a different runtime. The tool wall and egress are actually
tighter on LangGraph (no ambient tools; egress in-tool); `budget.usd` is looser (no native cap).
The doctrine (the *why*, the honest limits that hold across every runtime) is one page:
[`../../framework/README.md`](../../framework/README.md).
