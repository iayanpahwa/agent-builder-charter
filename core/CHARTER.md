# core/ — the shared engine (runtime-neutral)

The **doctrine** — what CHARTER is and the values behind it — lives in the framework
sub-project: [`../framework/README.md`](../framework/README.md). Read that first.

This folder is the **shared, runtime-neutral engine** that every builder in `../builders/`
reuses (they don't duplicate it):

- `charter.schema.yaml` — the field-by-field spec. Every field has a plain description and an
  `x-enforced` tag. **This is the source of truth for what a charter contains.**
- `validate.py` — validate one charter (fail-closed); used by the interview and by builders.
- `loader.py` — the charter loader + a concept demo of per-run enforcement (status/tools/budget
  + fail-closed). Run it: `python3 loader.py`.
- `registry.py` — the fleet board concept demo (pause / audit / ownership by query).
- `eval_checks.py` — the deterministic invariant checks (contains / matches / contains_url /
  claims_cited / …) that a builder's eval gate calls.
- `examples/price-watch-scraper.charter.yaml` — a standalone charter, used by the demos.

The **per-runtime** pieces (how a charter becomes a *runnable* agent) live in each builder —
e.g. `../builders/claude-headless/run_headless.py` (the headless runner) and `cc_guard.py`
(its egress hook). Nothing runtime-specific belongs here.

## How strictly each field is held (the `x-enforced` tags)
- **R** — a hard wall: the run is blocked or killed on the spot.
- **CI** — a release gate: a failure blocks the deploy (the evals test set).
- **AL** — an alarm: the owner is paged (the evals live number).
- **P** — must be filled in: the registry rejects a blank one (`id`, `version`, `owner`).
- **none** — declared, not enforced (e.g. `runtime`, `extensions`).

A missing, malformed, or schema-invalid charter **fails closed** — the agent doesn't start.
Better a dead agent than an ungoverned one.

## The off switch
`status` is the one field that changes during an *incident*, not development. Its live value
lives in the **registry** the runtime checks before every run, so pausing an agent (or a whole
group) is one write, not a redeploy. The file declares the *starting* status; the registry
holds the *current* one.
