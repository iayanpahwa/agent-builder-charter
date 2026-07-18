# Guide: build and run your first SDK agent

Plain, step by step. By the end you'll have an agent with a rulebook, a runnable `agent.py`,
and a log — that can only do what you allowed.

## The idea in one line
Every agent gets a small rulebook (its **charter**) that says exactly what it may do. This
builder embeds that rulebook directly in a single Python file, `agent.py`, built on the
**Claude Agent SDK** (Claude Code as a library) — so the limits are enforced in-process, not
just described.

## Before you start
- **Claude Code** (to run the interview).
- **Python 3** + `pip install claude-agent-sdk` (pinned in the generated `requirements.txt`).
- The **`claude` CLI** (Node) on your PATH, authenticated — the Python SDK shells out to it;
  unlike the TypeScript SDK, it does not bundle the binary.
- The auth env var for whichever mode you pick in the interview: `ANTHROPIC_API_KEY` (api-key
  mode) or `CLAUDE_CODE_OAUTH_TOKEN` (subscription mode).
- Open the **repo root** in Claude Code (the interview lives there); your agent lands in this
  builder.

## Step 1 — Make the agent (don't write the rulebook by hand)
In Claude Code (repo root), say: *"use the new-agent skill to make a &lt;thing&gt; agent."*
The framework-neutral interview asks the **name first**, then which **runtime** (pick
`claude-sdk`), then plain questions — what it does, what it can touch, how bad if it goes
wrong, who owns it, and which auth mode — captures a **brief**, and hands off to the
`create-claude-sdk-agent` generator, which writes a self-contained project under
`builders/claude-sdk/agents/<name>/`:

```
agents/<name>/
├── brief.yaml            # the neutral chart your answers produced
├── charter.yaml           # the rulebook
├── agent.py                # the artifact: embedded charter, enforced options, egress hook,
│                            # eval gate, logging — directly runnable, no wrapper script
├── prompts/
│   ├── system.md           # the agent's instructions
│   └── task.md             # the job it runs each time
├── evals/cases.yaml        # optional — the checks its output must pass
├── requirements.txt        # claude-agent-sdk pin
└── README.md
```

## Step 2 — Look at what it made
Open `agents/<name>/charter.yaml` and read it top to bottom: its name, owner, the exact tools
it may use (maybe none), how much it may spend, which sites it may reach, which auth mode it
uses, and the one number that says it's doing its job. Then skim `agent.py` — the same charter
is embedded near the top of the file as `CHARTER`. Editing it directly is exactly what you must
never do (see below).

## Step 3 — Check the rulebook is valid
```bash
python3 core/validate.py builders/claude-sdk/agents/<name>/charter.yaml
```
`VALID`, or it tells you what's missing. A broken rulebook means the agent won't start at
all — on purpose. A dead agent is safer than an ungoverned one.

## Step 4 — See what's really enforced (no tokens)
```bash
python3 builders/claude-sdk/agents/<name>/agent.py --dry-run
```
This prints an honest report per field: `block` (really enforced), `declared`, or `none` (and
`—` for a field you didn't set) — plus which auth mode is live and its billing/ToS note. Read
it — it tells you the truth (e.g. a dollar cap is a client-side estimate, not a metered wall; a
real sandbox needs a container).

## Step 5 — Run it
```bash
pip install -r builders/claude-sdk/agents/<name>/requirements.txt
./builders/claude-sdk/agents/<name>/agent.py manual        # or: python3 agent.py cron
```
This runs the agent **in-process on the Claude Agent SDK**, enforced by its embedded charter
(pinned model, only its tools, a hard turn limit, a wall-clock timeout, egress via a
`PreToolUse` hook). If it has evals, the output is checked against them: pass → the run is
**complete**; fail → **failed** (and it tells you which check failed). Either way it appends a
line to `agents/<name>/logs/runs.jsonl` (name · timestamp · trigger · outcome · cost) and saves
the full output.

The word after `agent.py` is just a **trigger label** written to the log (`manual`, `cron`,
`webhook` — whatever says *why* it ran).

### Watch it work, or keep a trace (optional)
If you turned on observability in the interview (or want it for one run), the agent takes flags:

```bash
./agents/<name>/agent.py manual --stream    # echo the loop live: assistant text + each tool call
./agents/<name>/agent.py cron   --trace     # write logs/<ts>.trace.jsonl, one redacted line per tool call
./agents/<name>/agent.py manual --quiet --no-trace   # override the charter default the other way
```

`--stream` is handy running by hand; leave it off for cron. `--trace` is for audit/debugging;
the run log always carries a `tool_calls` count either way. Both are redacted like the saved
output, and neither changes what the agent may do — they only observe the enforced loop.

## Step 6 — Run it on a schedule (cron / launchd / CI)
`agent.py` is just a command that resolves its own paths, so any scheduler works. Two things the
scheduler's environment must have that your interactive shell already does:

- **The auth env var** for the agent's mode — `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`.
  cron does *not* inherit your shell's environment, so set it in the job (or a wrapper that
  sources it — don't paste a secret into a world-readable crontab if you can avoid it).
- **The `claude` CLI on `PATH`** — the SDK shells out to it. cron/launchd start with a minimal
  `PATH`, so point at it explicitly.

To run every morning at 8:

```cron
0 8 * * *  ANTHROPIC_API_KEY=sk-ant-...  PATH=/path/to/claude/bin:/usr/bin:/bin  /abs/path/to/builders/claude-sdk/agents/<name>/agent.py cron >> /tmp/<name>.log 2>&1
```

Use the `cron` trigger label so scheduled runs are easy to spot in the log. The same line works
under launchd, a systemd timer, or a CI cron — they all just run the file.

## Changing an agent later
Don't hand-edit `agent.py` or its embedded charter. Re-run the `new-agent` interview (or the
`create-claude-sdk-agent` generator against the edited `brief.yaml`) — it regenerates the file
and bumps its version, so you always know what the agent was allowed to do, and when.

## The honest catch
A rulebook keeps an agent from doing too much. It does **not** make the agent smart, or its
answers correct. The evals gate catches format/citation mistakes cheaply; a cited *fact* still
needs a human (or the standalone `gen-evals` skill) to confirm.

---

Want a worked example? `example.agent.py` in this folder is a finished claude-sdk agent (a
docs.python.org researcher). Read it, then try Step 4 against it:
`python3 builders/claude-sdk/example.agent.py --dry-run`.
