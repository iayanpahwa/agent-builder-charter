# Guide: build and run your first agent

Plain, step by step. By the end you'll have an agent with a rulebook, a runnable command,
and a log — that can only do what you allowed.

## The idea in one line
Every agent gets a small rulebook (its **charter**) that says exactly what it may do. The
runner reads that rulebook and blocks anything not on it. The agent runs **headless**
(`claude -p`), so those limits are real.

## Before you start
- **Claude Code** (to run the interview) + the **`claude` CLI** on your PATH (to run the agent).
- **Python 3** + the deps: `pip install -r ../../requirements.txt` (PyYAML + jsonschema).
- Open the **repo root** in Claude Code (the interview lives there); your agent lands in this builder.

## Step 1 — Make the agent (don't write the rulebook by hand)
In Claude Code (repo root), say: *"use the new-agent skill to make a &lt;thing&gt; agent."*
The framework-neutral interview asks the **name first**, then which **runtime** (pick
`headless`), then plain questions — what it does, what it can touch, how bad if it goes wrong,
who owns it — captures a **brief**, and hands off to the `create-headless-agent` generator,
which writes a self-contained project under `builders/claude-headless/agents/<name>/`:

```
agents/<name>/
├── brief.yaml         # the neutral chart your answers produced
├── charter.yaml         # the rulebook
├── prompts/
│   ├── system.md        # the agent's instructions
│   └── task.md          # the job it runs each time
├── evals/cases.yaml     # optional — the checks its output must pass
├── run.sh               # the artifact: the command you run
└── README.md
```

## Step 2 — Look at what it made
Open `agents/<name>/charter.yaml` and read it top to bottom: its name, owner, the exact
tools it may use (maybe none), how much it may spend, which sites it may reach, and the one
number that says it's doing its job.

## Step 3 — Check the rulebook is valid
```bash
python3 core/validate.py builders/claude-headless/agents/<name>/charter.yaml
```
`VALID`, or it tells you what's missing. A broken rulebook means the agent won't start at
all — on purpose. A dead agent is safer than an ungoverned one.

## Step 4 — See what's really enforced (no tokens)
```bash
python3 builders/claude-headless/run_headless.py --charter builders/claude-headless/agents/<name>/charter.yaml --dry-run
```
This prints the exact `claude -p` command it will run, and an honest report per field:
`block` (really enforced), `declared`, or `none` (and `—` for a field you didn't set). Read it —
it tells you the truth (e.g. a dollar cap may not bite under a subscription; a real sandbox
needs a container).

## Step 5 — Run it
```bash
./builders/claude-headless/agents/<name>/run.sh manual        # or: cron / webhook / whatever triggered it
```
This runs the agent **headless**, enforced by its charter (pinned model, only its tools,
a hard turn limit, a wall-clock timeout). If it has evals, the output is checked against
them: pass → the run is **complete**; fail → **failed** (and it tells you which check failed).
Either way it appends a line to `agents/<name>/logs/runs.jsonl` (name · timestamp · trigger ·
outcome · cost) and saves the full output.

## Step 6 — Concept demos (optional)
```bash
python3 core/loader.py       # watch the guard pause / budget-kill / tool-deny an agent
python3 core/registry.py     # the fleet board: pause every pii agent in one query
```

## Changing an agent later
Don't hand-edit the rulebook. Re-run the `new-agent` interview (or the `create-headless-agent`
generator against the edited `brief.yaml`) — it updates the file and bumps its version, so you
always know what the agent was allowed to do, and when.

## The one rule to remember
You can leave things out of a rulebook if you don't need them. But **never let a rulebook
claim a limit that nothing actually checks.** An honest blank beats a fake promise, because
someone will trust the promise.

## The honest catch
A rulebook keeps an agent from doing too much. It does **not** make the agent smart, or its
answers correct. The evals gate catches format/citation mistakes cheaply; a cited *fact* still
needs a human (or the standalone `gen-evals` skill) to confirm.

---

Want a worked example? `examples/repo-researcher.charter.yaml` is a finished headless
charter (a read-only code/docs researcher). Read it, then try Step 4 against it.
