"""Smoke tests for AgentKit - no network, no API keys."""

import json
import os
import shutil
import tempfile
import unittest

from agent import Agent, Memory, ToolRegistry
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
        self.memory = Memory(path=os.path.join(self.tmp, "memory"))
        self.registry = ToolRegistry()
        build_tools(self.registry, self.memory, workdir=os.path.join(self.tmp, "ws"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_registry_has_builtin_tools(self):
        names = {t.name for t in self.registry._tools.values()}
        for expected in ("remember", "recall", "calculate", "read_file", "write_file"):
            self.assertIn(expected, names)

    def test_memory_roundtrip(self):
        self.memory.remember("name", "Ada")
        self.assertEqual(self.memory.recall("name"), {"name": "Ada"})
        self.memory.forget("name")
        self.assertEqual(self.memory.recall(), {})

    def test_calculate_tool(self):
        out = self.registry.call("calculate", {"expression": "(1250*0.08)+300"})
        self.assertEqual(out, "400.0")

    def test_calculate_blocks_code(self):
        out = self.registry.call("calculate", {"expression": "__import__('os').system('ls')"})
        self.assertIn("Calc error", out)

    def test_file_tools(self):
        self.registry.call("write_file", {"path": "a.txt", "content": "hello"})
        self.assertEqual(self.registry.call("read_file", {"path": "a.txt"}), "hello")
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

    def test_agent_reports_llm_error(self):
        class Boom:
            def chat(self, messages, tools=None):
                raise RuntimeError("no key")
        agent = Agent(name="Test", llm=Boom(), memory=self.memory, registry=self.registry)
        self.assertIn("llm error", agent.run("hi"))

    def test_unknown_tool_returns_error(self):
        self.assertIn("Error", self.registry.call("nope", {}))


if __name__ == "__main__":
    unittest.main()
