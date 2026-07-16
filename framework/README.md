# CHARTER: the doctrine

*One page: what we believe, **why** the framework exists, and **how** it works.*

**A charter is a single file that ships with every agent and lists the only things that
agent is allowed to do.** The system that runs the agent reads this file and is the *only*
way it gets a model, tools, secrets, network, or a place to run. **If it isn't in the file,
the agent can't do it.** It is not a document people are asked to read and follow. It is
the gate every run passes through.

---

## What we believe

Ten points. Each one maps to something the runtime actually does; none is decoration.

1. **Every agent has an owner.** A named human on the hook for it. An unowned agent is the most common thing that goes wrong.
2. **Every agent gets an isolated home.** Its own bounded sandbox with a pinned model, thrown away after the run, so one agent's mess stays its own.
3. **An agent can only touch what it was granted.** The tools it may call, the sites it may reach, the secrets it may hold: all listed in the charter. If it isn't on the list, the agent can't do it.
4. **Every agent has a hard ceiling.** A cap on steps, time, and spend, so a stuck or runaway agent stops on its own instead of running all night.
5. **An agent trusts only its own instructions.** Its system prompt is built from sources you chose. Every page it fetches and everything it writes to memory is data, never a new order, no matter what that text says.
6. **Dangerous actions wait for a human.** Anything destructive or public (dropping data, sending mail, posting) needs explicit approval and never fires on its own.
7. **An agent has to prove it still works.** A test set it must pass to ship, and a live measure that pages its owner when quality slips. A charter makes an agent contained and owned, not correct.
8. **Every agent can be watched and switched off.** Each run is logged, and a single write pauses one agent or the whole fleet.
9. **The rules live in one file the runtime enforces.** The loader reads the charter and is the only door. A missing or broken charter means the agent doesn't start. We keep that enforced list small and never claim a control that nothing actually checks.
10. **It's a menu, not a mandate.** Adopt as much as your scale needs; a smaller adoption is a real choice, not a failing grade. The one point we would never drop is the owner.

---

## Why it exists

When you run one agent, you can watch it. When you run a thousand, you can't. **Human
attention doesn't scale as a safety net.** CHARTER moves safety from *"someone is watching"*
to *"the rules are built into the walls,"* and turns fleet-wide questions into one query
instead of a thousand code reviews: *pause every agent that touches customer data; which
ones still run the retired model; who owns this misbehaving one, right now.*

One enforced file buys what you'd otherwise chase across five separate systems:

- **No surprise bill:** a hard budget ceiling per agent, so a stuck loop can't quietly burn $5,000 overnight.
- **A small blast radius:** fooled by a poisoned web page, an agent can only do what its charter allows. It can't email your data out if `send_email` was never on its list.
- **No silent drift:** the model is pinned, so behaviour doesn't change under you when a provider swaps or retires one.
- **Caught quality rot:** every agent carries a test set it must pass to ship, and a live number that pages its owner when quality drops.
- **An answer for the auditor:** one place shows what each agent can touch, what data it handles, and what it keeps.

---

## How it works

1. **The file is the gate.** One `charter.yaml` per agent. A *loader* reads it and hands the
   agent its model, tools, secrets, network, and sandbox, and nothing else. **The loader is
   the only door:** if there is any side channel (an env var, a "temporary" override), the
   file is a lie, and a lie you trust is worse than no file at all.
2. **It fails closed.** A missing, malformed, or invalid charter means the agent doesn't
   start. Better a dead agent than an ungoverned one.
3. **Memory is untrusted.** An agent's memory is written by the agent from whatever it saw,
   maybe a page trying to trick it. So memory, like any fetched page, is cleaned and
   provenance-tagged before it can be read as anything but data.

### What's in the file: the name is the checklist

CHARTER is a memory aid for the seven things every agent needs. Most are fields in the file;
one or two are behaviours the runtime does the same way for everyone.

| | Part | What it covers | In the file |
|---|---|---|---|
| **C** | **Context** | the only sources its instructions are built from, including your own `.md` instruction files | `context.trusted_sources`; everything else it reads is untrusted |
| **H** | **Harness** | the bounded box it runs in | `model`, `sandbox`, `budget` |
| **A** | **Authority** | what it may touch: built-in tools plus any custom tools, MCP servers, and skills you plug in | `tools`, `mcp`, `skills`, `credentials`, `egress`, `approval_tier` |
| **R** | **Recovery** | how it fails safely | a runtime behaviour (retry / resume / dead-letter) |
| **T** | **Telemetry** | what is watched, kept, and logged | `data` (sensitivity, redaction, retention) plus a run log each run (who, when, outcome, cost) |
| **E** | **Evals** | how you know it works | `evals`: must-pass tests plus a live number that pages the owner |
| **R** | **Responsibility** | who owns it, and the off switch | `id`, `version`, `owner`, `status` |

*(The exact fields, and how strictly each is held, live in the manifestation's schema.)*

---

## How much to adopt (names, not levels; none is "more correct")

- **Solo:** the questions in your head. Clear thinking, no enforcement.
- **Declared:** the charter as a file. A readable, versioned contract; still not enforced.
- **Enforced:** a loader (a Claude Code hook, or a generated subagent) enforces it for one
  agent, with a pinned model, a tool allow-list, and a budget. Real walls.
- **Fleet:** a registry of many agents, queryable and kill-switchable in one write.

Two rules keep the menu from turning to mush: **choose your subset per deployment, not per
agent** (so fleet questions still work), and **never claim a control nothing enforces.**

> One engineer's opinionated 2026 take, not a standard handed down from on high. Parts will
> turn out wrong as the tools change. That's expected. Take what fits.

---

**The builders:** [`../builders/claude-headless/`](../builders/claude-headless/) turns this
doctrine into a working toolkit. Open it in Claude Code and it interviews you to build a
governed agent that runs headless. The shared engine is in [`../core/`](../core/).
