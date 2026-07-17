# Contributing

Thanks for wanting to help. This project has a strong spine, so a few ground
rules keep contributions in line with what it stands for.

## The rules that can't bend
1. **Never claim a control that nothing enforces.** If a field in the schema
   can't actually be held at runtime, its `x-enforced` tag must say so. An honest
   blank beats a fake promise. This is the whole point of the project.
2. **The loader is the only door.** Any path that lets an agent do something not
   in its charter is a bug, not a feature.
3. **Fetched content and memory are untrusted** — data, never instructions.
4. **Menu, not mandate.** Add capability as an option with an honest warning, not
   as a default that quietly widens what agents can do.

## What's most welcome
- A **new builder** under `builders/` (Claude Agent SDK, OpenAI Agents, LangChain,
  or another runtime). Reuse `core/`; don't duplicate it. Ship an honest report of
  what your runtime can and can't enforce.
- Schema improvements in `core/charter.schema.yaml` (with the matching enforcement
  in a builder, or a clearly-tagged `x-enforced: declared`).
- Better eval invariants in `core/eval_checks.py`.
- Docs, examples, and honest-limit writeups.

## How to propose a change
1. Open an issue describing the problem before a large change.
2. Keep changes surgical — match the surrounding style; don't refactor what isn't
   broken.
3. Examples must not contain real names, emails, internal hostnames, or company
   details. Use `example.com` / `@owner` placeholders.
4. Validate any charter you touch: `python3 core/validate.py <charter>`.
5. Run the tests: `python3 -m pytest -q`. See [`TESTING.md`](TESTING.md) for what the
   suite covers and how to add to it. New enforcement needs a test that fails without it.

## Licensing of contributions
Code contributions are under **Apache-2.0**; contributions to the `framework/`
doctrine are under **CC BY 4.0**. By submitting a contribution you agree it's
licensed under the same terms as the part of the repo it lands in.
