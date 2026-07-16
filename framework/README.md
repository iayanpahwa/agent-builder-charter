# CHARTER — the doctrine

*One page: what the framework is, **why** it exists, and **how** it works.*

**A charter is a single file that ships with every agent and lists the only things that
agent is allowed to do.** The system that runs the agent reads this file and is the *only*
way it gets a model, tools, secrets, network, or a place to run. **If it isn't in the file,
the agent can't do it.** It is not a document people are asked to read and follow — it is
the gate every run passes through.

---

## Why it exists

When you run one agent, you can watch it. When you run a thousand, you can't. **Human
attention doesn't scale as a safety net.** CHARTER moves safety from *"someone is watching"*
to *"the rules are built into the walls,"* and turns fleet-wide questions into one query
instead of a thousand code reviews — *pause every agent that touches customer data; which
ones still run the retired model; who owns this misbehaving one, right now.*

One enforced file buys what you'd otherwise chase across five separate systems:

- **No surprise bill** — a hard budget ceiling per agent, so a stuck loop can't quietly burn $5,000 overnight.
- **A small blast radius** — fooled by a poisoned web page, an agent can only do what its charter allows. It can't email your data out if `send_email` was never on its list.
- **No silent drift** — the model is pinned; behaviour doesn't change under you when a provider swaps or retires one.
- **Caught quality rot** — every agent carries a test set it must pass to ship, and a live number that pages its owner when quality drops.
- **An answer for the auditor** — one place shows what each agent can touch, what data it handles, and what it keeps.

---

## How it works

1. **The file is the gate.** One `charter.yaml` per agent. A *loader* reads it and hands the
   agent its model, tools, secrets, network, and sandbox — and nothing else. **The loader is
   the only door:** if there is any side channel (an env var, a "temporary" override), the
   file is a lie, and a lie you trust is worse than no file at all.
2. **It fails closed.** A missing, malformed, or invalid charter means the agent doesn't
   start. Better a dead agent than an ungoverned one.
3. **Memory is untrusted.** An agent's memory is written by the agent from whatever it saw —
   maybe a page trying to trick it. So memory, like any fetched page, is cleaned and
   provenance-tagged before it can be read as anything but data.

### What's in the file — the name **is** the checklist

CHARTER is a memory aid for the seven things every agent needs. Most are fields in the file;
one or two are behaviours the runtime does the same way for everyone.

| | Part | What it covers | In the file |
|---|---|---|---|
| **C** | **Context** | the only sources its instructions are built from — including your own `.md` instruction files | `context.trusted_sources`; everything else it reads is untrusted |
| **H** | **Harness** | the bounded box it runs in | `model`, `sandbox`, `budget` |
| **A** | **Authority** | what it may touch — built-in tools plus any custom tools, **MCP servers**, and **skills** you plug in | `tools`, `mcp`, `skills`, `credentials`, `egress`, `approval_tier` |
| **R** | **Recovery** | how it fails safely | a runtime behaviour (retry / resume / dead-letter) |
| **T** | **Telemetry** | what is watched, kept, and logged | `data` (sensitivity, redaction, retention) + a **run log** each run (who, when, outcome, cost) |
| **E** | **Evals** | how you know it works | `evals` — must-pass tests + a live number that pages the owner |
| **R** | **Responsibility** | who owns it, and the off switch | `id`, `version`, `owner`, `status` |

*(The exact fields, and how strictly each is held, live in the manifestation's schema.)*

---

## What we believe

- **Earn every rule.** Keep the enforced list small — a rule earns its place only when the
  system actually does something with it. Everything else is advice, not a wall.
- **Never claim a control that nothing enforces.** Drop any control with eyes open, but the
  file must not *say* it limits egress (or tools, or spend) while nothing checks. An honest
  blank beats a hollow promise, because someone will trust the promise.
- **It's a menu, not a mandate.** Adopt to your scale; a smaller adoption is a real choice,
  not a failing grade. The one field worth keeping even at the most relaxed shape is
  **`owner`** — an unowned agent is the most common thing that goes wrong.
- **Contained is not correct.** A charter bounds what an agent *can do*, not whether it does
  it *well*. "Has a charter" means *contained and owned* — it does **not** mean *safe*.
  Correctness comes from evals and, where it matters, a human checking the output.

## How much to adopt (names, not levels — none is "more correct")

- **Solo** — the questions in your head. Clear thinking, no enforcement.
- **Declared** — the charter as a file. A readable, versioned contract; still not enforced.
- **Enforced** — a loader (or a Claude Code hook / generated subagent) enforces it for one
  agent: pinned model, tool allow-list, budget. Real walls.
- **Fleet** — a registry: many agents, queryable, kill-switchable in one write.

Two rules keep the menu from turning to mush: **choose your subset per deployment, not per
agent** (so fleet questions still work), and **never claim a control nothing enforces.**

---

**→ The builders:** [`../builders/claude-headless/`](../builders/claude-headless/) turns this
doctrine into a working toolkit — open it in Claude Code and it interviews you to build a
governed agent that runs headless. The shared engine is in [`../core/`](../core/).
