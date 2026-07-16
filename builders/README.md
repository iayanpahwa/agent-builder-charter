# builders/

One builder per runtime. Each turns the same charter (governed by `../core/`) into an
enforced, runnable agent for its target, and prints an honest report of what that runtime
can and can't enforce.

- `claude-headless/`: built. Agents run as `claude -p` commands (the highest-fidelity option today).
- `claude-sdk/`: planned (Claude Agent SDK; programmatic, same harness).
- `openai-agents/`: planned (OpenAI Agents SDK).
- `langchain/`: planned (LangChain / LangGraph deep agents).

The shared, runtime-neutral pieces (schema, validators, eval checks) live in `../core/` and
the doctrine in `../framework/`. Builders reuse them; they don't duplicate them.
