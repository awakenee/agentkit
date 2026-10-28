"""AgentKit - a small, readable AI agent framework with tools and memory.

Core loop:  think -> pick a tool -> run it -> feed the result back -> repeat.
Memory:     long-term facts (JSON) + rolling conversation history (JSONL).
"""

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    func: Callable[..., Any]
    returns: str = "string"

    def schema(self):
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        self._tools[tool.name] = tool
        return tool

    def tool(self, name=None, description=None, parameters=None, returns="string"):
        def deco(fn):
            self.register(Tool(
                name=name or fn.__name__,
                description=description or (fn.__doc__ or "").strip(),
                parameters=parameters or {"type": "object", "properties": {}},
                func=fn, returns=returns))
            return fn
        return deco

    def get(self, name):
        return self._tools.get(name)

    def all(self):
        """Every registered tool, in registration order."""
        return list(self._tools.values())

    def schemas(self):
        return [t.schema() for t in self._tools.values()]

    def call(self, name, args=None):
        args = args or {}
        t = self.get(name)
        if not t:
            return f"Error: no tool named '{name}'."
        try:
            out = t.func(**args)
        except TypeError as e:
            return f"Error: bad arguments for '{name}': {e}"
        except Exception as e:
            return f"Error while running '{name}': {type(e).__name__}: {e}"
        if isinstance(out, str):
            return out
        try:
            return json.dumps(out, ensure_ascii=False, default=str)
        except Exception:
            return str(out)


class Memory:
    """Long-term facts + conversation history, both on disk as JSON/JSONL."""

    def __init__(self, path="memory"):
        os.makedirs(path, exist_ok=True)
        self.path = path
        self.facts_path = os.path.join(path, "facts.json")
        self.hist_path = os.path.join(path, "history.jsonl")
        self._facts = {}
        if os.path.exists(self.facts_path):
            try:
                with open(self.facts_path, encoding="utf-8") as f:
                    self._facts = json.load(f)
            except Exception:
                self._facts = {}

    def remember(self, key, value):
        self._facts[key] = value
        self._save_facts()
        return f"Remembered '{key}'."

    def forget(self, key):
        if key in self._facts:
            del self._facts[key]
            self._save_facts()
            return f"Forgot '{key}'."
        return f"No fact named '{key}'."

    def recall(self, query=None):
        if not query:
            return dict(self._facts)
        q = query.lower()
        return {k: v for k, v in self._facts.items() if q in k.lower() or q in str(v).lower()}

    def _save_facts(self):
        # Write-then-rename so a crash mid-write cannot truncate stored facts.
        tmp = self.facts_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._facts, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.facts_path)

    def add(self, role, content, **meta):
        rec = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "role": role, "content": content}
        rec.update(meta)
        with open(self.hist_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def recent(self, n=20):
        return self._rows()[-n:]

    def _rows(self):
        if not os.path.exists(self.hist_path):
            return []
        rows = []
        with open(self.hist_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue  # skip a half-written line instead of losing the chat
        return rows

    def clear(self):
        """Delete conversation history, keep long-term facts."""
        with open(self.hist_path, "w", encoding="utf-8") as f:
            f.truncate(0)
        return "Conversation history cleared."

    def search_history(self, query, n=10):
        hits = [r for r in self._rows() if query.lower() in str(r.get("content", "")).lower()]
        return hits[-n:]

    def summary(self):
        lines = [f"- {k}: {v}" for k, v in self.recall().items()]
        return "\n".join(lines) if lines else "(no facts stored yet)"


class LLM:
    """Thin OpenAI-compatible client. Set OPENAI_API_KEY and OPENAI_BASE_URL."""

    def __init__(self, model=None, base_url=None, api_key=None, temperature=0.4):
        self.model = model or os.environ.get("AGENT_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.temperature = temperature

    def chat(self, messages, tools=None):
        if not self.api_key:
            raise RuntimeError("Set OPENAI_API_KEY (or pass api_key) before running the agent.")
        import urllib.error
        import urllib.request
        payload = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        data = json.dumps(payload).encode("utf-8")
        last_err = None
        for attempt in range(3):
            req = urllib.request.Request(
                self.base_url + "/chat/completions", data=data,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
                method="POST")
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:500]
                last_err = RuntimeError(f"HTTP {e.code} from model endpoint: {body}")
                if e.code != 429 and e.code < 500:
                    raise last_err from None  # a bad key or bad request will not fix itself
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        raise last_err


def fill(template, **values):
    """Replace only the placeholders we know about.

    str.format() would raise KeyError on any stray brace in a user-supplied
    system prompt (a JSON snippet, say), so substitution is literal instead.
    """
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


SYSTEM_TEMPLATE = """You are {name}, a capable AI agent with tools and memory.

Tools you can call:
{tool_list}

Long-term memory you already know:
{memory}

Rules:
- Use a tool whenever the answer depends on real data, files, or a calculation.
- Call one tool at a time, read its result, then decide the next step.
- Keep answers concise and concrete. No filler, no restating the question.
{extra}
"""


class Agent:
    def __init__(self, name="AgentKit", system=None, llm=None, memory=None,
                 registry=None, max_steps=8, extra=""):
        self.name = name
        self.registry = registry or ToolRegistry()
        self.memory = memory or Memory()
        self.llm = llm or LLM()
        self.max_steps = max_steps
        self.system = system or SYSTEM_TEMPLATE
        self.extra = extra

    def _system_prompt(self):
        tools = "\n".join(
            f"- {t.name}: {t.description} (params: {json.dumps(t.parameters.get('properties', {}), ensure_ascii=False)[:300]})"
            for t in self.registry.all()) or "(none)"
        return fill(self.system, name=self.name, tool_list=tools,
                    memory=self.memory.summary(), extra=self.extra)

    def _messages(self, user_input):
        msgs = [{"role": "system", "content": self._system_prompt()}]
        for r in self.memory.recent(20):
            msgs.append({"role": r["role"], "content": r["content"]})
        msgs.append({"role": "user", "content": user_input})
        return msgs

    def run(self, user_input):
        # Build first, then record: recording first would replay the same
        # message twice (once from history, once as the trailing user turn).
        msgs = self._messages(user_input)
        self.memory.add("user", user_input)
        for _ in range(self.max_steps):
            try:
                resp = self.llm.chat(msgs, tools=self.registry.schemas() or None)
            except Exception as e:
                return f"[llm error] {type(e).__name__}: {e}"
            msg = resp["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            if not calls:
                text = (msg.get("content") or "").strip()
                self.memory.add("assistant", text)
                return text or "I had nothing to say."
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls:
                fname = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except Exception:
                    args = {}
                out = self.registry.call(fname, args)
                msgs.append({"role": "tool", "tool_call_id": call["id"], "name": fname,
                             "content": str(out)[:8000]})
        return "I hit the step limit before answering."

    def chat(self):
        print(f"{self.name} ready. Type 'exit' to quit.\n")
        while True:
            try:
                line = input("you > ").strip()
            except (EOFError, KeyboardInterrupt):
                print(); break
            if line.lower() in ("exit", "quit", ":q"):
                break
            if not line:
                continue
            print(f"{self.name} > {self.run(line)}\n")
