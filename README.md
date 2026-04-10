# OMNIBRIDGE Multimodal Chatbot

OMNIBRIDGE is a browser-based AI assistant built around local-first inference with Ollama. It gives you a chat UI, image understanding, document text extraction, account-based access control, persistent conversation memory, and a deployment path for both Docker and Kubernetes.

The current web app is Ollama-driven for responses. PostgreSQL with `pgvector` adds long-term memory and chat history, Redis is used for session state when available, SQLite handles authentication, and local transcript files are written to `user_data/`.

## What This Project Does

OMNIBRIDGE combines a lightweight TypeScript frontend with a Python backend and multiple storage layers:

- Serves a chat interface with guest and authenticated user modes
- Routes text and image requests to local Ollama models
- Extracts readable text from uploaded documents before sending that content into the prompt
- Stores recent messages, durable memory, and user profile summaries in PostgreSQL with `pgvector`
- Supports local email/password auth and Google OAuth
- Limits guest usage and unlocks uploads/history for signed-in users
- Writes human-readable chat transcripts to local files under `user_data/`

## Core Functionality

### Chat and multimodal input

- Text chat goes to the configured Ollama text model
- Image uploads are base64-encoded and sent to the configured Ollama vision model
- Document uploads are parsed locally and appended to the user prompt as extracted text
- Supported text extraction paths currently include:
  - Plain text and code-like files such as `.txt`, `.md`, `.csv`, `.log`, `.json`, `.py`, `.js`, `.ts`, `.html`, `.css`, `.xml`, `.yaml`, `.yml`
  - `.docx` through `python-docx`
  - `.pdf` through `PyPDF2`
- Extracted document text is truncated with `MAX_DOC_CHARS` to keep prompts inside a practical context window

### Access model

- Guests can chat without creating an account
- Guests are limited to `10` messages
- Guests cannot upload files or images
- Authenticated users unlock uploads, conversation history, and persistent personalization
- Local auth uses email/password with `scrypt` hashing
- Google OAuth is available when the Google client credentials are configured

### Memory and personalization

- Short-term and long-term memory live in PostgreSQL
- User messages are stored in the `messages` table
- Durable memory snippets are stored in `memory_chunks` with vector embeddings
- User profile summaries are stored in `user_profiles`
- Embeddings are generated through Ollama
- Profile extraction tries Gemini first if an API key is configured, then falls back to Ollama, and finally falls back to heuristic inference if needed
- If PostgreSQL is unavailable, the app still answers chat requests but history and memory are skipped

### Session and local data handling

- Browser session state is cached in Redis when available
- If Redis is not available, the app falls back to in-process Python memory
- Authentication data is stored in a local SQLite database at `auth/auth.db` unless `AUTH_DB_PATH` is overridden
- Every conversation turn is also appended to a plain text file in `user_data/`, keyed by user email or session ID

## Architecture

```text
Browser
  -> Vite dev server (development) or Nginx (container build)
  -> Python HTTP backend
      -> Ollama API
      -> Redis for session state (optional)
      -> PostgreSQL + pgvector for memory/history (optional but recommended)
      -> SQLite for auth users, auth sessions, guest tracking, OAuth state
      -> user_data/*.txt for local transcript dumps
```

### Storage split

This project intentionally uses different storage systems for different jobs:

- SQLite: auth users, auth sessions, guest usage counters, OAuth state
- PostgreSQL + `pgvector`: message history, long-term memory, user profiles, vector search
- Redis: browser session cache for chat state
- Local filesystem: transcript exports in `user_data/`

## Tech Stack

| Layer | Technology |
| --- | --- |
| Frontend | Vite, Vanilla TypeScript, plain CSS |
| Backend | Python 3, `BaseHTTPRequestHandler`, `ThreadingHTTPServer` |
| AI runtime | Ollama |
| Vision input | Ollama vision model |
| Memory embeddings | Ollama embeddings API |
| Auth | Local email/password auth, Google OAuth 2.0 with PKCE |
| Auth storage | SQLite |
| Memory/history storage | PostgreSQL + `pgvector` |
| Session cache | Redis |
| Containers | Docker, Docker Compose |
| Reverse proxy | Nginx |
| Orchestration | Kubernetes manifests included in `kubernetes/` |

## Repository Layout

```text
.
├── auth/                 # SQLite auth store, local auth, Google OAuth flow
├── backend/              # Python chat server and Ollama integration
├── database/             # PostgreSQL schema, pgvector memory, profile logic
├── frontend/             # Vite app, TypeScript UI, production build output
├── kubernetes/           # Base, backend, frontend, postgres, redis, ollama manifests
├── user_data/            # Local chat transcript exports
├── docker-compose.yml    # Multi-container local deployment scaffold
└── nginx.conf            # Frontend reverse proxy config for /api and /auth
```

## Environment Variables

Copy `.env.example` to `.env` and adjust the values for your environment.

### Core runtime variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | Yes | Base URL for the Ollama API |
| `OLLAMA_TEXT_MODEL` | Yes | Main text model for normal chat |
| `OLLAMA_VISION_MODEL` | Yes | Vision model used for image uploads |
| `OLLAMA_KEEP_ALIVE` | No | Ollama keep-alive value for chat requests |
| `OLLAMA_READ_TIMEOUT` | No | Read timeout for long-running Ollama responses |
| `MAX_DOC_CHARS` | No | Max extracted document characters appended to a prompt |
| `DB_ENABLED` | No | Set to `false` to disable PostgreSQL-backed memory/history |
| `DB_NAME` | If DB enabled | PostgreSQL database name |
| `DB_USER` | If DB enabled | PostgreSQL username |
| `DB_PASSWORD` | If DB enabled | PostgreSQL password |
| `DB_HOST` | If DB enabled | PostgreSQL host |
| `DB_PORT` | If DB enabled | PostgreSQL port |
| `DB_POOL_MAX` | No | PostgreSQL pool size |
| `DB_CONNECT_TIMEOUT` | No | PostgreSQL connect timeout in seconds |
| `REDIS_URL` | No | Redis connection string for session caching |
| `AUTH_DB_PATH` | No | Override the default SQLite auth DB path |

### Optional auth and profile variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID` | No | Enables Google OAuth login |
| `GOOGLE_CLIENT_SECRET` | No | Enables Google OAuth login |
| `APP_BASE_URL` | Recommended when using OAuth | Public base URL used to build redirects |
| `GOOGLE_REDIRECT_URI` | Recommended when using OAuth | Explicit OAuth callback URL |
| `GEMINI_API_KEY` | No | Optional Gemini key for user profile extraction fallback |
| `GEMINI_MODEL` | No | Default Gemini model name |
| `PROFILE_GEMINI_MODEL` | No | Gemini model used for user profile extraction |

### Optional memory tuning variables

| Variable | Purpose |
| --- | --- |
| `OLLAMA_EMBEDDING_MODEL` | Embedding model for vector memory |
| `OLLAMA_MEMORY_SUMMARIZER_MODEL` | Model used to compress user facts into durable memory |
| `OLLAMA_PROFILE_MODEL` | Model used for Ollama-based profile extraction |
| `DB_RECENT_MESSAGE_LIMIT` | Number of recent messages to inject into the memory prompt |
| `DB_MEMORY_LIMIT` | Number of relevant memories to retrieve |
| `USER_PROFILE_REFRESH_EVERY_MESSAGES` | How often the stored user profile is recalculated |
| `USER_PROFILE_SOURCE_MESSAGE_LIMIT` | Number of past messages used to rebuild the user profile |

### Important implementation note

The repo exposes `OLLAMA_FILE_MODEL` in `.env.example`, but the current web request path parses documents into text and feeds that text into the normal prompt flow. In other words, document-only requests are currently handled as extracted text, not by a dedicated file model branch.

## Local Development

### Prerequisites

- Python `3.11+`
- Node.js `20+`
- Ollama running locally
- PostgreSQL with the `vector` extension if you want memory and history
- Redis if you want external session caching across processes

### Recommended Ollama models

At minimum, pull the models you plan to reference in `.env`:

```bash
ollama pull llama3.2:3b
ollama pull moondream
ollama pull nomic-embed-text
```

If you keep the defaults from `.env.example`, these are the relevant starting points:

- `llama3.2:3b` for text chat
- `moondream` for image understanding
- `nomic-embed-text` for vector embeddings

If you want memory summarization and profile extraction to stay fully local, also set and pull explicit values for:

- `OLLAMA_MEMORY_SUMMARIZER_MODEL`
- `OLLAMA_PROFILE_MODEL`

Without those, the code falls back to its built-in defaults and then to heuristic behavior where necessary.

### Setup

1. Create the environment file.

```bash
cp .env.example .env
```

2. Create and activate the backend virtual environment.

```bash
python3 -m venv backend/venv
source backend/venv/bin/activate
pip install -r backend/requirements.txt
```

3. Initialize the auth database.

```bash
python3 auth/init_auth_db.py
```

4. Initialize PostgreSQL schema if `DB_ENABLED=true`.

```bash
python3 database/init_db.py
```

5. Start the backend.

```bash
python3 backend/app.py
```

6. Start the frontend in a separate shell.

```bash
cd frontend
npm install
npm run dev
```

7. Open the app at `http://localhost:3000`.

### Development notes

- Vite proxies `/api` and `/auth` to `http://127.0.0.1:5000`
- Google OAuth for local development should use the same public URL the browser sees, typically `http://localhost:3000/auth/google/callback`
- The backend can start without PostgreSQL, but history, long-term memory, and profile personalization will be unavailable
- The auth SQLite store initializes automatically on startup, so `auth/init_auth_db.py` is optional but useful when setting up explicitly

## Running Without PostgreSQL

If you only want local chat without memory or history:

1. Set `DB_ENABLED=false` in `.env`
2. Start Ollama
3. Start the backend
4. Start the Vite frontend

In this mode:

- Chat still works
- Local auth still works because it uses SQLite
- Guest limits still work
- Upload gating still works
- Conversation history and long-term memory are disabled

## Docker Compose

The repo includes a `docker-compose.yml` that wires together:

- `frontend`
- `backend`
- `postgres`
- `redis`

The frontend is exposed on `http://localhost:8080`.

### Start it

```bash
docker compose up --build
```

### What the compose file currently assumes

- PostgreSQL uses the `ankane/pgvector` image
- Redis is available at `redis://redis:6379/0`
- The backend writes transcript files to the mounted `./user_data` directory
- Ollama is expected to be reachable from the backend container at `http://host.docker.internal:11434`

### Compose caveats

The Docker setup is a useful scaffold, but it is not a fully hardened production deployment as-is.

- The backend currently binds to `127.0.0.1:5000` in `backend/app.py`. For container-to-container traffic, it needs to bind to `0.0.0.0`.
- `host.docker.internal` may need adjustment on Linux, depending on your Docker runtime configuration.
- The auth SQLite database is not mounted to persistent storage in the current compose file.
- Redis is used as an in-memory cache only; no persistence is configured.

## Kubernetes

The `kubernetes/` folder includes manifests for:

- namespace and base secret
- frontend deployment, service, and ingress
- backend deployment and service
- PostgreSQL deployment and service
- Redis deployment and service
- Ollama deployment and service

### Build and tag the images

```bash
docker build -f backend/Dockerfile -t omnibridge-backend:latest .
docker build -f frontend/Dockerfile -t omnibridge-frontend:latest .
```

### Apply the manifests

```bash
kubectl apply -f kubernetes/base.yaml
kubectl apply -f kubernetes/postgres.yaml
kubectl apply -f kubernetes/redis.yaml
kubectl apply -f kubernetes/ollama.yaml
kubectl apply -f kubernetes/backend.yaml
kubectl apply -f kubernetes/frontend.yaml
```

### Before using the manifests in a real environment

- Replace placeholder images such as `omnibridge-frontend:latest` and `omnibridge-backend:latest` with real registry images
- Replace `chatbot.example.com` in the ingress with your real domain
- Replace the example DB secret values
- Add OAuth and Gemini environment variables if you need those features
- Replace `emptyDir` volumes with persistent volumes for PostgreSQL and Ollama
- Change the backend bind address from `127.0.0.1` to `0.0.0.0`

### Production readiness note

Treat the Kubernetes manifests as a starting point, not a finished production setup. The general wiring is present, but persistence, secrets management, image publishing, hostnames, and backend network binding still need to be finalized.

## API Surface

### Public app endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/status` | Backend and auth status |
| `GET` | `/api/auth/status` | Current auth state |
| `GET` | `/api/history` | Load stored chat history for authenticated users |
| `POST` | `/api/chat` | Send a chat message with optional file uploads |
| `POST` | `/api/reset` | Clear current in-memory conversation state |
| `POST` | `/api/auth/signup` | Create local account |
| `POST` | `/api/auth/login` | Login with local account |
| `POST` | `/api/auth/logout` | Logout current account |
| `GET` | `/auth/google/start` | Start Google OAuth |
| `GET` | `/auth/google/callback` | Complete Google OAuth |
| `GET` | `/health` | Basic health endpoint |

## Operational Notes

### Guest vs authenticated behavior

- Guests can send up to 10 messages
- Guests do not get upload access
- Authenticated users can upload files and images
- Authenticated users can load prior chat history from PostgreSQL

### Logging and data footprint

- Auth data is stored locally in SQLite
- Message history and vector memory are stored in PostgreSQL when enabled
- Each conversation turn is also appended to a local text file in `user_data/`

### Error handling and fallback behavior

- If Redis is unavailable, session state falls back to local Python memory
- If PostgreSQL is unavailable, the chat endpoint still responds but skips memory-backed personalization
- If Google OAuth is not configured, local email/password auth still works

## Production Checklist

Before calling this production-ready, close the following gaps:

1. Bind the backend to `0.0.0.0` for container and cluster networking.
2. Persist the auth SQLite database or move auth into PostgreSQL.
3. Replace `emptyDir` storage with persistent volumes.
4. Put real secret management in place for DB credentials, OAuth credentials, and any Gemini key.
5. Confirm Ollama model availability and startup behavior on the target host or cluster.
6. Add TLS, a real ingress hostname, and environment-specific `APP_BASE_URL` and redirect URIs.
7. Validate upload size limits and reverse-proxy settings for your deployment target.

## Current State Of The Project

What is already implemented:

- A working browser chat UI
- Local Ollama text and image processing
- Local and Google-based sign-in
- Guest usage limits
- PostgreSQL-backed message history and memory
- Redis-backed session caching with fallback
- Docker and Kubernetes deployment scaffolding

What still needs attention for a serious production rollout:

- container network binding for the backend
- persistent storage strategy for auth and stateful services
- secret handling and image publishing
- environment-specific deployment hardening
