# claude-headless builder

One builder in the CHARTER repo. It turns a charter into an agent that runs **headless**
(`claude -p`) — the tier where the charter is really enforced. The shared engine (schema,
validators, eval checks) lives in [`../../core/`](../../core/); the doctrine (the *why*) in
[`../../framework/`](../../framework/). This folder only adds the headless runner + the interview.

A charter lists the only things an agent may do. The runner reads it and is the only door:
model, tools, turns, timeout, network, environment all come from the charter. **If it's not in
the file, the agent can't do it.**

## Quickstart — build your own agent
You need **Claude Code** (to run the interview), a **terminal**, and **Python 3 + PyYAML**.

1. Open **this folder** (`builders/claude-headless/`) in **Claude Code**, terminal beside it.
2. Say what you want, e.g. *"Help me build a release-notes summarizer agent."*
   Claude Code reads `CLAUDE.md` and runs the **new-agent** interview — it asks the **name
   first**, then plain questions (never silently deciding model / budget / tools / egress), and
   offers a **plug-in round** (extra `.md`, MCP servers, skills).
3. It generates a self-contained project at `agents/<id>/`: the charter, `prompts/`, optional
   `evals/`, and **`run.sh`** — the artifact. It validates the charter for you.
4. See what's really enforced (no tokens):
   `python3 run_headless.py --charter agents/<id>/charter.yaml --dry-run`
5. Run it: `./agents/<id>/run.sh manual`. It runs the agent headless (enforced), gates on its
   evals, marks the run complete/failed, and logs it.

Want to see the ideas first? `python3 ../../core/loader.py` (enforcement) and
`python3 ../../core/registry.py` (the fleet board / off-switch).

## New here? → read `GUIDE.md`

## What's here
```
builders/claude-headless/           # open THIS in Claude Code
├── CLAUDE.md                       # what Claude Code auto-reads to drive the interview
├── README.md · GUIDE.md            # the map · the step-by-step
├── run_headless.py                 # THE RUNNER — charter -> enforced `claude -p` + eval gate + logging
├── cc_guard.py                     # the egress hook the runner passes via --settings
├── examples/repo-researcher.charter.yaml   # a headless example agent
├── agents/                         # YOUR agents (generated; empty on a fresh clone)
└── .claude/skills/{new-agent, run-evals}    # COMMITTED — you get these on clone

shared, reused by every builder (one level up):
../../core/       charter.schema.yaml · CHARTER.md · validate.py · loader.py · registry.py · eval_checks.py
../../framework/  the one-page doctrine
```

## How it fits together
```
1. AUTHOR    the new-agent skill interviews you (name first) and writes the agent project (never by hand)
2. VALIDATE  ../../core/validate.py checks the charter (fail closed)
3. REPORT    run_headless.py --dry-run prints, honestly, what each field really enforces
4. RUN       run.sh -> run_headless.py IS the door: model / tools / turns / timeout / egress enforced
5. GATE      if evals opted in, the output is checked against invariants -> complete / failed
6. LOG       every run appends to agents/<id>/logs/runs.jsonl (name · timestamp · trigger · outcome · cost)
```

## Decided / shipped (v0.2)
- Charters are executable and generated, never hand-written; **headless `claude -p` is the enforcement tier.**
- Real headless walls: `model`, `tools` (+ dangerous tools denied), `budget.steps` (`--max-turns`),
  the wall-clock timeout, and `egress` (via the `--settings` hook when a specific list is set).
- `tools: []` is valid — a text-only agent is the safest kind.
- Honest limits: `--max-budget-usd` may be a no-op under subscription auth; a real fs/net **sandbox**
  and true **credential isolation** need a container.
- `runtime` field; `egress: [any]` sentinel (empty egress invalid); `extensions.human_verification`.
- Evals are optional and cheap: deterministic invariants gate the run; no pytest, no judge model.
- **Plug-ins:** an agent can declare `mcp` servers / custom tools and `skills`, loaded *strict*
  (only yours) with their capabilities added to the allow-list and surfaced in the report; extra
  `.md` instruction files go in `context.trusted_sources`.

## Open / roadmap
- Approval as a per-tool property; an aggregate/time-window budget cap for 24/7 agents;
  `credentials.scope` checked against the real grant; a container profile for real sandbox/egress.
- Sibling builders (`../claude-sdk/`, `../openai-agents/`, `../langchain/`) — placeholders today.

## Doctrine
The values and honest limits are in the one-page doctrine:
[`../../framework/README.md`](../../framework/README.md).
