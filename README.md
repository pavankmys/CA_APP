# CA Inter Practice Engine

A Streamlit + Supabase (Postgres) spaced-repetition (SM-2) study tool for
ICAI CA Inter exam preparation, with AI-generated MCQs and CPA-style
simulation exams.

> **Note:** This repository is **public** (required for free Streamlit
> Community Cloud hosting). No credentials, API keys, or database
> connection strings are committed — see [Configuration](#configuration)
> below for how secrets are supplied.

---

## Features

- **🎯 Daily Practice Deck** — SM-2 spaced-repetition review of MCQs, with
  auto-computed recall ratings based on attempts and time taken.
- **📊 Analytics** — study time, readiness/trending score, chapter-level
  confidence, weak-chapter tracking, and exam history.
- **📝 CPA Simulation Exam** — full mock exam combining a high-difficulty
  MCQ section with multi-part case-study simulations, scored against a
  75% proficiency threshold.
- **🔒 Optional password gate** for the hosted app (single shared password).

---

## Architecture

This is a two-app split, both backed by the same Supabase Postgres database:

| App | Purpose | Where it runs |
|---|---|---|
| `CA_APP/app.py` | Hosted practice app — Daily Practice Deck, Analytics, CPA Simulation Exam | Streamlit Community Cloud (or anywhere) |
| `CA_APP/ingest_app.py` | Content generation — PDF upload, AI-generated MCQs/simulations, question bank management | Desktop only (needs AI provider API keys) |
| `CA_APP/database.py` | Shared data access layer (psycopg2 / Postgres) | Both |
| `CA_APP/parser.py` | PDF chunking + multi-provider LLM generation | Ingest app only |

AI providers supported for content generation: Google Gemini, Anthropic
Claude, xAI Grok, Kimchi (kimi-k2.5).

---

## Setup

### 1. Database (Supabase)

1. Create a free [Supabase](https://supabase.com) project.
2. In the SQL editor, run `CA_APP/schema.sql` to create the base tables.
3. Run `CA_APP/migration_002_cpa_exam.sql` to add the CPA Simulation Exam
   tables.
4. Get your connection string (Project Settings → Database → Connection
   string → use the **pooler** connection for IPv4 compatibility).

### 2. Local environment

```powershell
python -m venv venv
venv\Scripts\pip install -r CA_APP/requirements.txt          # hosted app only
venv\Scripts\pip install -r CA_APP/requirements-ingest.txt    # ingest app (adds AI SDKs)
```

Copy the example config files and fill in your real values (both are
gitignored):

```powershell
copy CA_APP\.env.example CA_APP\.env
copy CA_APP\.streamlit\secrets.toml.example CA_APP\.streamlit\secrets.toml
```

### 3. Run locally

```powershell
venv\Scripts\streamlit run CA_APP/app.py          # practice app
venv\Scripts\streamlit run CA_APP/ingest_app.py   # ingest app (desktop)
```

---

## Deployment (Streamlit Community Cloud)

1. Push this repo to GitHub (must be public for the free tier).
2. On [share.streamlit.io](https://share.streamlit.io), create a new app:
   - Repository: this repo, branch `master`
   - Main file path: `CA_APP/app.py`
3. In **Settings → Secrets**, add:
   ```toml
   SUPABASE_DB_URL = "postgresql://..."
   APP_PASSWORD = "your-chosen-password"   # optional
   ```
4. Deploy — the app gets a public `https://<name>.streamlit.app` URL.

`CA_APP/ingest_app.py` is **not** deployed; run it locally to populate the
question bank and CPA exam content pool.

---

## Configuration

| Variable | Used by | Required | Description |
|---|---|---|---|
| `SUPABASE_DB_URL` | both apps | Yes | Postgres connection string (Supabase pooler recommended) |
| `APP_PASSWORD` | `app.py` | No | Shared password gate for the hosted app — leave unset to disable |
| `AI_PROVIDER` | `ingest_app.py` | Yes (ingest only) | `gemini`, `claude`, `grok`, or `kimchi` |
| `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `XAI_API_KEY` / `KIMCHI_API_KEY` | `ingest_app.py` | Per provider | AI provider API keys |

Secrets are read from `.streamlit/secrets.toml` (hosted) or `.env`
(desktop) — see `.env.example` and `.streamlit/secrets.toml.example` for
templates. **Never commit `.env` or `.streamlit/secrets.toml`** — both are
gitignored.

---

## Tech Stack

Python, Streamlit, Supabase (Postgres) via `psycopg2`, SM-2 spaced
repetition, Pydantic v2 (LLM structured output).
