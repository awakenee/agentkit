"""Smoke tests for AgentKit - no network, no API keys."""

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from agent import Agent, Memory, ToolRegistry
from health import HealthServer
from tools_builtin import build_tools


class FakeLLM:
    """Returns a scripted sequence of chat responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append((messages, tools))
        if not self.responses:
            return {"choices": [{"message": {"content": "done"}}]}
        return self.responses.pop(0)


def tool_call_response(name, args, call_id="c1"):
    return {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": call_id, "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


def text_response(text):
    return {"choices": [{"message": {"content": text}}]}


class AgentKitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = os.path.join(self.tmp, "ws")
        self.memory = Memory(path=os.path.join(self.tmp, "memory"))
        self.registry = ToolRegistry()
        build_tools(self.registry, self.memory, workdir=self.ws)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_registry_has_builtin_tools(self):
        names = {t.name for t in self.registry.all()}
        for expected in ("remember", "recall", "calculate", "read_file", "write_file"):
            self.assertIn(expected, names)

    def test_memory_roundtrip(self):
        self.memory.remember("name", "Ada")
        self.assertEqual(self.memory.recall("name"), {"name": "Ada"})
        self.memory.forget("name")
        self.assertEqual(self.memory.recall(), {})

    def test_memory_clear_keeps_facts(self):
        self.memory.remember("name", "Ada")
        self.memory.add("user", "hello")
        self.memory.clear()
        self.assertEqual(self.memory.recent(), [])
        self.assertEqual(self.memory.recall(), {"name": "Ada"})

    def test_corrupt_history_line_is_skipped(self):
        with open(self.memory.hist_path, "w", encoding="utf-8") as f:
            f.write('{"role": "user", "content": "one"}\n{"broken\n')
        self.assertEqual(len(self.memory.recent()), 1)

    def test_calculate_tool(self):
        out = self.registry.call("calculate", {"expression": "(1250*0.08)+300"})
        self.assertEqual(out, "400.0")

    def test_calculate_blocks_code(self):
        out = self.registry.call("calculate", {"expression": "__import__('os').system('ls')"})
        self.assertIn("Calc error", out)

    def test_calculate_blocks_power_bombs(self):
        for expr in ("9**9**9", "10**10000", "2**99999"):
            self.assertIn("Calc error", self.registry.call("calculate", {"expression": expr}), expr)
        self.assertEqual(self.registry.call("calculate", {"expression": "2**10"}), "1024")

    def test_file_tools(self):
        self.registry.call("write_file", {"path": "a.txt", "content": "hello"})
        self.assertEqual(self.registry.call("read_file", {"path": "a.txt"}), "hello")
        self.assertIn("a.txt", self.registry.call("list_files"))

    def test_file_tools_cannot_escape_workspace(self):
        self.assertIn("Error", self.registry.call("read_file", {"path": "/etc/passwd"}))
        self.assertIn("Error", self.registry.call("write_file",
                                                  {"path": "../escaped.txt", "content": "x"}))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escaped.txt")))

    def test_http_get_blocks_internal_addresses(self):
        for url in ("http://127.0.0.1:8000/", "http://169.254.169.254/latest/meta-data/",
                    "file:///etc/passwd", "gopher://example.com/"):
            out = self.registry.call("http_get", {"url": url})
            self.assertIn("HTTP error", out, url)

    def test_http_get_can_be_disabled(self):
        reg = ToolRegistry()
        build_tools(reg, self.memory, workdir=self.ws, allow_network=False)
        self.assertIn("disabled", reg.call("http_get", {"url": "https://example.com"}))

    def test_registry_call_without_args(self):
        self.registry.call("write_file", {"path": "a.txt", "content": "hi"})
        self.assertIn("a.txt", self.registry.call("list_files"))

    def test_agent_calls_tool_then_answers(self):
        llm = FakeLLM([
            tool_call_response("remember", {"key": "lang", "value": "Python"}),
            text_response("Noted."),
        ])
        agent = Agent(name="Test", llm=llm, memory=self.memory, registry=self.registry)
        answer = agent.run("remember that my language is Python")
        self.assertEqual(answer, "Noted.")
        self.assertEqual(self.memory.recall("lang"), {"lang": "Python"})

    def test_user_message_sent_once(self):
        llm = FakeLLM([text_response("hi")])
        agent = Agent(name="Test", llm=llm, memory=self.memory, registry=self.registry)
        agent.run("hello")
        sent = llm.calls[0][0]
        self.assertEqual([m["role"] for m in sent], ["system", "user"])
        self.assertEqual([m["content"] for m in sent if m["role"] == "user"], ["hello"])

    def test_agent_reports_llm_error(self):
        class Boom:
            def chat(self, messages, tools=None):
                raise RuntimeError("no key")
        agent = Agent(name="Test", llm=Boom(), memory=self.memory, registry=self.registry)
        self.assertIn("llm error", agent.run("hi"))

    def test_unknown_tool_returns_error(self):
        self.assertIn("Error", self.registry.call("nope", {}))

    def test_system_prompt_tolerates_braces(self):
        # AGENT_SYSTEM may contain JSON or braces; that must not crash the prompt.
        agent = Agent(name="Test", system="You are {name}. Reply as {json: true}.",
                      llm=FakeLLM([]), memory=self.memory, registry=self.registry)
        self.assertIn("You are Test", agent._system_prompt())

    def test_extra_instructions_reach_prompt(self):
        llm = FakeLLM([text_response("ok")])
        agent = Agent(name="Test", llm=llm, memory=self.memory, registry=self.registry,
                      extra="Always answer in Arabic.")
        agent.run("hi")
        self.assertIn("Always answer in Arabic.", llm.calls[0][0][0]["content"])


class HealthServerTests(unittest.TestCase):
    def test_health_endpoint(self):
        server = HealthServer(0, state={"bot": "running"}).start()
        try:
            port = server._httpd.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                body = json.loads(r.read())
            self.assertEqual(r.status, 200)
            self.assertEqual(body["status"], "ok")
            self.assertEqual(body["bot"], "running")
        finally:
            server.stop()

    def test_unknown_path_404(self):
        server = HealthServer(0).start()
        try:
            port = server._httpd.server_address[1]
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
            self.assertEqual(ctx.exception.code, 404)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
