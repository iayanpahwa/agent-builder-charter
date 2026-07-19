# builders/

One builder per runtime. Each turns the same charter (governed by `../core/`) into an
enforced, runnable agent for its target, and prints an honest report of what that runtime
can and can't enforce.

- `claude-headless/`: built. Agents run as `claude -p` commands (the highest-fidelity option today).
- `claude-sdk/`: built. Ships a single self-contained, runnable `agent.py` on the Claude Agent
  SDK (Claude Code as a library).
- `langchain/`: built. Ships a single self-contained, runnable `agent.py` on the LangGraph minimal
  harness (`create_react_agent`); Deep Agents is an opt-in, not the default.
- `openai-agents/`: planned (OpenAI Agents SDK).

The shared, runtime-neutral pieces (schema, validators, eval checks) live in `../core/` and
the doctrine in `../framework/`. Builders reuse them; they don't duplicate them.
