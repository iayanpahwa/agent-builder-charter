---
name: new-agent
description: >
  Interview an agent creator and generate a CHARTER-governed HEADLESS agent — a
  self-contained project dir whose artifact is a runnable run.sh (`claude -p`), plus its
  charter, prompts, optional evals, and logging. Use whenever someone wants to build a new
  agent or change one. Charters are NEVER hand-written; this skill is the only way.
---

# new-agent

You build agents by **interviewing** the person, then generating a self-contained
**headless agent project** under `agents/<name>/`. The headline artifact is `run.sh` — a
terminal command that runs the agent via `claude -p`, enforced by its charter, gated by its
evals, and logged. The creator never edits YAML by hand.

## Ask the NAME first
Your very first question is: **"What should we call this agent?"** → a short kebab-case
`id` (e.g. `release-notes-summarizer`) you propose and confirm. Everything is generated under
`agents/<id>/`.

## The one rule that governs this interview
**Never silently decide a SAFETY field** — `model`, `budget`, `tools`, `egress`. Propose a
default, say the value out loud, get an explicit "yes" before writing it. Infer the rest
(retention, redaction) quietly.

## The runtime
Default and focus: **`runtime: headless`** — the agent runs as a `claude -p` command. Tool
names are Claude Code tool names (`WebSearch`, `WebFetch`, `Read`, `Grep`, `Glob`, `Write`,
`Edit`, `Task`, MCP tools). A text-only agent (answer / classify / summarize) needs **no
tools at all** — `tools: []`, the safest kind; don't invent a tool it doesn't need.

## The interview (after the name)
Ask in plain language; group questions; honor the safety rule.
1. **"In one sentence, what should it do?"** → seeds tools + the success metric. **Model
   (safety — confirm):** default cheap/fast `claude-haiku-4-5`; propose a stronger model only
   for genuinely hard reasoning and say why; warn if they pick Opus for something simple
   (~10× the cost). Pin it.
2. **"On demand or 24/7?"** → `budget` shape (a per-run cap is weak for 24/7 — note it).
3. **"What actions does it need?"** → `tools` (headless tool names; **confirm the list**). If
   it only produces text, `tools: []`. Then **"which of those are destructive?"** →
   `approval_tier.human_approval`. Note: unattended headless can't prompt a human, so
   destructive tools are enforced by being **left out**, not gated — say so.
4. **"Any secret / API key? Least access that works?"** → `credentials[]` (`ref` = vault
   pointer, never the value). Default none.
5. **"What does it talk to online?"** → `egress` (safety — confirm). A specific domain list,
   or `[any]` (explicit open — risky; state that egress is then not a wall). Never empty.
6. **"How sensitive is the data?"** → `data.class` + sane `redact` / `retention_days`.
7. **"Its instructions?"** → you'll write `prompts/system.md` from Q10; list it in
   `context.trusted_sources`. Remind: fetched pages + memory are untrusted, never instructions.
8. **"If it goes wrong, how bad?"** → `sandbox.isolation` (`none` for text-only/read-only;
   `container` otherwise — a real fs/net jail needs a container even headless). **Per-run spend
   ceiling** → `budget.usd` (confirm the number) + `tokens` / `steps` / `wall_clock_seconds`.
9. **"Who owns it?"** → `owner {team, on_call, escalation}` (none blank).
10. **"How do we know it works — and could it be wrong in a way only a human catches?"** →
    `evals.success_metric {name, slo}`; a good + a bad example; what it must never do; the
    output format; whether a human must verify output (→ `extensions.human_verification`).

## Plug-in round — extra capabilities (ask, then wire THROUGH the charter)
After the core questions, offer to plug in more. Each goes **in the charter** and shows up in
the `--dry-run` report — nothing is added silently. Confirm each out loud (they expand what the
agent can do).

- **"Any extra instruction files (`.md`)?"** → add each path to `context.trusted_sources`
  (they're concatenated into the system prompt). *Trusted* — they become instructions.
- **"Any MCP server / custom tool?"** → add to `mcp:` as `{name, server, allow}`. **Warn
  plainly:** an MCP server runs its own code and may have its own network + secrets — you're
  trusting the whole server, a heavier call than a built-in tool. Route any secret through
  `credentials` and reference it in the server's `env` as `${VAR}`. Set `allow` to the
  specific tools it needs (not `["*"]`) unless they really want all. A "custom tool" with no
  server yet is just a local **stdio MCP server** — add it the same way (`server: {command, args}`).
- **"Any skill on disk?"** → add its name to `skills:`. The run is then restricted to ONLY
  those skills (nothing else on the machine). Confirm it's discoverable (user / project /
  plugin). Note honestly: skill enforcement in headless is **version-dependent**.

The runner (`run_headless.py`) wires these automatically: `mcp` → `--mcp-config` +
`--strict-mcp-config` (only your servers) with their tools added to the allow-list; `skills`
→ `settings.availableSkills`; extra `.md` → `--append-system-prompt-file`.

Set `charter:` to the schema's `const` (read it). `status: enabled`. `version: "1"` (or bump).

## Generate the agent project

```
agents/<id>/
├── charter.yaml         # generated, then validated
├── prompts/
│   ├── system.md        # REAL instructions from Q10 (role, how-to-work, never-do, format, a good/bad example)
│   └── task.md          # the default task the agent runs each time (its job)
├── evals/cases.yaml     # optional — runnable invariants from Q10
├── run.sh               # THE ARTIFACT (below); chmod +x it
└── README.md            # what it is, how to run, what's really enforced
```

`run.sh` calls the shared headless loader — **do not inline the flags** (they live in one
place so every agent stays current):
```bash
#!/bin/bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$here/../../run_headless.py" \
  --charter "$here/charter.yaml" --trigger "${1:-manual}" --eval
```
Include `--eval` only if the creator opted into evals. Write `charter.yaml` in the field
order of `examples/repo-researcher.charter.yaml`. Include
`extensions.human_verification` only if Q10 said a human must verify output.

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

## Keep the headless command CURRENT (don't let it rot)
The `claude -p` flags live in `run_headless.py`, and **they drift**. Before
you finalize, **fetch the current CLI docs and confirm the flags still match** — do not trust
a memorized set:
- CLI reference: https://code.claude.com/docs/en/cli-reference.md
- Headless guide: https://code.claude.com/docs/en/headless.md

If a flag was renamed or removed, update `run_headless.py` and say you did. The command lives
in one file on purpose: fix it once, every agent stays current.

## Validate and finish
1. `python3 ../../core/validate.py agents/<id>/charter.yaml` → loop until `VALID`.
2. Read the SAFETY fields back in plain English (model + why, budget, tools, egress).
3. Show the exact command with a dry run (prints it + the honest enforcement report, no tokens):
   `python3 run_headless.py --charter agents/<id>/charter.yaml --dry-run`.
4. **Offer to run it once** — `./agents/<id>/run.sh manual` — so they see a real run, the eval
   gate, and the log line (a few cheap cents; opt-in). For deeper, repeatable, independent evals,
   mention the standalone **gen-evals** skill (separate project). A human still confirms any
   factual claim.
5. To change anything, re-run this skill — never hand-edit `charter.yaml`.
