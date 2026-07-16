# claude-sdk builder (placeholder, not built yet)

Planned builder that turns a CHARTER charter into an enforced agent on the Claude Agent SDK
(claude-agent-sdk). It isn't implemented yet; this folder marks the slot.

When built it will, like `../claude-headless/`:
- reuse the shared engine in `../../core/` (charter schema, `validate.py`, `eval_checks.py`),
- translate a charter into the Claude Agent SDK's config (model, tools, turn cap, MCP, system prompt, and so on),
- add the eval gate and logging, and print an honest report of what this runtime enforces.

Reference implementation: `../claude-headless/`. Doctrine: `../../framework/README.md`.
