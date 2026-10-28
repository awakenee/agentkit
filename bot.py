"""Telegram bot front-end for AgentKit.

Same brain (Agent + tools + memory), Telegram as the interface.
Per-user memory: memory/users/<telegram_id>/ so each user has their own facts and history.

Run:
    export TELEGRAM_BOT_TOKEN=123:ABC
    export OPENAI_API_KEY=sk-...
    python bot.py

Preflight (no network, no Telegram calls):
    python bot.py --check

On a PaaS, set PORT and the bot also serves GET /health on that port.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import Agent, LLM, Memory, ToolRegistry
from health import HealthServer, port_from_env
from tools_builtin import build_tools

BASE_MEMORY = os.environ.get("AGENT_MEMORY_DIR", "memory")
AGENT_NAME = os.environ.get("AGENT_NAME", "AgentKit")
SYSTEM_EXTRA = os.environ.get("AGENT_SYSTEM", "")
AGENT_WORKDIR = os.environ.get("AGENT_WORKDIR", "workspace")
MAX_USERS_CACHED = 200

SYSTEM_TEMPLATE = """You are {name}, a warm and capable AI agent, chatting over Telegram.

Tools you can call:
{tool_list}

Long-term memory you already know:
{memory}

Rules:
- Use a tool whenever the answer depends on real data, files, or a calculation.
- Call one tool at a time, read its result, then decide the next step.
- Keep answers short and readable on a phone. No filler, no restating the question.
- Telegram formatting is supported: *bold*, _italic_, `code`. Use it sparingly.
{extra}
"""

_agents: "OrderedDict[int, Agent]" = OrderedDict()
_locks: dict = {}
STATE = {"bot": "starting", "handled": 0, "last_activity": None}


def allowed_users():
    raw = os.environ.get("AGENT_ALLOWED_USERS", "").strip()
    if not raw:
        return None
    return {part.strip() for part in raw.split(",") if part.strip()}


def is_allowed(user_id) -> bool:
    allow = allowed_users()
    return allow is None or str(user_id) in allow


def get_agent(user_id: int) -> Agent:
    """One agent per Telegram user, with isolated memory."""
    if user_id in _agents:
        _agents.move_to_end(user_id)
        return _agents[user_id]
    base = os.path.join(BASE_MEMORY, "users", str(user_id))
    mem = Memory(path=base)
    reg = ToolRegistry()
    build_tools(reg, mem, workdir=os.path.join(base, "workspace"),
                allow_network=os.environ.get("AGENT_ALLOW_NETWORK", "1") != "0")
    agent = Agent(name=AGENT_NAME, llm=LLM(), memory=mem, registry=reg,
                  system=SYSTEM_TEMPLATE, extra=SYSTEM_EXTRA)
    _agents[user_id] = agent
    while len(_agents) > MAX_USERS_CACHED:
        _agents.popitem(last=False)
    return agent


def _lock_for(user_id: int) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = _locks[user_id] = asyncio.Lock()
    return lock


async def _reply(update, text, markdown=True):
    """Send a reply, falling back to plain text if Telegram rejects the markdown."""
    text = text[:4000]
    if markdown:
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
            return
        except Exception:
            pass  # unbalanced *_ in model output is normal; send it literally
    await update.message.reply_text(text)


async def start(update, context):
    await _reply(update,
                 f"Hi {update.effective_user.first_name}! I'm {AGENT_NAME}.\n"
                 "I remember things between chats and I can use tools - files, memory, "
                 "calculations, web.\n\nJust talk to me.")


async def help_cmd(update, context):
    agent = get_agent(update.effective_user.id)
    names = ", ".join(t.name for t in agent.registry.all())
    await _reply(update,
                 f"Available tools:\n{names}\n\nCommands:\n"
                 "/facts - what I remember about you\n"
                 "/forget <key> - delete a memory\n"
                 "/reset - clear conversation history", markdown=False)


async def facts(update, context):
    agent = get_agent(update.effective_user.id)
    hits = agent.memory.recall()
    if not hits:
        await _reply(update, "I don't remember anything about you yet.", markdown=False)
        return
    lines = [f"- {k}: {v}" for k, v in hits.items()]
    await _reply(update, "What I remember:\n" + "\n".join(lines), markdown=False)


async def forget_cmd(update, context):
    if not context.args:
        await _reply(update, "Give me the key to forget, e.g. /forget name", markdown=False)
        return
    agent = get_agent(update.effective_user.id)
    await _reply(update, agent.memory.forget(" ".join(context.args)), markdown=False)


async def reset(update, context):
    agent = get_agent(update.effective_user.id)
    await _reply(update, agent.memory.clear(), markdown=False)


async def chat(update, context):
    if not is_allowed(update.effective_user.id):
        await _reply(update, "This bot is private.", markdown=False)
        return
    text = update.message.text or update.message.caption or ""
    if not text.strip():
        return
    agent = get_agent(update.effective_user.id)
    try:
        await context.bot.send_chat_action(update.effective_chat.id, "typing")
        # agent.run() is blocking HTTP + file IO; keep it off the event loop so
        # one slow user cannot freeze everyone else's chats.
        async with _lock_for(update.effective_user.id):
            answer = await asyncio.to_thread(agent.run, text)
    except Exception:
        traceback.print_exc()
        answer = "Something broke on my side. Try again?"
    STATE["handled"] += 1
    STATE["last_activity"] = update.message.date.isoformat() if update.message.date else None
    await _reply(update, answer)


def preflight() -> int:
    """Validate configuration without contacting Telegram or the model."""
    import re
    ok = True
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    key = os.environ.get("OPENAI_API_KEY", "")
    print(f"agent name      : {AGENT_NAME}")
    print(f"model           : {os.environ.get('AGENT_MODEL', 'gpt-4o-mini')}")
    print(f"base url        : {os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1')}")
    print(f"memory dir      : {os.path.abspath(BASE_MEMORY)}")
    print(f"port            : {port_from_env() or '(none - no health endpoint)'}")
    print(f"allowed users   : {os.environ.get('AGENT_ALLOWED_USERS') or '(everyone)'}")

    if not token or token.startswith("your-"):
        print("FAIL telegram  : TELEGRAM_BOT_TOKEN is not set")
        ok = False
    elif not re.match(r"^\d+:[A-Za-z0-9_-]{30,}$", token):
        print("FAIL telegram  : TELEGRAM_BOT_TOKEN does not look like 123456:ABC...")
        ok = False
    else:
        print(f"ok   telegram  : token for bot id {token.split(':')[0]}")

    if not key:
        print("FAIL model key : OPENAI_API_KEY is not set")
        ok = False
    else:
        print(f"ok   model key : {key[:4]}...{key[-4:]} ({len(key)} chars)")

    try:
        probe = os.path.join(BASE_MEMORY, ".write-test")
        os.makedirs(BASE_MEMORY, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        print("ok   disk      : memory dir is writable")
    except Exception as e:
        print(f"FAIL disk      : cannot write to {BASE_MEMORY}: {e}  (is a volume mounted?)")
        ok = False

    print("\nREADY" if ok else "\nNOT READY - fix the lines marked FAIL")
    return 0 if ok else 1


def main():
    if "--check" in sys.argv:
        raise SystemExit(preflight())

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN first (or run `python bot.py --check`).")
    try:
        from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
    except ImportError:
        raise SystemExit("python-telegram-bot is missing. Run: pip install -r requirements.txt")

    port = port_from_env()
    server = None
    if port:
        STATE["bot"] = "running"
        try:
            server = HealthServer(port, state=STATE).start()
            print(f"health endpoint on 0.0.0.0:{port}/health")
        except OSError as e:
            raise SystemExit(f"cannot bind port {port}: {e}")

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("facts", facts))
    app.add_handler(CommandHandler("forget", forget_cmd))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    print(f"{AGENT_NAME} bot is running (memory: {os.path.abspath(BASE_MEMORY)}).")
    try:
        # drop_pending_updates: after a redeploy, ignore the backlog instead of
        # answering stale messages; stop_signals lets the platform SIGTERM us.
        app.run_polling(drop_pending_updates=True, stop_signals=("SIGINT", "SIGTERM"))
    finally:
        STATE["bot"] = "stopped"
        if server:
            server.stop()


if __name__ == "__main__":
    main()
