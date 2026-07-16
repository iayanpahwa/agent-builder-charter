# langchain builder — PLACEHOLDER (not built yet)

Planned builder that turns a CHARTER charter into an enforced agent on **LangChain / LangGraph (deep agents)**. Not
implemented yet — this folder marks the slot.

When built it will, like `../claude-headless/`:
- reuse the shared engine in `../../core/` (charter schema, `validate.py`, `eval_checks.py`),
- translate a charter into LangChain / LangGraph (deep agents)'s config (model, tools, turn cap, MCP, system prompt, …),
- add the eval gate + logging, and print an honest report of what THIS runtime enforces.

Reference implementation: `../claude-headless/`. Doctrine: `../../framework/README.md`.
