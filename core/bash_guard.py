"""bash_guard — narrow a granted `Bash` down to one command shape the charter declared.

WHY THIS EXISTS. Granting `Bash` used to void a scoped `egress` silently. The egress hook gates
`WebFetch|WebSearch`; `curl` is neither, so `egress: [api.example.com]` plus `tools: [Bash]`
described an agent confined to one host and produced an agent that could reach the internet. The
enforcement report said so honestly (`none  egress.other`) but nothing could close it. A charter
you can only declare as broken is not much of a charter.

WHAT IT DOES. Given a charter's `bash_allow` list, permit exactly one shape:

    curl [safe flags] '<url>'

with the URL's host and path matched against the allow-list, one URL per command, no shell
plumbing, and an allow-list of flags. Everything else is denied. Default-deny is deliberate: the
failure mode of a bug in here is an agent that cannot run its command, never an open shell.

WHAT IT IS NOT. It is not a sandbox. It reduces `Bash` from "anything" to "these endpoints", which
is worth having, but a process that can still be handed a crafted response body is not isolated —
that needs a container, and the report keeps saying so.

Pure and dependency-free so all three runtimes can share it: headless imports it, and the
self-contained builders inline it the way they inline core/eval_checks.py.
"""

import shlex
from urllib.parse import urlparse

# Flags that take no argument and cannot reach the filesystem, the network path, or TLS.
_SAFE_FLAGS = {
    "-s",
    "--silent",
    "-S",
    "--show-error",
    "-f",
    "--fail",
    "-i",
    "--include",
    "-v",
    "--verbose",
    "-g",
    "--globoff",
    "--compressed",
    "--http1.1",
    "--http2",
}

# Flags that take exactly one argument, where the argument is inspected below.
_SAFE_FLAGS_WITH_VALUE = {
    "-H",
    "--header",
    "-A",
    "--user-agent",
    "-e",
    "--referer",
    "-X",
    "--request",
    "-d",
    "--data",
    "--data-raw",
    "--data-urlencode",
    "--json",
    "-m",
    "--max-time",
    "--connect-timeout",
    "--retry",
}

# Characters that are shell control OUTSIDE quotes. Inside single quotes every one of them is an
# ordinary character; a scanner that does not know that denies every real API URL, because query
# strings are full of & and ;.
_SHELL_META = set("&;|<>()\n\r")


def _unquoted_metachar(command):
    """The first shell metacharacter that is NOT inside quotes, or None.

    Walks the string tracking quote state, because quoting is the whole question. Single quotes
    make everything inert. Double quotes still expand `$(`, backtick, and `${`, so those are
    treated as control even inside them.
    """
    in_single = in_double = False
    i = 0
    while i < len(command):
        ch = command[i]
        if in_single:
            if ch == "'":
                in_single = False
        elif in_double:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_double = False
            elif ch == "`":
                return "`"
            elif ch == "$" and i + 1 < len(command) and command[i + 1] in "({":
                return "$" + command[i + 1]
        else:
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif ch == "\\":
                i += 2
                continue
            elif ch == "`":
                return "`"
            elif ch == "$" and i + 1 < len(command) and command[i + 1] in "({":
                return "$" + command[i + 1]
            elif ch in _SHELL_META:
                return ch
        i += 1
    if in_single or in_double:
        return "unbalanced quote"
    return None


def _endpoint_ok(url, allow):
    """(ok, reason) for one URL against the allow-list.

    Host and path come from urlparse, NEVER a substring search: `https://good.example@evil.tld/x`
    contains the allowed host as a substring while its actual hostname is evil.tld, and
    `good.example.evil.tld` ends with it. urlparse is the only thing that reads these the way the
    network stack will.
    """
    try:
        u = urlparse(url)
    except ValueError as e:
        return False, f"URL could not be parsed ({e})"

    if u.scheme != "https":
        return False, f"scheme {u.scheme or '(none)'!r} is not https"
    if u.port is not None:
        return False, f"explicit port {u.port} is not permitted"
    if u.username or u.password:
        return False, "URL carries userinfo (user@host), which hides the real hostname"

    host = (u.hostname or "").lower()
    path = u.path or "/"
    if ".." in path:
        return False, "path contains '..', which can climb out of the permitted prefix"

    for entry in allow:
        if host != (entry.get("host") or "").lower():
            continue
        prefix = entry.get("path") or "/"
        # Prefix match on a path BOUNDARY: '/v1/items' must not permit '/v1/itemsX'.
        if path != prefix and not path.startswith(prefix.rstrip("/") + "/"):
            continue
        return True, f"{host}{prefix} is declared in bash_allow"
    return False, f"{host}{path} is not declared in bash_allow"


def _method_ok(method, url, allow):
    for entry in allow:
        u = urlparse(url)
        if (u.hostname or "").lower() != (entry.get("host") or "").lower():
            continue
        methods = [m.upper() for m in (entry.get("methods") or ["GET"])]
        if method.upper() in methods:
            return True, ""
        return False, f"method {method.upper()} not in {methods} for this endpoint"
    return False, "no matching endpoint"


def bash_decision(command, allow):
    """(allow: bool, reason: str) — may this Bash command run under this charter's bash_allow?

    The reason is fed back to the model on a denial, so it says what was wrong specifically
    enough to be actionable rather than just 'denied'.
    """
    allow = allow or []
    if not allow:
        return False, (
            "Bash is granted but the charter declares no bash_allow endpoints, so no command "
            "is permitted. Add bash_allow to the charter, or drop Bash from tools."
        )
    if not command or not command.strip():
        return False, "empty command"

    meta = _unquoted_metachar(command)
    if meta:
        return False, (
            f"command contains unquoted shell control {meta!r}; only a single plain curl is "
            "permitted, with no pipes, redirection, chaining, or substitution"
        )

    try:
        parts = shlex.split(command)
    except ValueError as e:
        return False, f"command could not be parsed as a shell command ({e})"
    if not parts:
        return False, "empty command"

    if parts[0] != "curl":
        return False, (
            f"only `curl` is permitted here, not {parts[0]!r}. bash_allow narrows Bash to "
            "fetching the endpoints the charter declares; it is not a general shell."
        )

    urls, method = [], "GET"
    i = 1
    while i < len(parts):
        tok = parts[i]
        if tok in _SAFE_FLAGS:
            i += 1
            continue
        if tok in _SAFE_FLAGS_WITH_VALUE:
            if i + 1 >= len(parts):
                return False, f"flag {tok} has no value"
            val = parts[i + 1]
            if tok in ("-X", "--request"):
                method = val
            if tok in ("-d", "--data", "--data-raw", "--data-urlencode", "--json"):
                if val.startswith("@"):
                    return False, (
                        f"{tok} {val!r} reads a local file into the request; that is a way to "
                        "exfiltrate anything readable and is never permitted"
                    )
            i += 2
            continue
        if tok in ("-L", "--location"):
            # Deliberately not on the safe list. curl follows the redirect itself, so the hop
            # lands wherever the response says and this gate never sees the second URL — the
            # allow-list would be checked against the address the agent asked for, not the one it
            # reached. The langchain builder re-checks every hop for the same reason; here we
            # cannot, so we refuse instead of pretending.
            return False, (
                f"{tok} follows redirects, and the redirect target is never checked against "
                "bash_allow — the request could land on any host. Fetch the final URL directly."
            )
        if tok.startswith("-"):
            return False, (
                f"flag {tok!r} is not on the permitted list. Flags are allow-listed because curl "
                "has hundreds and several of them write files, disable TLS checks, or redirect "
                "the request around the declared endpoint."
            )
        urls.append(tok)
        i += 1

    if not urls:
        return False, "no URL in the command"
    if len(urls) > 1:
        return False, (
            f"{len(urls)} URLs in one command; only one is permitted, because a second URL is "
            "how a fetched result gets sent somewhere the charter never declared"
        )

    ok, why = _endpoint_ok(urls[0], allow)
    if not ok:
        return False, f"endpoint refused: {why}"
    ok, why = _method_ok(method, urls[0], allow)
    if not ok:
        return False, f"method refused: {why}"
    return True, f"curl to a declared endpoint ({urls[0]})"
