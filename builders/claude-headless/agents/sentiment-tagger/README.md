# sentiment-tagger

A tiny demo agent: give it text, it replies `POSITIVE` / `NEGATIVE` / `NEUTRAL`.
Text-only (no tools), runs headless via `claude -p`, enforced by its charter.

## Run it
```bash
cd builders/claude-headless/agents/sentiment-tagger
./run.sh manual
```
This runs the agent headless (model, turns, and timeout enforced from `charter.yaml`),
then checks its output against `evals/cases.yaml`. If the invariant passes, the run is
logged as complete; otherwise failed. Every run appends to `logs/runs.jsonl`
(name · timestamp · trigger · outcome · cost · invariants) and saves the full output.

## What's really enforced (headless)
`model`, `tools` (none, so it truly can't act), `budget.steps` (`--max-turns`), the
wall-clock timeout, `egress: [none]` (no network — WebFetch/WebSearch denied), and
`data.retention_days` (old logs and saved output pruned when it runs) are hard walls.
Change the task by editing `prompts/task.md`, or pass a different `--prompt`. To change the
rules, re-run the `new-agent` interview (or the `create-headless-agent` generator against
`brief.yaml`); never hand-edit `charter.yaml`.
