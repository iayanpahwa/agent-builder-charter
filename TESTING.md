# Testing

How to run and write the tests for this repo. The suite guards the one promise the
whole project rests on: **the loader enforces what the charter says, and the report
never claims a control that nothing enforces.** A test here is how we keep that true
as the code changes.

## What the suite is (and isn't)

Every test in `tests/` is **offline and free**:

- **No tokens.** Nothing calls a model. The runner's enforcement, the schema, the
  loader, and the report are all exercised as pure functions or via a local subprocess.
- **No real network.** The egress tests spawn `egress_guard.py` as a subprocess, but it
  only parses the URL string in a tool event — it never fetches anything.
- **No real agent runs.** We test the walls (model pin, tool allow-list, budget, egress,
  redaction, retention, fail-closed), not an agent's answers. Answer quality is the job
  of a charter's own `evals`, which is separate.
- **Fast.** The whole suite (120 tests today) runs in a second or two.
- **Stock-CI-safe.** No secrets, no API keys, no custom pytest markers to deselect —
  a clean checkout runs all of it with nothing configured.

## Prerequisites

Python 3, the repo's dependencies, and `pytest`:

```bash
pip install -r requirements.txt   # PyYAML + jsonschema
pip install pytest                 # test-only, not a runtime dependency
```

`requirements.txt` holds what the framework itself uses — PyYAML to read charters,
`jsonschema` to validate them. `pytest` is only needed to run the suite, so it's kept
out of `requirements.txt`.

## Running the tests

From the repo root:

```bash
python3 -m pytest -q            # the whole suite, quiet
python3 -m pytest               # verbose (per-test names)
python3 -m pytest tests/test_egress_guard.py          # one file
python3 -m pytest tests/test_registry.py -k duplicate  # one test by name substring
python3 -m pytest -x            # stop at the first failure
```

Run from the repo root so `tests/conftest.py` is discovered — it puts `core/` on
`sys.path`, which is how the tests `import loader` / `import registry` the same way
`core/validate.py` does. Tests that reach into the builder add
`builders/claude-headless/` to the path themselves.

## What each file covers

| File | What it locks in |
|---|---|
| `conftest.py` | Shared fixtures: `core/` on `sys.path`, the `SHIPPED_CHARTERS` list (all three must validate), and the `good_charter` fixture (a fresh copy of a known-valid charter per test). |
| `test_schema_validation.py` | Shipped charters validate; each schema violation is refused with `CharterInvalid`; the custom egress semantics; the `validate.py` CLI; the fail-closed basics. |
| `test_registry.py` | `Registry.register()` refuses a second agent under an id already in the fleet (`DuplicateAgentId`) instead of silently overwriting the first. |
| `test_enforcement_vocab.py` | The enforcement vocabulary stays honest: every schema `x-enforced` tag is a known token, every status the report emits is a known token, and every field the schema tags `block` gets an actual report row. |
| `test_report_honesty.py` | The `--dry-run` report is total (every field a charter can carry gets a row) and honest (the wording never overstates what headless actually enforces). |
| `test_egress_guard.py` | `egress_guard.py`'s runtime allow/deny decisions — `[any]` opens, `[none]` denies, a scoped list gates by host, WebSearch (no host) is denied. |
| `test_egress_hook_path.py` | Regression: the hook is wired with the charter's *real* path, so an agent whose charter isn't named `charter.yaml` still gets egress enforced (not failed-closed by a broken path). |
| `test_scoped_env.py` | `scoped_env()` builds the subprocess env from OS essentials + Claude auth + the charter's declared `env:` refs, and drops every other host secret. |
| `test_trusted_sources.py` | `context.trusted_sources` can't escape the charter directory — the schema rejects absolute/`..` paths, and the runtime's `realpath` check catches even a clean-looking symlink. |
| `test_input_validation.py` | Malformed egress entries and tool/MCP names are rejected at load, and the old `host_allowed` `*.`-strip bypass stays closed. |
| `test_redaction.py` | `_redact` scrubs declared patterns and secret values, `_declared_secret_values` reads env-ref credentials, and `prune_logs` deletes output and trims `runs.jsonl` past the retention window. |

## Writing a new test

Keep new tests to the same bar:

1. **Offline and free.** No tokens, no real network, no live agent run. Test the wall,
   not the model. If you need a charter, start from the `good_charter` fixture and mutate
   the one field under test.
2. **Assert fail-closed.** For anything security-relevant, the important test is the
   *deny* path: a malformed charter, an escaping path, a host not on the list. Prove the
   door stays shut, not just that the happy path opens it.
3. **Prove the test can fail (mutation check).** Before you trust a new test, break the
   code it guards and confirm the test goes red; then revert. A test that passes no matter
   what the code does is worse than no test — it's a false sense of safety, exactly the
   thing this project exists to prevent.
4. **Use `parametrize` for tables** of valid/invalid inputs (see `test_input_validation.py`
   or `test_trusted_sources.py`) rather than copy-pasting near-identical test bodies.
5. **Match the style.** Short module docstring saying what the file locks in and why it's
   token-free; group related cases with `# ---` section comments.

## Running it in CI

There's no CI workflow in the repo yet, but the suite is built to drop straight into one —
no tokens, no secrets, no markers to deselect. A single job on a stock runner is all it takes
to gate every pull request and push:

```bash
pip install -r requirements.txt pytest
python3 -m pytest -q
```
