"""Telegram bot front-end for AgentKit.

Same brain (Agent + tools + memory), Telegram as the interface.
Per-user memory: memory/users/<telegram_id>/ so each user has their own facts and history.

Run:
    export TELEGRAM_BOT_TOKEN=123:ABC
    export OPENAI_API_KEY=sk-...
    python bot.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

from agent import Agent, LLM, Memory, ToolRegistry
from tools_builtin import build_tools

BASE_MEMORY = os.environ.get("AGENT_MEMORY_DIR", "memory")
AGENT_NAME = os.environ.get("AGENT_NAME", "AgentKit")
SYSTEM_EXTRA = os.environ.get("AGENT_SYSTEM", "")

SYSTEM_TEMPLATE = """You are {name}, a warm and capable AI agent, chatting over Telegram.

Tools you can call:
{tool_list}

Long-term memory you already know:
{memory}

Rules:
- Use a tool whenever the answer depends on real data, files, or a calculation.
- Call one tool at a time, read its result, then decide the next step.
- Keep answers short and readable on a phone. No filler, no restating the question.
- Telegram formatting is supported: *bold*, _italic_, `code`.
{extra}
"""

_agents = {}


def get_agent(user_id: int) -> Agent:
    """One agent instance per Telegram user, with isolated memory."""
    if user_id in _agents:
        return _agents[user_id]
    base = os.path.join(BASE_MEMORY, "users", str(user_id))
    mem = Memory(path=base)
    reg = ToolRegistry()
    build_tools(reg, mem, workdir=os.path.join(base, "workspace"))
    agent = Agent(
        name=AGENT_NAME,
        llm=LLM(),
        memory=mem,
        registry=reg,
        system=SYSTEM_TEMPLATE.replace("{extra}", SYSTEM_EXTRA),
    )
    _agents[user_id] = agent
    return agent


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Hi {update.effective_user.first_name}! I'm {AGENT_NAME}.\n"
        "I remember things between chats and I can use tools - files, memory, calculations, web.\n\n"
        "Just talk to me.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agent = get_agent(update.effective_user.id)
    names = ", ".join(t.name for t in agent.registry._tools.values())
    await update.message.reply_text(
        f"Available tools:\n{names}\n\nCommands:\n"
        "/facts - what I remember about you\n/forget <key> - delete a memory\n/reset - clear conversation history")


async def facts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agent = get_agent(update.effective_user.id)
    hits = agent.memory.recall()
    if not hits:
        await update.message.reply_text("I don't remember anything about you yet.")
        return
    lines = [f"- {k}: {v}" for k, v in hits.items()]
    await update.message.reply_text("What I remember:\n" + "\n".join(lines))


async def forget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Give me the key to forget, e.g. /forget name")
        return
    agent = get_agent(update.effective_user.id)
    await update.message.reply_text(agent.memory.forget(" ".join(context.args)))


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agent = get_agent(update.effective_user.id)
    if os.path.exists(agent.memory.hist_path):
        open(agent.memory.hist_path, "w").close()
    await update.message.reply_text("Conversation history cleared.")


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agent = get_agent(update.effective_user.id)
    text = update.message.text or update.message.caption or ""
    if not text.strip():
        return
    try:
        await context.bot.send_chat_action(update.effective_chat.id, "typing")
        answer = agent.run(text)
    except Exception:
        traceback.print_exc()
        answer = "Something broke on my side. Try again?"
    await update.message.reply_text(answer[:4000])


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN first.")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("facts", facts))
    app.add_handler(CommandHandler("forget", forget_cmd))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    print(f"{AGENT_NAME} bot is running.")
    app.run_polling()


if __name__ == "__main__":
    main()
