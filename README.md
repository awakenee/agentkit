# AgentKit

A small, readable AI agent framework - **tool registry + persistent memory + a think/tool/answer loop** - with two front-ends: a CLI and a Telegram bot.

## Run the CLI

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
python main.py                        # interactive REPL
python main.py "summarize notes.txt"  # one-shot
```

## Run the Telegram bot

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=123456:ABC...
export OPENAI_API_KEY=sk-...
python bot.py
```

Talk to it on Telegram. Commands: `/start`, `/help`, `/facts`, `/forget <key>`, `/reset`.
Each Telegram user gets isolated memory under `memory/users/<id>/`.

## What is inside

- `agent.py` - `ToolRegistry`, `Memory`, `LLM`, `Agent` (the loop).
- `tools_builtin.py` - built-in tools: `remember`, `recall`, `forget`, `search_history`,
  `now`, `calculate`, `read_file`, `write_file`, `list_files`, `http_get`.
- `main.py` - CLI front-end.
- `bot.py` - Telegram front-end (python-telegram-bot v20+).

## Add your own tool

```python
from agent import ToolRegistry

registry = ToolRegistry()

@registry.tool(
    name="weather",
    description="Get current weather for a city.",
    parameters={"type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"]})
def weather(city: str) -> str:
    return f"12C and raining in {city}"
```

Register it on the agent's registry and the model will start using it.

## Swap the model

Any OpenAI-compatible endpoint works (OpenAI, Groq, Together, Ollama, vLLM):

```bash
export OPENAI_BASE_URL=http://localhost:11434/v1
export AGENT_MODEL=llama3
```

## Tune the personality

```bash
export AGENT_NAME="Aria"
export AGENT_SYSTEM="Be concise. Use British English. Never use emoji."
```

## Notes

- Memory is plain JSON/JSONL on disk, so run it on a persistent host (VPS, container).
- The bot uses long-polling; give it a container restart policy.
- Never commit API keys - `.gitignore` already excludes `.env` and `memory/`.
