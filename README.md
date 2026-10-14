# IT Support Assistant

A browser based IT support assistant built with FastAPI, plain JavaScript, SQLite, and OpenTelemetry. It classifies a support request, runs code-selected diagnostic tools, and gives advice using the configured language model and, when available, the troubleshooting manual. Changes to a system require the user to approve a pending action.

## Requirements

- Python 3.11 or newer
- Node.js for the frontend test suite
- An API key for the selected hosted chat provider, or a running local Ollama server
- Docker Desktop only if you want the optional local OpenTelemetry stack

Run commands from the repository root so relative paths such as `data/assistant.db` and `.env` resolve correctly.

## Run on Windows PowerShell

Create and activate a virtual environment, then install the declared dependencies:

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Create `.env` only if you do not already have one, then edit it and add the key for your selected provider:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Set `LLM_PROVIDER` to a provider listed in `.env.example`, `LLM_MODEL` to a model supported by that provider, and provide its credential through `LLM_API_KEY` or the matching provider-specific key. For example:

```dotenv
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-120b
GROQ_API_KEY=your-provider-key
```

Keep real credentials in your local `.env`; do not commit that file or paste its contents into logs or support requests.

Create the first administrator once. The command prompts for the email and password without echoing the password:

```powershell
python -m backend.bootstrap_admin
```

Then start the application:

```powershell
uvicorn backend.main:app --reload --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), register a regular account, or sign in with the administrator created above. The app serves the frontend and API from the same origin. `/health` is a liveness check; administrator runtime details are in the Admin dashboard.

## Run on macOS or Linux

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
# Edit .env and configure a supported chat provider and credential.
python -m backend.bootstrap_admin
uvicorn backend.main:app --reload --port 8000
```

## Configuration

Application values are read from `.env` by `backend/utils/config.py`. Common settings are:

| Setting | Purpose |
| --- | --- |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` | Active chat provider, model, and optional shared key |
| Provider key such as `GROQ_API_KEY` | Provider-specific chat credential when `LLM_API_KEY` is blank |
| `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` | Chat provider timeout and retry bound |
| `DB_PATH` | SQLite file for accounts, sessions, conversations, and settings |
| `MEMORY_WINDOW_TURNS` | Bounded in-process conversation context |
| `UPLOAD_DIR`, `MAX_UPLOAD_BYTES`, `MAX_ATTACHMENTS_PER_MESSAGE` | Attachment storage and limits |
| `LLM_SUPPORTS_VISION`, `STREAM_MAX_SECONDS` | Image capability flag and stream deadline |
| `KNOWLEDGE_PDF_PATH` | Local troubleshooting PDF used by semantic retrieval |
| `HF_TOKEN`, `HF_EMBEDDING_MODEL`, `HF_EMBEDDING_URL` | Hugging Face embedding service configuration |
| `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION` | Optional Qdrant semantic index |
| `TELEMETRY_ENABLED`, `TELEMETRY_EXPORTER` | Telemetry enablement and `otlp`, `console`, or `none` exporter |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP HTTP endpoint, normally `http://localhost:4318` for local Compose |

The active chat model is read from `.env` at application startup. Admin model selections can be validated and saved for review, but saving does not activate them, including after a restart. To change the active model, update `LLM_PROVIDER`, `LLM_MODEL`, and the matching credential in `.env`, then restart the backend. Provider configuration status does not test external provider connectivity.

## Optional semantic search for the troubleshooting manual

At startup, the application indexes the local PDF with the configured Hugging Face embedding service and Qdrant only when both `HF_TOKEN` and `QDRANT_URL` are set. Set `KNOWLEDGE_PDF_PATH` to the manual's path if it is not at the default location. If either service is not configured or indexing fails, the assistant starts without semantic retrieval.

The admin Embeddings and Memory & Retrieval views report available configuration and source-file status. They do not verify Qdrant connectivity or index health. The current index does not persist embedding-model version and dimension metadata, so changing embedding models or manually clearing/replacing a collection is not a supported admin operation. Do not point a changed model at an existing collection without a separately designed, validated re-index migration.

## Optional OpenTelemetry stack

To send traces, metrics, and logs to the local collector, set `TELEMETRY_ENABLED=true`, `TELEMETRY_EXPORTER=otlp`, and `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` in `.env`. Start the local stack in another PowerShell window:

```powershell
docker compose -f deploy/docker-compose.yml up -d
```

Useful local dashboards:

- Jaeger traces: [http://localhost:16686](http://localhost:16686)
- Prometheus: [http://localhost:9090](http://localhost:9090)
- Grafana: [http://localhost:3000](http://localhost:3000)

If no collector is running, use `TELEMETRY_EXPORTER=console` for local development or `TELEMETRY_EXPORTER=none` to disable exporting. The included Compose stack is for local development: do not expose its ports, anonymous Grafana viewer, or default credentials to an untrusted network.

## Tests

Run backend tests from the repository root:

```powershell
python -m pytest -q
```

Run frontend tests with Node's built-in test runner:

```powershell
node --test frontend/tests/*.test.cjs
```

Tests use fakes, mocks, and temporary databases; they do not require live provider credentials.

## Current account and admin features

- Users can register, sign in, sign out, change their password, and keep private persistent conversations.
- Users can create, rename, continue, edit, and delete conversations and messages they own.
- Admins can manage account roles/status, inspect audit events, and validate/save a supported chat provider and model.
- Admin Overview, Embeddings, Memory & Retrieval, and Settings views show server-returned status. Application-wide settings views are read-only; no admin settings mutation endpoint is currently provided.
- The first administrator must be created with `python -m backend.bootstrap_admin`; public registration always creates a regular user.

Email-based password reset is not available because no email delivery service is configured. An administrator cannot reset another account's password through the dashboard; users can change their own password while authenticated.

Existing legacy ticket turns and attachments are not exposed through authenticated user routes. They are left untouched until an explicit ownership policy is implemented.

## Database and deployment notes

The application applies additive, versioned SQLite migrations as its database-backed services initialize. Back up `DB_PATH` before deploying an update and verify the migration on a copy of production data first. Do not delete or recreate the database to resolve a migration issue.

For production, serve the app behind HTTPS, restrict network access to the database and observability services, use managed secrets rather than a checked-in `.env`, configure persistent backups, and protect the OTLP/Grafana/Jaeger/Prometheus endpoints. SQLite and in-process short-term memory are intended for a single-instance deployment; a multi-worker or distributed deployment needs a separately designed shared database and session-memory migration.
