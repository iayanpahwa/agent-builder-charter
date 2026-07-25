---
name: run-evals
description: >
  Run or re-run a headless agent's eval cases against its output, or check a sample output
  ad-hoc without a live run. For headless agents the eval gate is built into the agent's
  `run` shim (`--eval`); this skill covers running it, interpreting the log, checking a pasted output
  cheaply, and escalating to gen-evals for repeatable independent evals. Use for "run/check
  evals for <agent>", "did it pass its evals", or after changing a prompt/model.
---

# run-evals

For a **headless** agent the eval gate lives in the run itself: `./run` (which calls
`run_headless.py --eval`) runs the agent, checks its output against `agents/<id>/evals/cases.yaml`
deterministic invariants, marks the run **complete** or **failed**, and logs it. This skill
helps you run that, read the result, or check output without spending tokens.

## Run / re-run the gate (a few cheap tokens)
```bash
./agents/<id>/run manual
```
Then read `agents/<id>/logs/runs.jsonl` — the latest entry's `outcome` is `complete` (all
invariants passed) or `failed` (the entry lists which invariant failed and why). Re-run after
changing the prompt or model.

## Check a sample output WITHOUT a live run (free)
If they'd rather not spend tokens, take a pasted/sample output and check it against the cases
yourself using the same code the gate uses — `../../core/eval_checks.py`. For each
case, run a tiny inline `python3` calling `eval_checks.check_all(invariants, output)` and
report pass/fail with the offending line. Identical logic to the live gate.

## The invariant vocabulary (`cases.yaml`)
`contains` / `not_contains` / `matches` / `not_matches` / `contains_url` / `valid_json` /
`claims_cited: [keywords]` — every line mentioning a keyword must carry a URL (catches an
unsourced figure, the classic failure).

## Honesty (do not soften)
Invariants are real — they're code. They prove an output is *cited / formatted*, not that a
cited number is *true*; only a human clicking the source (or the case's `human_check`) does
that. For quality a deterministic check can't judge, use a human — or the standalone
**gen-evals** skill (separate project: independent judge model, baselines, regression gating;
heavier, more tokens). Use gen-evals for high-stakes agents; use this for cheap everyday checks.
