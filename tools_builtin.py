"""Built-in tools for AgentKit. All local - no API keys needed."""

import ast
import datetime
import json
import math
import os
import re


def build_tools(registry, memory, workdir="./workspace"):
    os.makedirs(workdir, exist_ok=True)

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
            node = ast.parse(expression, mode="eval")
            for n in ast.walk(node):
                if not isinstance(n, OK):
                    raise ValueError(f"blocked node {type(n).__name__}")
                if isinstance(n, ast.Constant) and not isinstance(n.value, (int, float)):
                    raise ValueError("only numbers allowed")
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") not in allowed:
                    raise ValueError("function not allowed")
            return str(eval(compile(node, "<calc>", "eval"), {"__builtins__": {}}, allowed))
        except Exception as e:
            return f"Calc error: {e}"

    @registry.tool(
        name="read_file",
        description="Read a text file from the workspace.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})
    def read_file(path):
        full = path if os.path.isabs(path) else os.path.join(workdir, path)
        if not os.path.exists(full):
            return f"No such file: {full}"
        return open(full, encoding="utf-8", errors="replace").read()[:20000]

    @registry.tool(
        name="write_file",
        description="Write text to a file in the workspace (overwrites).",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]})
    def write_file(path, content):
        full = path if os.path.isabs(path) else os.path.join(workdir, path)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {len(content)} chars to {full}"

    @registry.tool(
        name="list_files",
        description="List files in the workspace.",
        parameters={"type": "object", "properties": {}})
    def list_files():
        out = []
        for root, _, files in os.walk(workdir):
            for fn in files:
                out.append(os.path.relpath(os.path.join(root, fn), workdir))
        return "\n".join(out) or "(empty workspace)"

    @registry.tool(
        name="http_get",
        description="Fetch a URL and return the first part of the response body.",
        parameters={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]})
    def http_get(url):
        import urllib.request
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AgentKit/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read(20000).decode("utf-8", "replace")
            body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S)
            body = re.sub(r"<[^>]+>", " ", body)
            return re.sub(r"\s+", " ", body).strip()[:4000]
        except Exception as e:
            return f"HTTP error: {e}"

    return registry
