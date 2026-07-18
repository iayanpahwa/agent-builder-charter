# agents/

Your SDK agents live here, one folder per agent, created by the root `new-agent` interview +
the `create-claude-sdk-agent` generator (never by hand). Empty on a fresh clone. Each folder
holds `brief.yaml`, `charter.yaml`, `agent.py` (the artifact — directly runnable, no wrapper
script), `prompts/`, optional `evals/`, `requirements.txt`, and a gitignored `logs/`.

To make one, open the repo root in Claude Code and say what you want, for example: *"help me
build a release-notes summarizer agent"* — pick the `claude-sdk` runtime when asked. Claude
Code runs the interview, which captures a brief; the generator writes `agents/<your-id>/` here.

Step by step: see `GUIDE.md`, or the Quickstart in `README.md`.
