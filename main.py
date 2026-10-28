"""CLI entry point: wires memory + tools + LLM into an agent and runs a REPL."""

import os
import sys
from agent import Agent, LLM, Memory, ToolRegistry
from tools_builtin import build_tools


def main():
    memory = Memory(path=os.environ.get("AGENT_MEMORY_DIR", "memory"))
    registry = ToolRegistry()
    build_tools(registry, memory,
                workdir=os.environ.get("AGENT_WORKDIR", "workspace"),
                allow_network=os.environ.get("AGENT_ALLOW_NETWORK", "1") != "0")
    agent = Agent(name=os.environ.get("AGENT_NAME", "AgentKit"), llm=LLM(),
                  memory=memory, registry=registry,
                  extra=os.environ.get("AGENT_SYSTEM", ""))
    if len(sys.argv) > 1:
        print(agent.run(" ".join(sys.argv[1:])))
        return
    agent.chat()


if __name__ == "__main__":
    main()
