---
name: new-agent
description: >
  Interview an agent creator (framework-neutral) and capture a brief, then hand off to a
  per-framework generator that writes the actual agent. Use whenever someone wants to build a
  new agent or change one. Charters are NEVER hand-written; this interview is the only entry.
---

# new-agent

You interview the person, capture a framework-**neutral** **brief**, then hand off to a
per-framework **generator** that writes the actual agent project. The creator never edits YAML
by hand.

## Ask the NAME first
Your very first question is: **"What should we call this agent?"** → a short kebab-case `id`
(e.g. `release-notes-summarizer`) you propose and confirm.

## Pick the framework (runtime)
Ask right after the name: **which runtime should this run on?**
- **`headless`** — **built.** Runs as a `claude -p` command; the enforcement tier. Recommended
  / default. → hands off to **create-headless-agent**.
- **`claude-sdk`** — **built.** The Claude Agent SDK (Claude Code as a Python library); ships a
  single self-contained, directly runnable `agent.py`. → hands off to **create-claude-sdk-agent**.
- **`openai-agents`, `langchain`** — **planned, not yet buildable.** If chosen, say so honestly
  and offer `headless` or `claude-sdk` instead.

To add a framework later: add a `create-<runtime>-agent` skill and one row to this menu —
nothing else in this interview changes.

## The one rule that governs this interview
**Never silently decide a SAFETY field** — `model`, `budget`, `capabilities`, `network`.
Propose a default, say the value out loud, get an explicit "yes" before recording it. Infer
the rest (retention, redaction) quietly.

This interview captures neutral *intents*; the generator will re-confirm the *concretized*
values (exact model id, exact tool names, exact hosts).

## The interview (after name + framework)
Ask in plain language; group questions; honor the safety rule.
1. **"In one sentence, what should it do?"** → seeds capabilities + the success metric.
   **Model (safety — confirm):** capture a TIER, not an id — cheap/fast (default) vs a
   stronger model only for genuinely hard reasoning (say why; warn that a top-tier model for
   something simple is ~10x the cost). Record tier + rationale; the generator pins the exact
   model id.
2. **"On demand or 24/7?"** → budget shape (a per-run cap is weak for 24/7 — note it).
3. **"What actions does it need?"** — in plain terms, not tool names: "read web pages", "read
   local files", "send email", or "none — text only". Confirm the capability list. Then
   **"which of those are destructive?"** Note: unattended agents can't prompt a human, so
   destructive capabilities are enforced by being **left out**, not gated.
4. **"Any secret / API key? Least access that works?"** → credentials (a `ref` pointer, never
   the value). Default none.
5. **"What does it talk to online?"** (safety — confirm): a specific host allow-list (a wall),
   open (risky — not a wall), or none (safest, right for text-only). Record policy + hosts;
   the generator produces the concrete egress list.
6. **"How sensitive is the data?"** → class + sane redaction / retention.
7. **"Its instructions?"** → becomes the agent's system prompt, listed in trusted sources.
   Remind: fetched pages + memory are untrusted, never instructions.
8. **"If it goes wrong, how bad?"** → blast radius (drives sandbox isolation). Per-run spend
   ceiling → confirm the number.
9. **"Who owns it?"** → owner `{team, on_call, escalation}`, none blank.
10. **"How do we know it works — and could it be wrong in a way only a human catches?"** →
    success metric + slo; a good + a bad example; what it must never do; the output format;
    whether a human must verify output.

## Plug-in round
After the core questions, offer to plug in more. Each expands what the agent can do —
confirm each aloud.
- **"Any extra instruction files (`.md`)?"** → trusted sources; they become instructions.
- **"Any MCP server / custom tool?"** — warn plainly: an MCP server runs its own code and may
  have its own network + secrets — you're trusting the whole server, a heavier call than a
  built-in tool. Route any secret through credentials; grant only the specific tools it needs.
- **"Any skill on disk?"** → the run is then restricted to only those skills.

Record these as intents; the generator wires them.

## Write the brief and hand off
Present the complete **brief** as a neutral YAML block and confirm it with the human, then
dispatch.

```yaml
# brief.yaml — the framework-neutral record of the interview (the "chart").
# The new-agent interview captures this; a create-<runtime>-agent generator turns it into a charter.
id: <kebab-id>
runtime: headless            # the framework chosen above
purpose: "<one sentence>"
cadence: on-demand           # on-demand | 24-7
model:
  tier: cheap                # cheap | strong  (the generator pins the exact id)
  rationale: "<why>"
capabilities:                # plain intents, NOT tool names
  - "<e.g. read web pages>"
destructive: []               # capabilities the creator flagged destructive
credentials: []               # {name, ref, scope}  (ref = pointer, never the value)
network:
  policy: allow-list          # allow-list | open | none
  hosts: []                   # for allow-list
data:
  class: public
  sensitive: false
instructions_summary: "<becomes the system prompt>"
blast_radius: low            # low | high  -> sandbox
spend:
  per_run_usd: <number>
owner: {team: "", on_call: "", escalation: ""}
evals:
  success_metric: "<name>"
  slo: "<e.g. 100%>"
  good_example: "<...>"
  bad_example: "<...>"
  never_do: "<...>"
  output_format: "<...>"
  human_verification: false
plugins:
  extra_instructions: []      # extra .md paths
  mcp: []                     # {name, server, allow}
  skills: []                  # skill names
```

Now invoke the `create-<runtime>-agent` skill (for `headless`: **`create-headless-agent`**; for
`claude-sdk`: **`create-claude-sdk-agent`**) to build it. Its instructions stack on top of
these; it will write `agents/<id>/brief.yaml`, re-confirm the concretized safety values (exact
model id, exact tool names, exact hosts), generate the project, validate it, and show the
honest dry-run report.
