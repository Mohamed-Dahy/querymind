# QueryMind

[![CI](https://github.com/Mohamed-Dahy/querymind/actions/workflows/ci.yml/badge.svg)](https://github.com/Mohamed-Dahy/querymind/actions/workflows/ci.yml)

**An AI data analyst for company databases.** Ask a question in plain English and get back the answer,
the SQL that produced it, a result table, and a chart.

> "Which country placed the most orders in 1997?" → answer + SQL + table + chart

QueryMind is built to **assist** data teams, not replace them. It handles routine questions so analysts
can focus on deeper work, and it always shows its SQL so every answer can be checked.

> 🚧 **Status: early development.** The foundations are in place (database, read-only access, config, LLM
> layer, CI). The question-to-answer pipeline is next.

---

## How it will work

```
Question → LLM writes SQL → safety check → run on a read-only DB user → answer + table + chart
```

## Safety first

QueryMind runs SQL written by an AI, so the database is protected in layers:

- **Read-only database user.** The agent connects as `analyst_ro`, which can only `SELECT` from business
  tables. Every write (INSERT, UPDATE, DELETE, DROP, …) is blocked by Postgres itself. Tests prove this.
- **Query timeout.** Any query running longer than 10 seconds is cancelled.
- **Secrets stay out of code.** All passwords and API keys come from environment variables.
- *Coming next:* SQL validation before every query, row limits, PII masking, and an audit log.

## Tech stack

| Area | Tools |
|---|---|
| Language | Python 3.12, [uv](https://docs.astral.sh/uv/) |
| Database | PostgreSQL 16 + pgvector (Docker) |
| LLMs | Groq and Gemini through one interface (LangChain wrappers) |
| Config | pydantic-settings |
| Quality | ruff, mypy (strict), pytest, pre-commit |
| CI | GitHub Actions |
| Planned | LangGraph, FastAPI, Streamlit, AWS (Bedrock, ECS), Terraform |

---

## Getting started

### Requirements
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (it installs Python 3.12 for you)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- An API key from [Groq](https://console.groq.com/keys) or [Google AI Studio](https://aistudio.google.com/apikey)

### Setup

```bash
git clone https://github.com/Mohamed-Dahy/querymind.git
cd querymind

uv sync                                # install dependencies
cp .env.example .env                   # then edit .env: passwords + your API key
                                       # (Windows PowerShell: Copy-Item .env.example .env)

docker compose up -d --wait            # start Postgres + pgvector on 127.0.0.1:5433
uv run python data/seed_northwind.py   # load the sample data + create the read-only user
```

### Try the LLM connection

```bash
uv run python scripts/llm_smoke.py                    # uses LLM_PROVIDER from .env
uv run python scripts/llm_smoke.py --provider gemini  # or pick a provider
```

### Run the checks

```bash
uv run pytest                    # all tests (integration tests need the database running)
uv run pytest -m "not integration"  # unit tests only
uv run ruff check .              # lint
uv run mypy                      # type-check
```

## Configuration

All settings are read from environment variables or a `.env` file. See [`.env.example`](.env.example) for
the full list. The app checks them at startup and stops with a clear message if something is missing.

| Variable | Purpose |
|---|---|
| `POSTGRES_*` | Database connection and admin login (setup only) |
| `ANALYST_RO_USER` / `ANALYST_RO_PASSWORD` | The read-only user the agent connects with |
| `LLM_PROVIDER` | `groq` or `gemini` |
| `GROQ_API_KEY` / `GROQ_MODEL` | Groq settings (when `LLM_PROVIDER=groq`) |
| `GOOGLE_API_KEY` / `GEMINI_MODEL` | Gemini settings (when `LLM_PROVIDER=gemini`) |
| `QUERY_TIMEOUT_SECONDS` / `MAX_ROWS` | Query limits (optional) |

## Project structure

```
querymind/
├── src/querymind/
│   ├── config.py        # typed settings, validated at startup
│   ├── llm/             # one LLM interface + Groq and Gemini adapters
│   └── ...              # api, agent, db, safety, semantic, … (coming)
├── data/                # seed script for the Northwind sample database
├── scripts/             # helper scripts (LLM smoke test)
├── tests/
│   ├── unit/            # fast, no database or network
│   └── integration/     # real Postgres
├── docker-compose.yml
└── .env.example
```

## Sample data

The demo uses **Northwind**, a classic fictional company database (customers, orders, products, employees,
suppliers). The seed script downloads it from
[pthom/northwind_psql](https://github.com/pthom/northwind_psql) and checks its SHA-256 hash before loading it.

## Roadmap

- [x] Foundations: database, read-only user, config, LLM layer, CI
- [ ] Question → SQL → answer (first demo)
- [ ] Self-correcting agent with clarifying questions (LangGraph)
- [ ] Semantic layer and schema search (RAG with pgvector)
- [ ] Accuracy evaluation on the BIRD benchmark
- [ ] Users, permissions, row-level security, PII masking
- [ ] Deployment on AWS
