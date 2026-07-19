# agents/

Your LangChain agents live here, one folder per agent, created by the root `new-agent` interview
+ the `create-langchain-agent` generator (never by hand). Empty on a fresh clone. Each folder
holds `brief.yaml`, `charter.yaml`, `agent.py` (the artifact — a single self-contained, directly
runnable file with the charter embedded, no wrapper script), `prompts/`, optional `evals/`,
`requirements.txt`, and a gitignored `logs/`.

To make one, open the repo root in Claude Code and say what you want, for example: *"help me
build a docs researcher agent"* — pick the `langchain` runtime when asked. Claude Code runs the
interview, which captures a brief; the generator writes `agents/<your-id>/` here.

Step by step: see `GUIDE.md`, or the Quickstart in `README.md`.
