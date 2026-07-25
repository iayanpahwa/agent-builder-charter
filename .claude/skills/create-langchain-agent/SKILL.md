---
name: create-langchain-agent
description: >
  Turn a framework-neutral agent brief into a CHARTER-governed agent on LangChain / LangGraph — a
  single self-contained, directly-runnable agent.py (embedded charter, pinned model, egress-guarded
  tools, eval gate, logging) on the LangGraph minimal harness. Invoked by the new-agent interview
  after the creator picks the langchain runtime (or run standalone against an existing
  agents/<id>/brief.yaml). ALWAYS builds against the latest LangChain/LangGraph docs.
---

# create-langchain-agent

You receive a framework-neutral **brief** (from the new-agent interview) and turn it into a
self-contained **LangChain / LangGraph** agent under `builders/langchain/agents/<id>/`. The
headline artifact is a single **`agent.py`** — directly runnable (`./agent.py manual`, or
`python3 agent.py cron` from cron), no wrapper script. It embeds its own charter, pins the model,
builds egress-guarded tools, enforces the walls, gates evals, and logs.

## ALWAYS build against the latest docs (do not trust memory)
The LangChain/LangGraph API drifts hard — the agent harness has moved (`AgentExecutor` deprecated →
`langgraph.prebuilt.create_react_agent` → LangChain 1.0 `langchain.agents.create_agent`), and
`recursion_limit` / message / usage shapes change. BEFORE generating, WebFetch and confirm the
current API, then record the version/date you targeted in the `agent.py` header. The pages:
- https://reference.langchain.com/python/langgraph.prebuilt/chat_agent_executor/create_react_agent —
  `create_react_agent(model, tools, prompt=...)`, invoke with `config={"recursion_limit": N}`,
  result `["messages"][-1].content`
- https://docs.langchain.com/oss/python/langchain/models — `init_chat_model("<provider>:<id>", ...)`
- https://docs.langchain.com/oss/python/deepagents/overview — Deep Agents (the opt-in, below)

If an import or signature changed (e.g. `create_agent` is now the recommended minimal harness),
adapt the generated `agent.py` and say plainly that you did. Study
`builders/langchain/example.agent.py` directly — it is the reference artifact.

## First: write the brief to disk
Create `builders/langchain/agents/<id>/` and write the brief there as `brief.yaml` (the durable
chart + regeneration input). If run standalone, read an existing `agents/<id>/brief.yaml` instead
of re-interviewing.

**Before generating: check `data_source` in the brief.** If it says `verified: no`, or the field
is missing on an agent that depends on an external source, stop and fetch that source once by
hand now. A charter describing an agent that cannot get its data is waste, and the fetch takes a
minute. If the source is genuinely unreachable, say so and re-plan the purpose with the creator
rather than generating around it.

## Ask the provider (safety + billing — confirm aloud)
The one genuinely LangChain-specific interview question. LangChain is multi-provider; this builder
ships **Anthropic** wired, with the `model.provider` seam in place for others.
- **anthropic** (default) → key `ANTHROPIC_API_KEY`, billed to your Anthropic API account per
  token. Express as `credentials: [{name: api-auth, ref: env:ANTHROPIC_API_KEY, scope: "anthropic api"}]`
  and `model.provider: anthropic`.
- **another provider** (openai, ...) → only choose this if the creator asks; say honestly it is
  not wired yet in this builder version and offer to add it (needs the provider's `langchain-*`
  package, its auth env var, and a price-table entry). Do NOT silently emit an unwired provider.

State the honest guard: the agent runs under a **scoped process environment** that keeps only the
declared `env:` credential(s) plus OS essentials and **drops every other host secret** (any other
API key or token) — so a poisoned page can't exfiltrate a credential the charter never granted.

## Re-confirm the concretized safety values (concretizing is where safety hides)
The brief holds neutral intents; you concretize and RE-CONFIRM each aloud before writing:
- **model tier** → exact pinned id (cheap → `claude-haiku-4-5`; a stronger model only for genuinely
  hard reasoning — say why). Say the id; get a yes. → `model.id`, used in
  `init_chat_model("{provider}:{id}", **params)`.
- **capabilities** → this runtime's OWN tool verbs (see the catalog below), NOT Claude Code tool
  names. Select verbs from the catalog based on what the agent needs; propose read-only tools, and
  confirm the WRITE tool (`write_file`) aloud. Text-only → `tools: []` (the safest kind — LangGraph
  binds nothing, so nothing is callable). Do not name a verb outside the catalog — the agent fails
  closed on an unknown verb. Destructive capabilities beyond a path-jailed `write_file` are
  contained by being LEFT OUT (there is no `bash` — an unattended agent can't prompt a human, and
  an arbitrary shell defeats every wall).
- **network policy** → exact `egress` list (specific hosts / `[any]` / `[none]`; never empty). The
  wall lives INSIDE `fetch_url`: it host-checks each URL before requesting. `[any]` is open (say
  plainly that's not a wall); `[none]` refuses every host. If `tools: []`, egress is a non-issue —
  still record it honestly.
- **spend ceiling** → `budget.usd` (a CLIENT-SIDE estimate — see below), plus `budget.steps` and
  `budget.wall_clock_seconds`. Confirm the numbers.

## The tool catalog (select from this; never hand-write a guard)
The builder ships a **vetted catalog** of guarded tool factories in `example.agent.py`. You SELECT
verbs from it based on the interview and set the scope; you do NOT write a tool's guard from
scratch (a path-jail or host-check written per agent is where traversal/symlink bugs creep in, and
it can't be pre-tested or honestly reported). Each factory is copied into the generated `agent.py`,
so the file stays self-contained.

| verb | class | scope source | what it does |
|------|-------|--------------|--------------|
| `fetch_url` | read (net) | `egress` | HTTP GET, host-checked before the request |
| `read_file` | read (fs) | `sandbox.filesystem` | read a UTF-8 text file inside the jail root |
| `list_dir` | read (fs) | `sandbox.filesystem` | list a directory inside the jail root |
| `grep` | read (fs) | `sandbox.filesystem` | regex-search text files under the jail root (bounded) |
| `write_file` | **write (fs)** | `sandbox.filesystem` | write a UTF-8 file inside the jail root (creates dirs) |

Rules for selecting:
- **Any fs verb (`read_file`/`list_dir`/`grep`/`write_file`) REQUIRES `sandbox.filesystem`** — the
  jail root, e.g. `./out` (resolved relative to the agent dir). If the creator wants an fs tool,
  confirm the root aloud and set it; the agent fails closed at build if it's missing.
- **`write_file` is the one write capability — confirm it aloud** (which verb, which root). Read
  tools you may propose from the requirement; the write tool is a safety field.
- **No `bash`, no arbitrary-exec, no `http_post`** in the catalog. If a job seems to need a shell,
  that's the honest "you need a container" conversation, not a catalog entry.
- To EXTEND the catalog (a genuinely new capability): add a guarded factory + one `_NET_TOOLS`/
  `_FS_TOOLS` row to `example.agent.py` and a unit test for its guard — then it's selectable. A
  bespoke, non-safety-critical transform may live inside a guarded shell, but the I/O boundary
  (host, path) always comes from a catalog primitive.

Note this closes the loop on "save to a local file": with `write_file` granted, the AGENT writes
its own output inside the jail; without it, the runner still persists the final text to `logs/`.

## Critical: how LangGraph actually enforces (verify each build)
The non-obvious mechanics that MUST be preserved, and why they differ from the Claude builders:
- **Tools are a stronger wall here.** LangGraph has no ambient tool registry. The only tools that
  exist are the ones `build_tools` binds from the charter — so there is nothing to "disallow" and
  no `disallowed_tools` list. A text-only agent literally cannot call anything.
- **Egress is enforced inside the tool, not by a hook.** `make_fetch_url(egress)` returns a tool
  that runs `fetch_egress_check(url, egress)` and refuses before any request. Preserve this — it is
  THE egress wall. Honest caveat to carry into the report: only tools this builder generates are
  egress-checked; any third-party LangChain tool reaches the network OUTSIDE the guard (a container
  is required to fence it — the same class of caveat as Bash/MCP elsewhere).
- **Filesystem tools are jailed inside the tool by `jail_path`.** Each fs factory resolves the
  path with `realpath` and refuses anything that leaves `sandbox.filesystem` (`..`, absolute,
  symlink escape). This is THE fs wall — the one primitive, reused, unit-tested. Same honest
  caveat: it's an APPLICATION-level jail across the tools this builder ships, not an OS sandbox;
  process-wide fs containment still needs a container.
- **The harness is version-aware.** LangChain 1.0 moved the minimal harness to
  `langchain.agents.create_agent` (arg `system_prompt`); older LangGraph has
  `langgraph.prebuilt.create_react_agent` (arg `prompt`, deprecated since V1). `run()` prefers the
  new import and falls back, so both lines work. Keep this — do not pin to the deprecated one.
- **`budget.steps` maps to `recursion_limit`.** A ReAct loop spends ~2 graph super-steps per
  reason→act turn, so `recursion_limit ≈ 2 * steps + 1`. It is a real wall (raises
  `GraphRecursionError`); the run is marked `killed`. State the mapping in the report so the number
  isn't surprising.
- **`budget.usd` is SOFT.** LangGraph has no native spend cap. The agent sums `usage_metadata`
  across the returned messages and multiplies by a local price table AFTER the run. It can report a
  breach; it cannot hard-stop a call mid-flight. The report says `declared`, not `block` — do not
  overclaim it as a wall. Keep the price table honest (a comment that it must be verified).
- **`budget.wall_clock_seconds`** → `asyncio.wait_for` around `agent.ainvoke(...)`; a real wall
  (`killed` on timeout).

## Map the rest of the brief to the charter
`owner`; `data` (class / redact / retention); `sandbox` (`none` for text-only/read-only work — a
real fs/net jail still needs a container, since LangGraph runs in-process on the host);
`context.trusted_sources` (the instruction files, concatenated into the system prompt passed to the
harness; fetched pages/memory stay untrusted); `evals`;
`extensions.human_verification` only if a human must verify. Set `runtime: langchain`; `charter:`
= the schema's `const` (read `core/charter.schema.yaml`); `status: enabled`; `version: "1"` (bump
on change).

## Deep Agents is an opt-in extension, NOT the default
LangChain's Deep Agents (`create_deep_agent`) bundles planning, **sub-agents**, a **virtual
filesystem**, and skills. Those expand authority (spawning agents) and state (a persistent fs),
which a contained agent wants OFF by default — the same reason the SDK generator does not
auto-offer subagents/sessions. So:
- Default every agent to the **minimal harness** (`create_react_agent`).
- Offer Deep Agents only if the creator explicitly asks, warn plainly that it expands
  authority/state, and record it under `extensions.deep_agents` (declared-only, never enforced).
- This builder version does **not** wire `create_deep_agent`. If opted in, say honestly it is
  recorded but not yet implemented, and build the minimal-harness agent.

## Generate the agent project
```
builders/langchain/agents/<id>/
├── brief.yaml         # the neutral chart (written first)
├── charter.yaml       # generated, then validated (audit + regen source)
├── agent.py           # THE ARTIFACT: self-contained, executable (chmod +x); embeds the charter
├── prompts/
│   ├── system.md      # REAL instructions (role, never-do, format, a good/bad example)
│   └── task.md        # the default task it runs each time
├── evals/cases.yaml   # optional — runnable invariants
├── requirements.txt   # langgraph + langchain + langchain-anthropic (pinned vs live docs)
├── requirements.lock  # hash-pinned, generated from it (below)
├── .venv/             # written by core/provision.py (gitignored, per-machine)
├── run                # written by core/provision.py (gitignored, per-machine) — the entry point
└── logs/              # created on first run (gitignored)
```

You write everything above `.venv/`; **`core/provision.py` writes `.venv/` and `run`** in step 4
of *Validate and finish*. Do not hand-write a launcher — a hand-written one resolves `python3`
off the caller's PATH and needs a venv activated first, which is exactly what breaks under cron.

Generate `agent.py` in the exact shape of `builders/langchain/example.agent.py` — study that file
directly, it is the reference artifact:
- the module header docstring, rewritten for THIS agent: the first line reads `<id> — a
  CHARTER-governed agent ...`, and the `Run:` line references `agent.py` (this agent's filename),
  NOT `example.agent.py`. Rewrite every `example.agent.py` occurrence in the header to `agent.py`,
  and set the `argparse` description to `<id>`. Leave no `example.agent.py` or the reference name
  behind.
- embedded `CHARTER` dict, the `_PRICE_PER_MTOK` table
- the pure helpers: `scoped_env`, `_host_allowed` + `fetch_egress_check`, `resolve_root` +
  `jail_path` (the fs wall), `estimate_cost`, `redact`, the inlined eval_checks (`check_one`/
  `check_all`, copied verbatim from `core/eval_checks.py` — a self-contained agent can't import
  `core/`), run-log helpers, `_read_trusted_sources`, `_text_of`, `enforcement_report`
- the catalog factories `make_fetch_url` / `make_read_file` / `make_list_dir` / `make_grep` /
  `make_write_file` with the `_NET_TOOLS` + `_FS_TOOLS` tables, and `build_tools(charter, egress,
  fs_root)` that dispatches from them and fails closed (lazy langchain import inside each factory)
- the async `run()`: lazy version-aware harness import that fails closed with a pip hint, model
  pinned via `init_chat_model`, `fs_root` resolved from `sandbox.filesystem`, tools from the
  charter, the `recursion_limit` + `asyncio.wait_for` walls, usage-based cost estimate, eval gate on
  the RAW result before redaction, jsonl run-log + retention prune + collision-safe output save
- `main()` with `--dry-run` that prints the report WITHOUT importing langchain

The charter is embedded (the `.py` runs standalone) AND written as a sibling `charter.yaml` (audit +
regeneration) — write `charter.yaml` in the field order of
`builders/langchain/examples/langchain-docs-researcher.charter.yaml`.

### cases.yaml (if evals opted in)
Same invariant vocabulary as the other builders: `contains` / `not_contains` / `matches` /
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
The generated `agent.py` has to satisfy the repo's tooling, same as any file here: run
`black builders/langchain/agents/<id>/agent.py` and then `ruff check` on it, and fix what they
report, before step 2. `example.agent.py` is formatted at 100 columns (`pyproject.toml`), so a
transcribed copy that drifts will fail CI — and the diff noise makes a generated agent hard to
compare against the reference it came from.

1. `python3 core/validate.py builders/langchain/agents/<id>/charter.yaml` (from the REPO ROOT) →
   loop until `VALID`.
2. Read the SAFETY fields back in plain English (provider + which env var is kept and which
   dropped; model + why; budget incl. the steps→recursion_limit mapping; tools; egress).
3. Dry-run (no langchain import, no tokens):
   `python3 builders/langchain/agents/<id>/agent.py --dry-run` → walk the honest
   block/declared/none report, including the soft `budget.usd` and the third-party-tool egress
   caveat.
4. **Lock the dependencies.** `requirements.txt` carries version floors, so the same charter
   would resolve to a different agent six months from now. Turn it into an exact, hash-pinned
   lock — `--universal` keeps one lock valid on both macOS and Linux:
   ```
   uv pip compile --universal --generate-hashes --no-header \
     builders/langchain/agents/<id>/requirements.txt \
     -o builders/langchain/agents/<id>/requirements.lock
   ```
   Commit both: the `.txt` records intent, the `.lock` is what actually gets installed.
   If `uv` is not on PATH, say so plainly and skip this — provisioning falls back to the floors
   and the agent still runs, it is just not reproducible.
5. **Offer to provision it**, and run it for them if they agree — this is the only setup step,
   it is idempotent, and without it there is nothing to run:
   `python3 core/provision.py builders/langchain/agents/<id>`
   It builds `agents/<id>/.venv` from the pinned deps and writes `agents/<id>/run`. Nothing is
   installed outside the agent's own directory, and the run path never reaches a package index
   again — a runner that installed at invocation time would be an undeclared egress path, which
   is the thing a charter exists to prevent. Say what it did in one line; don't paste its output.
6. Offer to run it once — `./builders/langchain/agents/<id>/run` — needs `ANTHROPIC_API_KEY` set.
   For deeper repeatable evals, mention the standalone gen-evals skill. A human still confirms
   any factual claim.
7. To change anything, re-run the new-agent interview (or re-run this generator against the edited
   `brief.yaml`) — never hand-edit `charter.yaml` or `agent.py`.

## Hand it over
Close with exactly this, `<id>` filled in. It is the last thing the creator reads, so it has to
stand on its own — do not compress it into prose or drop the log paths.

```
Your agent is ready: <id>

  Run it
      ./builders/langchain/agents/<id>/run
      ./builders/langchain/agents/<id>/run --dry-run   # what's enforced; no tokens spent

  Read what it produced          (logs/ appears after the first real run)
      builders/langchain/agents/<id>/logs/runs.jsonl
          one line per run: outcome, cost, duration, eval pass/fail, output file
      builders/langchain/agents/<id>/logs/<timestamp>.output.txt
          the agent's full response, after redaction
      builders/langchain/agents/<id>/logs/<timestamp>.trace.jsonl
          per-tool-call trace, when extensions.observability.trace is on

  Latest output, any time
      ls builders/langchain/agents/<id>/logs/*.output.txt | sort | tail -1 | xargs cat

  Change anything
      re-run /new-agent — never hand-edit charter.yaml or agent.py
```

If they declined provisioning, keep the block but replace the first command with
`python3 core/provision.py builders/langchain/agents/<id>`, and say plainly that `run` does not
exist until they do that.
