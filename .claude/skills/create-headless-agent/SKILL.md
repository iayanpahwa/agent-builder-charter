---
name: create-headless-agent
description: >
  Turn a framework-neutral agent brief into a CHARTER-governed HEADLESS agent project — a
  self-contained dir whose artifact is a runnable run.sh (`claude -p`), plus its charter,
  prompts, optional evals, and logging. Invoked by the new-agent interview after the creator
  picks the headless runtime (or run standalone against an existing agents/<id>/brief.yaml).
---

# create-headless-agent

You receive a framework-neutral **brief** (from the new-agent interview) and turn it into a
self-contained **headless agent project** under `builders/claude-headless/agents/<id>/`. The
headline artifact is `run.sh` — a terminal command that runs the agent via `claude -p`,
enforced by its charter, gated by its evals, and logged.

## First: write the brief to disk
Create `builders/claude-headless/agents/<id>/` and write the brief there as `brief.yaml` (the
durable chart + regeneration input). If run standalone, read an existing
`agents/<id>/brief.yaml` instead of re-interviewing.

## The one rule (concretizing is where safety hides)
The brief holds NEUTRAL intents; turning them into headless specifics can silently decide a
safety field, so **re-confirm each concretized SAFETY value aloud with the human before
writing:**
- model tier → exact pinned id (cheap → `claude-haiku-4-5`; a stronger model only for
  genuinely hard reasoning). Say the id; get yes.
- capabilities → exact **Claude Code tool names** (`WebSearch`, `WebFetch`, `Read`, `Grep`,
  `Glob`, `Write`, `Edit`, `Task`, MCP tools). "read web pages" → `WebFetch` (+ `WebSearch` for
  search). Text-only → `tools: []` (the safest kind — don't invent a tool it doesn't need).
  Confirm the exact list. Destructive tools are LEFT OUT (unattended headless can't prompt a
  human).
- network policy → exact `egress` list (specific hosts / `[any]` / `[none]`; never empty).
  Confirm.
- **cross-check `WebSearch` against egress:** a scoped `egress` host list denies `WebSearch`
  outright (a search can't be confined to specific hosts), so `WebSearch` only actually fires
  under `egress: [any]`. If the brief wants both search and a scoped allow-list, surface the
  conflict to the human and resolve it — drop `WebSearch`, or switch to `egress: [any]` (and say
  plainly that's not a wall). Never ship a charter granting a tool the runtime will silently deny.
- spend ceiling → `budget.usd` (confirm the number) + `tokens` / `steps` /
  `wall_clock_seconds`.

## Map the rest of the brief to the charter
`owner`, `data` (class / redact / retention), `credentials` (`ref: env:VAR` is passed through
from the host env; `ref: vault://...` is declared but not resolved by this builder — it needs
a resolver), `evals`, `sandbox` (`none` for text-only/read-only; `container` otherwise — a real
fs/net jail needs a container even headless), `context.trusted_sources` (the instruction
files), and `extensions.human_verification` (only if Q10 said a human must verify). Set
`runtime: headless`; `charter:` = the schema's `const` (read `core/charter.schema.yaml`);
`status: enabled`; `version: "1"` (or bump on a change).

## Generate the agent project

```
builders/claude-headless/agents/<id>/
├── brief.yaml         # the neutral chart (written first)
├── charter.yaml       # generated, then validated
├── prompts/
│   ├── system.md      # REAL instructions (role, how-to-work, never-do, format, a good/bad example)
│   └── task.md        # the default task it runs each time
├── evals/cases.yaml   # optional — runnable invariants
├── run.sh             # THE ARTIFACT (below); chmod +x it
└── README.md          # what it is, how to run, what's really enforced
```

`run.sh` calls the shared headless loader — do not inline the flags (they live in one place so
every agent stays current):
```bash
#!/bin/bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$here/../../run_headless.py" \
  --charter "$here/charter.yaml" --trigger "${1:-manual}" --eval
```
Include `--eval` only if the creator opted into evals; write `charter.yaml` in the field order
of `builders/claude-headless/examples/repo-researcher.charter.yaml`.

### cases.yaml (if evals opted in) — runnable, so run_headless can gate on it
Turn Q10 answers into deterministic invariants (vocabulary: `contains` / `not_contains` /
`matches` / `not_matches` / `contains_url` / `valid_json` / `claims_cited: [...]`):
```yaml
success_metric: "each item cites a real source URL"
slo: "90%"
cases:
  - id: weekly-news
    input: "Give me this week's release news."
    invariants:
      - contains_url: true
      - claims_cited: [funding, raised, "$", Series, acquired, million]
    human_check: "click each cited source; confirm any figure/date is real"
```

## Plug-ins the runner wires automatically
The runner (`run_headless.py`) wires these automatically: `mcp` → `--mcp-config` +
`--strict-mcp-config` (only your servers) with their tools added to the allow-list; `skills`
→ `settings.availableSkills`; extra `.md` → `--append-system-prompt-file`.

## Keep the headless command CURRENT (don't let it rot)
The `claude -p` flags live in `run_headless.py`, and **they drift**. Before
you finalize, **fetch the current CLI docs and confirm the flags still match** — do not trust
a memorized set:
- CLI reference: https://code.claude.com/docs/en/cli-reference.md
- Headless guide: https://code.claude.com/docs/en/headless.md

If a flag was renamed or removed, update `run_headless.py` and say you did. The command lives
in one file on purpose: fix it once, every agent stays current.

## Validate and finish
1. `python3 core/validate.py builders/claude-headless/agents/<id>/charter.yaml` → loop until
   `VALID`.
2. Read the SAFETY fields back in plain English (model + why, budget, tools, egress).
3. Dry-run (prints the command + the honest enforcement report, no tokens):
   `python3 builders/claude-headless/run_headless.py --charter builders/claude-headless/agents/<id>/charter.yaml --dry-run`.
4. Offer to run it once — `./builders/claude-headless/agents/<id>/run.sh manual` — a few cheap
   cents, opt-in; for deeper repeatable independent evals, mention the standalone **gen-evals**
   skill. A human still confirms any factual claim.
5. To change anything, re-run the new-agent interview (or re-run this generator against the
   edited `brief.yaml`) — never hand-edit `charter.yaml`.
