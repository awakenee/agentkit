# Deploying the Telegram bot

AgentKit's bot uses **long-polling**, which means it never binds a port on its own.
PaaS platforms expect a web service to listen on `$PORT`. That is why the bot now
starts a small health endpoint:

```
GET /health  ->  200 {"status":"ok","service":"agentkit","bot":"running","handled":7}
```

Set `PORT` and you get the endpoint; leave it unset and the bot runs exactly as before.
All three manifests in this repo (`render.yaml`, `railway.json`, `fly.toml`) rely on it.

---

## 0. What you need first

| Thing | Where | Notes |
| --- | --- | --- |
| Telegram bot token | [@BotFather](https://t.me/BotFather) → `/newbot` | Looks like `123456789:AA...` |
| Model API key | OpenAI, Groq, Together, or any OpenAI-compatible endpoint | Set `OPENAI_BASE_URL` if not OpenAI |
| A platform account | Railway / Render / Fly (pick one below) | Card usually required even on free tiers |
| Your Telegram user id | [@userinfobot](https://t.me/userinfobot) | Only needed for `AGENT_ALLOWED_USERS` |

Validate everything locally *before* spending a deploy cycle:

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=123456:AA...
export OPENAI_API_KEY=sk-...
python bot.py --check
```

`--check` contacts nothing. It verifies the token shape, the model key, that the
memory directory is writable, and prints the resolved model and paths. It exits
non-zero if anything is wrong, so it is also safe to run in a container entrypoint.

---

## 1. Pick a platform

Prices checked October 2026 — verify on the provider's pricing page before you commit.

| | Railway | Render | Fly.io |
| --- | --- | --- | --- |
| Cheapest always-on | ~$5/mo Hobby minimum, usage offset by $5 credit | $7/mo Starter instance | ~$2–6/mo pay-as-you-go (512 MB ≈ $2.02) |
| Free tier | No permanent one (one-time trial credit) | Free web services **sleep after 15 min idle** | No permanent allowance; 2-hour trial |
| Sleeps when idle? | No | **Yes on Free** | Only if you let it scale to zero (we disable that) |
| Persistent disk | Volume, $0.15/GB/mo | Disk, $0.25/GB/mo, **paid instances only** | Volume, $0.15/GB/mo |
| Volume size must be ≥ | 1 GB | 1 GB | 1 GB |
| Best for | fastest setup, GitHub-integrated | UI-driven, predictable | cheapest always-on |

**Verdict:** if you want free, *none* of these is a good permanent home for a poller.
A sleeping service is a deaf bot, and a $5–7/mo always-on instance (or a $4 Hetzner VPS)
is the honest minimum. The `docker-compose.yml` path already in the README is cheaper
than all three if you already own a server.

---

## 2. Deploy

All three options need the same three secrets:

- `TELEGRAM_BOT_TOKEN` — required
- `OPENAI_API_KEY` — required
- `AGENT_ALLOWED_USERS` — **strongly recommended.** Comma-separated Telegram user ids.
  Without it, anyone who finds your bot can spend your model credits.

Add `AGENT_MODEL`, `AGENT_NAME`, `AGENT_SYSTEM` as desired (see README).
Set `AGENT_ALLOW_NETWORK=0` to disable the `http_get` tool entirely.

### Option A — Railway (fastest)

1. Push this repo to GitHub.
2. [railway.app](https://railway.app) → **New Project → Deploy from GitHub repo** → pick it.
3. Railway detects `Dockerfile` and `railway.json`. Add the secrets under
   **Variables** (paste them one per line; the raw-editor switch is quicker).
4. **Settings → Volumes → Add volume**, mount path `/data`.
5. Deploy. Railway injects `PORT` automatically; the health check hits `/health`.

### Option B — Render (blueprint)

1. Push this repo to GitHub.
2. Render → **New → Blueprint** → select the repo. `render.yaml` is read automatically.
3. Fill the three `sync: false` secrets when prompted.
4. The blueprint already sets `plan: starter`, `healthCheckPath: /health` and a 1 GB
   disk at `/data`.

Do **not** leave it on the Free instance type: Render suspends free web services after
15 minutes of inactivity and the bot stops receiving messages. That is a platform
behaviour, not a bug you can configure around.

### Option C — Fly.io (cheapest always-on)

```bash
fly auth login
fly launch --no-deploy --copy-config      # keeps the included fly.toml
fly volumes create agentkit_data --size 1 --region fra
fly secrets set TELEGRAM_BOT_TOKEN=... OPENAI_API_KEY=... AGENT_ALLOWED_USERS=...
fly deploy
```

`fly.toml` sets `auto_stop_machines = false` and `min_machines_running = 1` on purpose —
scaling to zero would stop the poller. Check with `fly logs` and `fly status`.

---

## 3. Verify the deploy (don't skip this)

```bash
curl -s https://<your-app-url>/health
# {"status":"ok","service":"agentkit","bot":"running","uptime_s":42.1,"handled":0}
```

Then, in Telegram:

1. Send `/start` to your bot — you should get a greeting.
2. Send `remember my name is Sam`, then `/facts` — the fact should appear.
3. Send `2+2` and ask it to use a tool — the tool list should appear in `/help`.

`handled` in `/health` increments per answered message, which is the quickest way to
prove the poller is alive rather than just the web server.

---

## 4. Persistence: the part that bites you

Memory is plain files under `AGENT_MEMORY_DIR` (`/data/memory` in the container).
Without a mounted volume, **every redeploy wipes every user's facts and history.**
That is why each option above mounts `/data`.

Verify the mount from the platform shell (Render/Railway/Fly all offer one):

```bash
ls -la /data/memory/users      # per-user directories appear after the first message
python bot.py --check          # "ok disk : memory dir is writable"
```

If `--check` says the disk is not writable, the volume is mounted at the wrong path or
owned by the wrong user — the container runs as uid 1000 (`agent`).

---

## 5. Updating and rolling back

- **Railway / Render:** connect the GitHub repo and each push to the tracked branch
  redeploys (Render: `autoDeploy: true` in the blueprint).
- **Fly:** `fly deploy` from your machine.
- Rollback on Render/Railway is a redeploy of the previous build; on Fly use
  `fly releases` then `fly deploy --image <previous-image>`.

After every redeploy, re-check `/health` and send one message. The bot drops pending
updates on startup (`drop_pending_updates=True`) so a backlog won't be answered all
at once, which is deliberate.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Deploy reports healthy but the bot never replies | Another instance is polling the same token (409 Conflict), or a webhook is set | Run one instance only. Clear a webhook: `curl "https://api.telegram.org/bot<token>/deleteWebhook"` |
| Logs show `Conflict: terminated by other getUpdates request` | Same token running locally *and* on the platform | Stop one of them |
| Bot went silent after ~15 min | Render Free instance slept | Move to the Starter instance |
| Health check fails / deploy never goes live | Bot crashed before binding `$PORT` | Read the first log lines: `--check` prints `FAIL` per bad variable |
| Memory empty after deploy | No volume, or mounted at the wrong path | Mount at `/data`, check `AGENT_MEMORY_DIR` |
| Replies show `*asterisks*` literally | The bot already retries without Markdown when Telegram rejects it; some clients still show raw text | Cosmetic; tell the model "no markdown" via `AGENT_SYSTEM` |
| `llm error: HTTP 401` | Bad or expired model key | Refresh `OPENAI_API_KEY` |
| `llm error: HTTP 429` | Rate limit — the client retries twice with backoff, then gives up | Raise your tier or lower traffic |
| Every reply is slow / users queue up | Expected: one worker, one model call at a time per user; requests are off the event loop but the LLM is still serial per user | Upgrade the instance or use a faster model |

---

## 7. Cost expectations

A personal bot with light use costs the platform fee and almost nothing in tokens: each
turn sends the system prompt plus the last 20 history rows, so long chats cost more per
message. If you care about that, `/reset` clears history without touching facts.

Cheapest legitimate setups, in order: your own VPS with `docker compose up -d`
(~$4/mo, no platform fee), Fly (~$2–6/mo), Railway (~$5/mo), Render ($7/mo).
