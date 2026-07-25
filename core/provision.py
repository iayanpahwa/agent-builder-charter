#!/usr/bin/env python3
"""core/provision.py — the one provisioning path for every builder.

    python3 core/provision.py <agent-dir>

Creates <agent-dir>/.venv, installs the agent's dependencies into it, resolves the external
binaries its runtime needs, and writes <agent-dir>/run — a generated shim that launches the
agent through absolute paths only, with no dependence on PATH, CWD, or shell state. A manual
run and a cron run therefore take exactly the same code path.

Two verbs, deliberately split:
  provision  resolves and installs; touches the network; run once per machine.
  run        installs nothing and never reaches a package index; the only live network is
             whatever the charter's `egress` declares.

That split is the wall. A runner that installs at invocation time would be an undeclared
egress path with arbitrary code execution on the end of it, which is exactly what a charter
is supposed to make impossible.

Nothing is installed outside <agent-dir>: no --user, no sudo, no system site-packages.
Uninstalling an agent is `rm -rf` on its directory.
"""

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# What each runtime needs. `entry` is how the shim invokes the agent; headless is the odd one
# out because its artifact is the shared run_headless.py driven by --charter, not a
# self-contained agent.py.
RUNTIMES = {
    "headless": {
        "builder": "claude-headless",
        "requirements": "requirements.txt",  # repo root: PyYAML + jsonschema
        "binaries": ["claude"],
        "entry": "run_headless",
        "trace": False,
    },
    "claude-sdk": {
        "builder": "claude-sdk",
        "requirements": os.path.join("builders", "claude-sdk", "requirements.txt"),
        "binaries": ["claude"],  # the Python SDK shells out to the Node CLI
        "entry": "agent.py",
        "trace": True,
    },
    "langchain": {
        "builder": "langchain",
        "requirements": os.path.join("builders", "langchain", "requirements.txt"),
        "binaries": [],
        "entry": "agent.py",
        "trace": True,
    },
}


def fail(msg, code=1):
    print(f"REFUSED: {msg}", file=sys.stderr)
    sys.exit(code)


# --- charter reading (stdlib only) ------------------------------------------------------
# PyYAML is one of the things we are about to install, so provisioning cannot depend on it.
# charter.yaml is generated, so `runtime:` is always a plain top-level scalar and these two
# narrow scans are safe. Anything deeper is the loader's job, not ours.
def read_runtime(agent_dir):
    """Declared runtime from charter.yaml, else inferred from the builder dir it sits in."""
    charter = os.path.join(agent_dir, "charter.yaml")
    if os.path.exists(charter):
        with open(charter) as f:
            m = re.search(r"^runtime:\s*([\w-]+)", f.read(), re.M)
        if m and m.group(1) in RUNTIMES:
            return m.group(1)
    for name, spec in RUNTIMES.items():  # fall back to the path (a deployed copy may differ)
        if os.sep + spec["builder"] + os.sep in agent_dir + os.sep:
            return name
    return None


def read_credential_vars(agent_dir):
    """Env var names the charter declares under `credentials`, for a presence check."""
    charter = os.path.join(agent_dir, "charter.yaml")
    if not os.path.exists(charter):
        return []
    with open(charter) as f:
        return sorted(set(re.findall(r"ref:\s*env:(\w+)", f.read())))


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# --- dependency install ------------------------------------------------------------------
def find_requirements(agent_dir, runtime):
    """Prefer the agent's own pinned lock, then its requirements.txt, then the builder's.

    Returns (path, hashed). A .lock is installed with hash checking; a .txt is not, because
    floors cannot be hash-pinned.
    """
    txt = os.path.join(REPO_ROOT, RUNTIMES[runtime]["requirements"])
    candidates = [
        os.path.join(agent_dir, "requirements.lock"),
        os.path.join(agent_dir, "requirements.txt"),
        txt[: -len(".txt")] + ".lock",  # the builder's lock, beside its requirements.txt
        txt,
    ]
    for p in candidates:
        if os.path.exists(p):
            return p, p.endswith(".lock")
    fail(f"no requirements file for runtime {runtime!r} (looked for {txt})")


def venv_python(agent_dir):
    return os.path.join(agent_dir, ".venv", "bin", "python")


def build_venv(agent_dir, req, hashed, force):
    """Create .venv in the agent dir and install into it. uv when available, else stdlib venv.

    Both paths produce an ordinary venv; the run path never invokes uv or pip, so uv is a
    provision-time accelerator you can adopt or drop without changing how agents run.
    """
    venv = os.path.join(agent_dir, ".venv")
    py = venv_python(agent_dir)
    uv = shutil.which("uv")

    if force and os.path.isdir(venv):
        shutil.rmtree(venv)

    if not os.path.exists(py):
        print(f"  creating .venv          ({'uv' if uv else 'python -m venv'})")
        cmd = [uv, "venv", venv] if uv else [sys.executable, "-m", "venv", venv]
        # S603 is suppressed on each subprocess call in this file: every argv element is a
        # literal, this interpreter, or a path from shutil.which — none of it comes from the
        # charter, the agent, or anything the agent fetched.
        subprocess.run(cmd, check=True, capture_output=True)  # noqa: S603

    print(
        f"  installing deps         ({os.path.relpath(req, REPO_ROOT)}"
        f"{', hash-checked' if hashed else ''})"
    )
    if uv:
        cmd = [uv, "pip", "install", "--python", py, "-r", req]
    else:
        cmd = [py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", req]
    if hashed:
        cmd.append("--require-hashes")
    r = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    if r.returncode != 0:
        fail(f"dependency install failed:\n{r.stderr.strip()}")
    return py


def resolve_binaries(names):
    """Resolve external binaries now, so cron cannot discover them missing at 3am."""
    found = {}
    for n in names:
        p = shutil.which(n)
        if not p:
            fail(
                f"{n!r} is not on PATH, and this runtime shells out to it.\n"
                f"  install it, then re-provision."
            )
        v = subprocess.run([p, "--version"], capture_output=True, text=True)  # noqa: S603
        if v.returncode != 0:
            fail(f"{p} is present but `{n} --version` failed — the binary looks broken.")
        print(f"  found {n:<17} {p}  ({v.stdout.strip().splitlines()[0] if v.stdout else 'ok'})")
        found[n] = p
    return found


# --- the run shim -------------------------------------------------------------------------
SHIM = """#!/bin/sh
# GENERATED by core/provision.py — do not edit; re-provision instead.
#   agent:   {agent_id} ({runtime})
#   deps:    {req_rel}
set -eu

AGENT_DIR='{agent_dir}'
VENV_PY='{venv_py}'
REQ_FILE='{req_file}'
REQ_SHA='{req_sha}'
PATH='{path}'
export PATH

# Dependencies changed since this shim was written? Refuse rather than run a stale venv.
if [ -f "$REQ_FILE" ]; then
  cur=$("$VENV_PY" -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$REQ_FILE")
  if [ "$cur" != "$REQ_SHA" ]; then
    echo "REFUSED: dependencies changed since provisioning." >&2
    echo "  re-provision: python3 {repo_root}/core/provision.py $AGENT_DIR" >&2
    exit 7
  fi
fi

# Credentials for unattended runs (cron/launchd inherit no shell rc). Optional: an
# interactive run just uses the environment you already have. The charter still filters
# what reaches the agent, so this file cannot widen it.
if [ -f "$AGENT_DIR/.env" ]; then
  set -a
  . "$AGENT_DIR/.env"
  set +a
fi

{exec_block}
"""

EXEC_AGENT_PY = """exec "$VENV_PY" "$AGENT_DIR/agent.py" "$@"
"""

# run_headless.py takes --trigger as a flag, but every builder's shim must accept the same
# `run [trigger] [flags]` interface, so translate the leading positional here.
EXEC_HEADLESS = """trigger=manual
if [ $# -gt 0 ]; then
  case "$1" in
    -*) ;;
    *) trigger="$1"; shift ;;
  esac
fi

exec "$VENV_PY" '{driver}' \\
  --charter "$AGENT_DIR/charter.yaml" \\
  --trigger "$trigger" \\
  --eval "$@"
"""


def write_shim(agent_dir, runtime, req, binaries):
    spec = RUNTIMES[runtime]
    if spec["entry"] == "run_headless":
        driver = os.path.join(REPO_ROOT, "builders", "claude-headless", "run_headless.py")
        exec_block = EXEC_HEADLESS.format(driver=driver)
    else:
        if not os.path.exists(os.path.join(agent_dir, "agent.py")):
            fail(f"{agent_dir}/agent.py not found — generate the agent before provisioning it")
        exec_block = EXEC_AGENT_PY

    # Explicit PATH: the dirs of the binaries we resolved, plus the base system dirs. Nothing
    # is inherited, so cron and an interactive shell see the same thing.
    dirs = []
    for p in binaries.values():
        d = os.path.dirname(p)
        if d not in dirs:
            dirs.append(d)
    path = ":".join([*dirs, "/usr/bin", "/bin"])

    shim = SHIM.format(
        agent_id=os.path.basename(agent_dir.rstrip(os.sep)),
        runtime=runtime,
        agent_dir=agent_dir,
        venv_py=venv_python(agent_dir),
        req_file=req,
        req_rel=os.path.relpath(req, REPO_ROOT),
        req_sha=sha256(req),
        path=path,
        repo_root=REPO_ROOT,
        exec_block=exec_block,
    )
    out = os.path.join(agent_dir, "run")
    with open(out, "w") as f:
        f.write(shim)
    # Owner + group only. World-execute buys nothing here: cron and launchd run the shim as a
    # user who already owns the agent dir, and this file bakes in that user's paths.
    os.chmod(out, os.stat(out).st_mode | stat.S_IXUSR | stat.S_IXGRP)  # noqa: S103
    return out


# --- the friendly part ----------------------------------------------------------------------
def summary(agent_dir, agent_id, runtime, run_path, missing_creds):
    logs = os.path.join(agent_dir, "logs")
    print(f"\n✅ {agent_id} provisioned ({runtime})\n")
    print("  Run it")
    print(f"      {run_path}")
    print(f"      {run_path} --dry-run      # enforcement report, no tokens spent\n")
    print("  Read what it produced  (logs/ appears on the first real run)")
    print(f"      {os.path.join(logs, 'runs.jsonl')}")
    print("          one line per run: outcome, cost, duration, eval results, output file")
    print(f"      {os.path.join(logs, '<timestamp>.output.txt')}")
    print("          the agent's full response, after redaction")
    if RUNTIMES[runtime]["trace"]:
        print(f"      {os.path.join(logs, '<timestamp>.trace.jsonl')}")
        print("          per-tool-call trace, when extensions.observability.trace is on")
    print("\n  Latest output at any time")
    print(f"      ls -t {os.path.join(logs, '*.output.txt')} | head -1 | xargs cat")
    print("\n  Last 5 runs at a glance")
    print(f"      tail -5 {os.path.join(logs, 'runs.jsonl')}")
    if missing_creds:
        print(f"\n  ⚠  Not set in this shell: {', '.join(missing_creds)}")
        print("     Export them before running, or for unattended runs put them in")
        print(f"     {os.path.join(agent_dir, '.env')} (chmod 600) as KEY=value lines.")
    print("\n  Re-provision after changing dependencies")
    print(f"      python3 {os.path.join(REPO_ROOT, 'core', 'provision.py')} {agent_dir}")
    print("\n  Remove everything this created (nothing was installed outside the agent dir)")
    print(f"      rm -rf {os.path.join(agent_dir, '.venv')} {run_path}\n")


def main():
    ap = argparse.ArgumentParser(
        description="Provision a generated agent: build its .venv and write its run shim."
    )
    ap.add_argument("agent_dir", help="path to builders/<builder>/agents/<id>")
    ap.add_argument("--force", action="store_true", help="rebuild .venv from scratch")
    ap.add_argument("--no-smoke", action="store_true", help="skip the --dry-run smoke test")
    args = ap.parse_args()

    agent_dir = os.path.realpath(args.agent_dir)
    if not os.path.isdir(agent_dir):
        fail(f"{args.agent_dir} is not a directory")
    agent_id = os.path.basename(agent_dir)

    runtime = read_runtime(agent_dir)
    if not runtime:
        fail(
            f"cannot tell which runtime {agent_id!r} is for — charter.yaml has no known "
            f"`runtime:` and the path is not under a builder. Known: {', '.join(RUNTIMES)}"
        )

    print(f"provisioning {agent_id} ({runtime})")
    req, hashed = find_requirements(agent_dir, runtime)
    binaries = resolve_binaries(RUNTIMES[runtime]["binaries"])
    build_venv(agent_dir, req, hashed, args.force)
    run_path = write_shim(agent_dir, runtime, req, binaries)
    print(f"  wrote                   {run_path}")

    if not args.no_smoke:
        r = subprocess.run([run_path, "--dry-run"], capture_output=True, text=True)  # noqa: S603
        if r.returncode != 0:
            fail(
                f"smoke test failed — `{run_path} --dry-run` exited {r.returncode}:\n"
                f"{(r.stderr or r.stdout).strip()}"
            )
        print("  smoke test              --dry-run ok")

    missing = [v for v in read_credential_vars(agent_dir) if not os.environ.get(v)]
    summary(agent_dir, agent_id, runtime, run_path, missing)


if __name__ == "__main__":
    main()
