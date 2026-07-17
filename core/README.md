# core/ (the shared engine)

Everything in this folder is runtime-neutral: it knows what a charter *is* and how to
reason about one, but nothing about how any particular runtime executes an agent. Every
builder under [`../builders/`](../builders/) reuses these files instead of duplicating them.

The doctrine (the *why*) is one page: [`../framework/README.md`](../framework/README.md).
Read that first if you haven't.

## The files

| File | What it is | Run it? |
|---|---|---|
| `charter.schema.yaml` | The field-by-field spec: every charter field, its type, and an `x-enforced` tag saying how strictly it's held (`block` / `gate` / `alarm` / `present` / `declared` / `none`). This is the source of truth for what a charter contains. | no |
| `CHARTER.md` | The enforcement model in plain language: what the `x-enforced` tags mean (`block` / `gate` / `alarm` / `present` / `declared` / `none`), why a bad charter fails closed, and how the live off switch works. | no |
| `validate.py` | Validate one charter and exit non-zero if it's invalid. Used by CI and by the `new-agent` interview, which loops until a generated charter passes. | `python3 validate.py <charter.yaml>` |
| `loader.py` | The reference loader, and the smallest real proof of the bet that *the loader is the only door*. It loads a charter fail-closed and enforces three fields end to end (status, tools, budget) against a fake agent, showing pause-mid-run, budget-kill, tool-deny, and a broken charter refusing to start. | `python3 loader.py` |
| `registry.py` | The reference fleet board: the live picture of many agents. Shows the headline promise as one query (*pause every agent that touches customer data*), plus the drift query (who still runs a retired model) and the ownership query. It also refuses a duplicate agent `id`, so a fleet query can't silently miss or double-count one. | `python3 registry.py` |
| `eval_checks.py` | The deterministic invariant checks (`contains`, `matches`, `contains_url`, `claims_cited`, and so on). Pure functions, no model calls, no cost. A builder's eval gate and the `run-evals` skill both call these. | imported |
| `examples/` | Sample charters used by the demos above, e.g. `price-watch-scraper.charter.yaml` (a standalone scraping agent, filled out end to end). | no |

## How they fit together

```
charter.schema.yaml   <- the spec (what a charter must contain)
        |
        |-- validate.py      checks a charter against it (fail closed)
        |-- loader.py        loads + enforces a charter for one run
        |-- registry.py      holds many charters + their live status
        `-- eval_checks.py   checks an agent's OUTPUT against invariants
```

`loader.py` and `registry.py` are concept demos: they enforce against a fake agent so you can
prove the mechanics in something that runs in one second. The real enforcement of a live agent
happens in a builder (e.g. [`../builders/claude-headless/run_headless.py`](../builders/claude-headless/run_headless.py),
which reuses `loader.py` and `eval_checks.py` from here).

## The two rules this code exists to make real

1. **The loader is the only door.** An agent gets a model, tools, secrets, and network only
   through the loader reading the charter. Any side channel makes the file a lie.
2. **Fail closed.** A missing, malformed, or schema-invalid charter means the agent doesn't
   start. Better a dead agent than an ungoverned one.
