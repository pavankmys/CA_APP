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
- **🎧 Audio Notes** — chapter PDFs converted into spoken-explainer audio
  episodes (AI script generation with a mandatory hallucination-check
  pass, then text-to-speech), published as a podcast RSS feed — subscribe
  via "Add by URL" in any podcast app. See [Audio Notes](#audio-notes-podcast-feed)
  below.
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
| `CA_APP/audio_notes.py` | Audio Notes pipeline — script generation, hallucination verification, edge-tts synthesis | Ingest app only |
| `CA_APP/audio_publish.py` | Audio Notes publishing — Cloudflare R2 upload + podcast RSS feed generation | Ingest app only |

AI providers supported for content generation: Google Gemini, Anthropic
Claude, xAI Grok, Kimchi (kimi-k2.5). Audio Notes script generation always
uses Gemini (long-form generation isn't reliable on the other providers).

---

## Setup

### Database (Supabase)

1. Create a free [Supabase](https://supabase.com) project.
2. In the SQL editor, run `CA_APP/schema.sql` to create the base tables.
3. Run `CA_APP/migration_002_cpa_exam.sql` to add the CPA Simulation Exam
   tables.
4. Run `CA_APP/migration_004_audio_episodes.sql` to add the Audio Notes
   table (optional, only needed for the Audio Notes feature).
5. Get your connection string (Project Settings → Database → Connection
   string → use the **pooler** connection for IPv4 compatibility).

---

## How to Run

### Windows (PowerShell)

```powershell
python -m venv venv
venv\Scripts\pip install -r CA_APP/requirements.txt          # hosted app only
venv\Scripts\pip install -r CA_APP/requirements-ingest.txt    # ingest app (adds AI SDKs)

copy CA_APP\.env.example CA_APP\.env
copy CA_APP\.streamlit\secrets.toml.example CA_APP\.streamlit\secrets.toml
```

Fill in your real values in `CA_APP\.env` (both files are gitignored), then run:

```powershell
venv\Scripts\streamlit run CA_APP/app.py          # practice app
venv\Scripts\streamlit run CA_APP/ingest_app.py   # ingest app (desktop)
```

### Linux / Fedora (bash)

```bash
python3 -m venv venv
venv/bin/pip install -r CA_APP/requirements.txt          # hosted app only
venv/bin/pip install -r CA_APP/requirements-ingest.txt    # ingest app (adds AI SDKs)

cp CA_APP/.env.example CA_APP/.env
cp CA_APP/.streamlit/secrets.toml.example CA_APP/.streamlit/secrets.toml
```

Fill in your real values in `CA_APP/.env` (both files are gitignored), then run:

```bash
venv/bin/streamlit run CA_APP/app.py          # practice app
venv/bin/streamlit run CA_APP/ingest_app.py   # ingest app (desktop)
```

> On Fedora, if `python3 -m venv` fails with "ensurepip is not available",
> install it first: `sudo dnf install python3-pip`. The Audio Notes feature's
> `edge-tts`/`mutagen` dependencies need no extra system packages beyond Python.

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
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET` / `R2_PUBLIC_URL` | `ingest_app.py` | Only for Audio Notes | Cloudflare R2 (S3-compatible) credentials for hosting audio files + the podcast feed |

Secrets are read from `.streamlit/secrets.toml` (hosted) or `.env`
(desktop) — see `.env.example` and `.streamlit/secrets.toml.example` for
templates. **Never commit `.env` or `.streamlit/secrets.toml`** — both are
gitignored.

---

## Audio Notes (Podcast Feed)

Chapter PDFs can be converted into spoken-explainer audio episodes
(~20-30 min each) via the **🎧 Generate Audio Notes** section of
`ingest_app.py`. Each script is generated by Gemini, run through a
hallucination-verification pass against the source PDF, then synthesized
with `edge-tts` and uploaded to Cloudflare R2. The **Manage Audio Notes**
section publishes/updates the podcast RSS feed.

Subscribe to the feed in any podcast app that supports "Add by URL" /
"Follow a Show by URL" (e.g. AntennaPod, Podcast Addict, Apple Podcasts —
Pocket Casts requires a paid plan for custom feed URLs). The feed URL is
`<R2_PUBLIC_URL>/feed.xml` — see `PODCAST_FEED_URL` in your `.env`.

---

## Tech Stack

Python, Streamlit, Supabase (Postgres) via `psycopg2`, SM-2 spaced
repetition, Pydantic v2 (LLM structured output).
