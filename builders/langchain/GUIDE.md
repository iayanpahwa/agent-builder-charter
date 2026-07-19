# Guide: build and run your first LangChain agent

Plain, step by step. By the end you'll have an agent with a rulebook, a runnable `agent.py`, and
a log — that can only do what you allowed.

## The idea in one line
Every agent gets a small rulebook (its **charter**) that says exactly what it may do. This builder
embeds that rulebook directly in a single Python file, `agent.py`, built on the **LangGraph**
minimal agent harness — so the limits are enforced in-process, not just described.

## Before you start
- **Claude Code** (to run the interview).
- **Python 3** + `pip install -r requirements.txt` (`langgraph`, `langchain`,
  `langchain-anthropic`; pinned in the generated `requirements.txt`).
- **`ANTHROPIC_API_KEY`** in your environment — the provider key the charter declares. It bills
  your Anthropic API account per token.
- Open the **repo root** in Claude Code (the interview lives there); your agent lands in this
  builder.

## Step 1 — run the interview
At the repo root, say: *"Help me build a docs researcher agent."* The **new-agent** interview asks
the name first, then the runtime — pick **`langchain`** — then plain questions. It never silently
decides a safety field (model, budget, capabilities, network); it proposes a default, says it out
loud, and waits for your yes. It captures a **brief** and hands off to the **create-langchain-agent**
generator.

## Step 2 — the generator re-confirms the safety values
The brief holds neutral intents; the generator concretizes them and reads each back:
- **model** → an exact pinned id (default `claude-haiku-4-5`; a stronger model only for genuinely
  hard reasoning, and it says why).
- **tools** → this runtime's own verbs, not Claude Code tool names. "Read web pages" → `fetch_url`
  (egress-guarded). Text-only → `tools: []`, the safest kind.
- **egress** → the exact host allow-list `fetch_url` is checked against; `[any]` (open, not a
  wall) or `[none]` (no network).
- **budget** → the spend/step/time ceilings, including how `budget.steps` maps to LangGraph's
  `recursion_limit` (about twice the steps, for a read-then-answer loop).

Then it writes `agents/<id>/`: `brief.yaml`, `charter.yaml`, `agent.py` (the artifact),
`prompts/`, optional `evals/`, and `requirements.txt`.

## Step 3 — validate the charter
```bash
python3 ../../core/validate.py agents/<id>/charter.yaml     # loop until VALID
```
A missing or malformed charter means the agent refuses to start. Better a dead agent than an
ungoverned one.

## Step 4 — read the honest report (no tokens)
```bash
python3 agents/<id>/agent.py --dry-run
```
Walk each field: `block` (a real wall), `declared` (recorded, not enforced), `none` (this runtime
can't enforce it), `—` (you didn't set it). This report is the truth — trust it over any prose.
For this builder, note the honest lines: `budget.usd` is a client-side estimate, and any
third-party tool you add reaches the network outside the `fetch_url` egress guard.

## Step 5 — run it
```bash
pip install -r agents/<id>/requirements.txt
export ANTHROPIC_API_KEY=sk-...
./agents/<id>/agent.py manual
```
It pins the model, binds only your tools, runs under the step + wall-clock ceilings, gates on your
evals if any, marks the run complete/failed/killed, and appends a line to `agents/<id>/logs/runs.jsonl`.
A few cents. A human still confirms any factual claim in the output.

## Step 6 — change something
Never hand-edit `charter.yaml` or `agent.py`. Re-run the interview, or re-run the generator against
the edited `brief.yaml` — it bumps the version, so every run stays tied to the exact rules it ran
under.

## The one honest limit
A charter bounds what an agent *can do*, not whether it does it *well*. "Has a charter" means
contained and owned — not correct. Quality comes from the evals and, where it matters, a human.
