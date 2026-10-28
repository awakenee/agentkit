"""Built-in tools for AgentKit. All local - no API keys needed."""

import ast
import datetime
import ipaddress
import json
import math
import os
import re
import socket
import urllib.parse
import urllib.request


def resolve_within(workdir, path):
    """Absolute path inside `workdir`, or ValueError if it escapes.

    Guards both absolute paths (/etc/passwd) and traversal (../../secrets).
    """
    root = os.path.realpath(workdir)
    candidate = path if os.path.isabs(path) else os.path.join(root, path)
    full = os.path.realpath(candidate)
    if full != root and not full.startswith(root + os.sep):
        raise ValueError(f"path escapes the workspace: {path}")
    return full


def check_public_url(url):
    """Allow http(s) to public hosts only. Blocks SSRF to localhost/cloud metadata."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http:// and https:// URLs are allowed")
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")
    default_port = 443 if parsed.scheme == "https" else 80
    try:
        infos = socket.getaddrinfo(host, parsed.port or default_port,
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError(f"cannot resolve {host}: {e}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            raise ValueError(f"blocked non-public address: {ip}")
    return url


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect hop, so a public URL cannot bounce to 169.254.x.x."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_tools(registry, memory, workdir="./workspace", allow_network=True):
    os.makedirs(workdir, exist_ok=True)
    opener = urllib.request.build_opener(_GuardedRedirect)

    @registry.tool(
        name="remember",
        description="Store a long-term fact about the user or project. Use for preferences, names, goals, context.",
        parameters={"type": "object",
                    "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                    "required": ["key", "value"]})
    def remember(key, value):
        return memory.remember(key, value)

    @registry.tool(
        name="recall",
        description="Search long-term memory for stored facts. Optionally filter by a query.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}})
    def recall(query=""):
        hits = memory.recall(query or None)
        return json.dumps(hits, ensure_ascii=False, indent=2) if hits else "No matching facts."

    @registry.tool(
        name="forget",
        description="Delete a long-term fact by key.",
        parameters={"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]})
    def forget(key):
        return memory.forget(key)

    @registry.tool(
        name="search_history",
        description="Search past conversation turns for a keyword.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})
    def search_history(query):
        hits = memory.search_history(query)
        if not hits:
            return "No matches in history."
        return "\n".join(f"[{h['role']}] {h['content']}" for h in hits)

    @registry.tool(
        name="now",
        description="Get the current date and time.",
        parameters={"type": "object", "properties": {}})
    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    @registry.tool(
        name="calculate",
        description="Evaluate a math expression, e.g. '(1250*0.08)+300'. Safe AST eval.",
        parameters={"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]})
    def calculate(expression):
        allowed = {k: getattr(math, k) for k in
                   ("sqrt", "floor", "ceil", "log", "sin", "cos", "tan", "pi", "e")}
        OK = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Call, ast.Name,
              ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.FloorDiv,
              ast.Mod, ast.USub, ast.UAdd)
        try:
            if len(expression) > 400:
                raise ValueError("expression too long")
            node = ast.parse(expression, mode="eval")
            for n in ast.walk(node):
                if not isinstance(n, OK):
                    raise ValueError(f"blocked node {type(n).__name__}")
                if isinstance(n, ast.Constant) and not isinstance(n.value, (int, float)):
                    raise ValueError("only numbers allowed")
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") not in allowed:
                    raise ValueError("function not allowed")
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow):
                    # Stop `9**9**9**9`: exponent must be a small literal,
                    # and the result must stay small enough to compute.
                    exp = n.right
                    if not isinstance(exp, ast.Constant) or not isinstance(exp.value, (int, float)):
                        raise ValueError("exponent must be a literal number")
                    if abs(exp.value) > 512:
                        raise ValueError("exponent too large")
                    if isinstance(n.left, ast.Constant) and isinstance(n.left.value, (int, float)):
                        if n.left.value not in (0, 1, -1) and abs(exp.value) * math.log10(abs(n.left.value)) > 5000:
                            raise ValueError("result would be too large")
            result = eval(compile(node, "<calc>", "eval"), {"__builtins__": {}}, allowed)
            if isinstance(result, (int, float)) and abs(result) == float("inf"):
                raise ValueError("result too large")
            return str(result)
        except Exception as e:
            return f"Calc error: {e}"

    @registry.tool(
        name="read_file",
        description="Read a text file from the workspace.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})
    def read_file(path):
        try:
            full = resolve_within(workdir, path)
        except ValueError as e:
            return f"Error: {e}"
        if not os.path.exists(full):
            return f"No such file: {path}"
        with open(full, encoding="utf-8", errors="replace") as f:
            return f.read(20000)

    @registry.tool(
        name="write_file",
        description="Write text to a file in the workspace (overwrites).",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]})
    def write_file(path, content):
        try:
            full = resolve_within(workdir, path)
        except ValueError as e:
            return f"Error: {e}"
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {len(content)} chars to {os.path.relpath(full, os.path.realpath(workdir))}"

    @registry.tool(
        name="list_files",
        description="List files in the workspace.",
        parameters={"type": "object", "properties": {}})
    def list_files():
        out = []
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in sorted(dirs) if d != "__pycache__"]
            for fn in sorted(files):
                out.append(os.path.relpath(os.path.join(root, fn), workdir))
                if len(out) >= 500:
                    return "\n".join(out) + "\n... (truncated)"
        return "\n".join(out) or "(empty workspace)"

    @registry.tool(
        name="http_get",
        description="Fetch a public http(s) URL and return the first part of the response body.",
        parameters={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]})
    def http_get(url):
        if not allow_network:
            return "Network access is disabled for this agent (AGENT_ALLOW_NETWORK=0)."
        try:
            check_public_url(url)
            req = urllib.request.Request(url, headers={"User-Agent": "AgentKit/1.0"})
            with opener.open(req, timeout=20) as r:
                body = r.read(20000).decode("utf-8", "replace")
            body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S)
            body = re.sub(r"<[^>]+>", " ", body)
            return re.sub(r"\s+", " ", body).strip()[:4000]
        except Exception as e:
            return f"HTTP error: {e}"

    return registry
