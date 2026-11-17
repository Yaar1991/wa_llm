# 📱 WhatsApp Group Summary Bot (local LLM)

![release version](https://img.shields.io/github/v/release/ilanbenb/wa_llm)
![Build Image](https://github.com/ilanbenb/wa_llm/actions/workflows/docker.yml/badge.svg)
![Release](https://github.com/ilanbenb/wa_llm/actions/workflows/release.yml/badge.svg)

A WhatsApp bot that runs on **your own WhatsApp number** as a linked device. It posts a **Hebrew summary** to the groups you enable, on a schedule, using a **local LLM** through [Ollama](https://ollama.com). No chat content goes to a cloud AI service.

---

## How it works

- You link the WhatsApp service to your number as a companion device, the same way WhatsApp Web works.
- You pick which groups to summarize with a CLI. Groups are disabled by default.
- **Only enabled groups are stored.** Direct messages are never stored or answered. Messages from groups you haven't enabled are dropped before they reach the database.
- On a schedule (one cron expression for all enabled groups), each enabled group with enough new messages gets two messages posted into it: a very brief recap paragraph that gives each discussion space in proportion to how big it was, then the full bullet-point summary.
- The summary includes messages you typed yourself, but never messages the bot sent.
- The bot never replies to messages and never @-tags anyone. People are referred to by their display names, written in Hebrew (see [Names in Hebrew](#names-in-hebrew)).
- The model loads into RAM only for the summary run and is unloaded right after.

---

## 🐳 Docker Compose Configurations

| File                           | Purpose                                                                         | Usage                                                  |
| ------------------------------ | ------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `docker-compose.yml`           | **Default/Development**. Builds the app from source and mounts `src/`.          | `docker compose up -d`                                 |
| `docker-compose.prod.yml`      | **Production**. Builds from source, no source mounts, restart policies.         | `docker compose -f docker-compose.prod.yml up -d`      |
| `docker-compose.local-run.yml` | **Local Execution**. Postgres and WhatsApp in Docker; you run the app on host.  | `docker compose -f docker-compose.local-run.yml up -d` |
| `docker-compose.base.yml`      | **Base Configuration**. Shared service definitions.                             | ❌ **Do not use directly**                             |

Host ports use an uncommon range to avoid clashes with other local services: API `47320`, WhatsApp panel `47321`, Postgres `47322`. Change them with `WEB_SERVER_HOST_PORT`, `WHATSAPP_HOST_PORT` and `POSTGRES_HOST_PORT` in `.env`.

Ollama is **not** in Docker. It runs natively on the Mac so it can use the Apple Silicon GPU, and the containers reach it at `host.docker.internal:11434`.

---

## 📋 Prerequisites

- 🐳 Docker Desktop
- 🦙 [Ollama](https://ollama.com/download) for macOS
- 🐍 Python 3.13+ and [uv](https://docs.astral.sh/uv/) (only for local development)
- 📲 Your WhatsApp account on your phone

## Quick Start

### 1. Set up Ollama (on the Mac, once)

Install Ollama, pull the base model, and create the bot's model from `ollama/Modelfile`:

```bash
brew install ollama            # or install the app from ollama.com
brew services start ollama     # or open the Ollama app

ollama pull gemma4:26b-a4b-it-qat
ollama create wa-summarizer -f ollama/Modelfile
```

The Modelfile fixes the context window, because the API the bot uses can't set it per request. The named model shares the base model's files, so it takes no extra disk space, and the setting survives reboots.

The bot unloads the model as soon as the run finishes (`UNLOAD_MODEL_AFTER_RUN`). To see the memory in use during a run, look at the `llama-server` process in Activity Monitor. For this model, `ollama ps` shows only working memory (under 1 GB), because the model file is memory-mapped.

**Choosing a model.** To try another model, change the `FROM` line in `ollama/Modelfile` and run `ollama create` again. Measured on an M4 Mac mini (32 GB) summarizing the same 50 real Hebrew group messages, with a 16K context:

| Model | RAM while loaded | Time per summary | Summaries |
| --- | --- | --- | --- |
| `gemma4:26b-a4b-it-qat` (default) | ~15 GB | ~30–40 s | Specific and well attributed; about one misspelled word per summary |
| `gemma4:e4b` | ~9.4 GB | ~27 s | Vague; misattributes some points |
| `gemma4:e4b-it-qat` | ~6.2 GB | ~30 s | Similar to `gemma4:e4b` |
| `qwen3:8b` | ~7.0 GB | ~18 s | Drifts into Chinese/Arabic in most runs |

In the same test, `LLM_PROVIDER=openai` with `gpt-6-luna` wrote the most accurate summaries, with no spelling mistakes, in about 15 s and for about $0.0004 per summary. It sends the chat to OpenAI.

Some models occasionally slip characters from another alphabet (e.g. Chinese) into the Hebrew. The bot regenerates such output, up to three tries in total. If every try drifts, it removes the stray characters from the last one.

### 2. Create .env file

```bash
cp .env.example .env
```

#### Environment Variables

| Variable                       | Description                                                                       | Default                                                      |
| ------------------------------ | --------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `WHATSAPP_HOST`                | WhatsApp Web API URL (Compose overrides it inside Docker)                         | `http://localhost:47321`                                     |
| `WHATSAPP_BASIC_AUTH_USER`     | WhatsApp API user                                                                 | `admin`                                                      |
| `WHATSAPP_BASIC_AUTH_PASSWORD` | WhatsApp API password                                                             | `admin`                                                      |
| `DB_URI`                       | PostgreSQL URI (Compose overrides it inside Docker)                               | `postgresql+asyncpg://user:password@localhost:47322/postgres` |
| `WEB_SERVER_HOST_PORT`         | Host port for the bot's API (read by Docker Compose)                              | `47320`                                                      |
| `WHATSAPP_HOST_PORT`           | Host port for the WhatsApp panel (read by Docker Compose)                         | `47321`                                                      |
| `POSTGRES_HOST_PORT`           | Host port for Postgres (read by Docker Compose)                                   | `47322`                                                      |
| `PORT`                         | Port the app listens on when run on the host (Docker uses 8000 internally)        | `47320`                                                      |
| `LOG_LEVEL`                    | Log level (`DEBUG`, `INFO`, `ERROR`)                                              | `INFO`                                                       |
| `OLLAMA_BASE_URL`              | Ollama server root URL (Compose sets `http://host.docker.internal:11434`)         | `http://localhost:11434`                                     |
| `OLLAMA_MODEL`                 | Ollama model name (created from `ollama/Modelfile`)                               | `wa-summarizer`                                              |
| `LLM_PROVIDER`                 | `ollama` (local) or `openai` (sends each run's messages to OpenAI's API)          | `ollama`                                                     |
| `OPENAI_API_KEY`               | OpenAI API key, required when `LLM_PROVIDER=openai`                               | (empty)                                                      |
| `OPENAI_MODEL`                 | OpenAI model when `LLM_PROVIDER=openai`                                           | `gpt-6-luna`                                                 |
| `LLM_DISABLE_THINKING`         | Ask thinking models to answer directly                                            | `true`                                                       |
| `LLM_MAX_OUTPUT_TOKENS`        | Max tokens per model reply                                                        | `3072`                                                       |
| `LLM_TIMEOUT_SECONDS`          | Timeout per model request                                                         | `600`                                                        |
| `UNLOAD_MODEL_AFTER_RUN`       | Evict the model from RAM after each summary run                                   | `true`                                                       |
| `SUMMARY_CRON`                 | When to summarize all enabled groups (crontab format). Empty disables scheduling. | `0 21 * * *`                                                 |
| `SUMMARY_TIMEZONE`             | Timezone for `SUMMARY_CRON` and transcript times                                  | `Asia/Jerusalem`                                             |
| `SUMMARY_MIN_MESSAGES`         | Skip a group until it has at least this many new messages                         | `15`                                                         |
| `SUMMARY_CHUNK_MAX_CHARS`      | Longer chats are summarized in parts of this size, then merged                    | `12000`                                                      |
| `HEBREW_NAMES_FILE`            | JSON file with the Hebrew spelling of each display name. Empty leaves names as is. | `data/hebrew_names.json`                                     |
| `PARTICIPANT_NOTES_FILE`       | Your notes about the people in the group (see below). Empty disables.             | `data/participants.txt`                                      |
| `LEARNED_NOTES_FILE`           | Notes the bot keeps about the people in the group. Empty disables learning.       | `data/participants_learned.txt`                              |

Keep `SUMMARY_CHUNK_MAX_CHARS` plus the output budget well below the `num_ctx` in `ollama/Modelfile`. Hebrew uses roughly 2–3 characters per token.

After editing `.env`, apply it with `docker compose up -d`. `docker compose restart` keeps the old values, because a container reads `.env` only when it is created.

### 3. Start the services

```bash
docker compose up -d
```

### 4. Link your WhatsApp number

1. Open http://localhost:47321 (user/password `admin`/`admin` unless changed).
2. On your phone, go to **WhatsApp → Settings → Linked Devices → Link a device**, then scan the QR code.

The service joins as one of your linked devices. Your phone and other devices keep working as usual.

> ⚠️ This uses an unofficial WhatsApp Web client, which goes against WhatsApp's terms. Automated sending can put your number at risk of restrictions. Keep the schedule modest.

### 5. Choose which groups to summarize

```bash
# Show your WhatsApp groups, numbered, with their enabled state
docker compose exec web-server python app/cli.py groups list

# Enable a group by its number, its name (or a unique part of it), or its JID
docker compose exec web-server python app/cli.py groups enable 3
docker compose exec web-server python app/cli.py groups enable "משפחה"

# Stop storing and summarizing a group
docker compose exec web-server python app/cli.py groups disable 3
```

- Numbers follow the alphabetical order of group names, so they shift when you join or rename a group. When you pick a group by number, the command shows its name and asks before changing it. Add `--yes` to skip the question.
- Enabling a group starts fresh: only messages from that moment on are stored and summarized.
- Disabling stops storing new messages. Messages already stored stay in the database.
- No restart is needed after enabling or disabling.

To try it right away instead of waiting for the schedule:

```bash
docker compose exec web-server python app/cli.py summarize-now --min-messages 1
```

When running the app on the host (`docker-compose.local-run.yml`), use `uv run python app/cli.py ...` instead.

#### Names in Hebrew

Summaries write people's names in Hebrew letters. The spelling of each name is kept in `data/hebrew_names.json`, which is not in git:

```json
{
  "Dana Levi": "דנה לוי",
  "Michael": "מיכאל"
}
```

- The key is the person's WhatsApp display name, and the value is how summaries write it.
- When someone whose name isn't in Hebrew writes for the first time, the model picks a spelling and adds it to the file. Later summaries reuse it.
- To fix a spelling, edit the value. The next summary uses it; no restart is needed.
- Names already written in Hebrew are used as they are and don't appear in the file.

#### Notes about the people in the group

Summaries read tone better (for example, sarcasm) when the model knows how each person usually writes. Two files in `data/` hold this background, one line per person in the form `<name>: <note>`:

- `data/participants.txt` is yours. Create it and write whatever helps, for example:

  ```text
  # Lines starting with # are ignored. Use the name as it appears in summaries.
  ניר: ימני. כותב הרבה בציניות; כשהוא "מסכים" עם השמאל או כותב "שמאלנים" במירכאות, זה בדרך כלל לעג.
  מתן מור: שמאל-מרכז, כותב ברצינות ומביא כתבות וציטוטים.
  ```

- `data/participants_learned.txt` is written by the bot. After each summary it sends, the model updates its notes on how people write and which positions they keep coming back to. You can edit or delete its lines.
- Your note about a person always wins over the learned one, and the bot is told never to contradict your notes.
- The notes only help the model interpret messages. They are never quoted in summaries.
- Both files are read on every run, so changes apply to the next summary without a restart.

### 6. API

Swagger docs are at `http://localhost:47320/docs`.

- **`POST /summarize_and_send_to_groups`** runs the summary job now, the same as the scheduled run.
- **`GET /status`** checks WhatsApp device and database health.

---

## 🚀 Production Deployment

1. Copy `.env.example` to `.env.prod` and configure it.
2. Start the services:
   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```

---

## Developing

### Setup

```bash
uv sync --all-extras --dev
```

### Development Commands

The project uses **Poe the Poet** for task automation:

```bash
uv run poe check      # format, then lint/typecheck/test in parallel
uv run poe format     # ruff format
uv run poe lint       # ruff check
uv run poe typecheck  # pyright
uv run poe test       # pytest
```

### Key Files

- Main application and scheduler wiring: `app/main.py`
- Group CLI: `app/cli.py` → `src/group_admin/`
- Webhook storage filter: `src/handler/__init__.py`
- Summary pipeline (chunking, prompts): `src/summarize_and_send_to_groups/`, `src/templates/`
- Ollama model access: `src/llm/`
- Database models: `src/models/`

---

## Architecture

- FastAPI backend that receives WhatsApp webhooks and stores messages for enabled groups only
- In-app scheduler (APScheduler) that runs the summary job on `SUMMARY_CRON`
- Local LLM via Ollama's OpenAI-compatible API (pydantic-ai)
- PostgreSQL for messages and groups
- [go-whatsapp-web-multidevice](https://github.com/aldinokemal/go-whatsapp-web-multidevice) as the WhatsApp linked-device client

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Submit a pull request

---

## License

[LICENCE](CODE_OF_CONDUCT.md)
