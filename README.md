# LLM Chatbot with Key Failover and Response Caching

A small full-stack chat app: React frontend, FastAPI backend, SQLite storage, and a LiteLLM gateway that fails over between API keys and caches responses. Every assistant reply shows where it came from (cache or live model), which model answered, how long it took and how many tokens that one request used.

## 1. Live URLs

| | URL |
|---|---|
| Frontend | _add after deploying_ |
| Backend | _add after deploying_ |
| Backend health | `<backend-url>/health` |
| Demo video | _add link_ |

Deployment notes are at the end of this file.

## 2. Run locally: with Docker and without

Copy and paste this directly into your `README.md`:

````

### With Docker

1. Copy `.env.example` to `.env`.
2. Add at least two valid API keys to `LLM_API_KEYS`.
3. Configure `LLM_MODEL` and other environment variables.
4. Start the application:

```bash
docker compose up --build -d
```

Or use:

```bash
make up
```

The application will be available at:

- Frontend: [http://localhost:3000](http://localhost:3000)
- Backend: [http://localhost:8000](http://localhost:8000)
- Health: [http://localhost:8000/health](http://localhost:8000/health)

Stop the application:

```bash
make down
```

The named SQLite volume `app_data` is retained when containers are stopped. Do not use `docker compose down -v` unless you intend to delete the database volume.

````

### Without Docker

Backend (Python 3.12+):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export LLM_API_KEYS=key-one,key-two
export LLM_MODEL=gemini/gemini-3.5-flash-lite
export DATABASE_URL=sqlite:///./data/app.db
export ALLOWED_ORIGINS=http://localhost:3000
export ENABLE_FAILURE_SIMULATION=true
uvicorn app.main:app --port 8000
```

Frontend (Node 20+):

```bash
cd frontend
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev    # http://localhost:3000
```

Backend tests (no network or real keys needed): `make test` or `cd backend && pytest`.

### Configuration

All configuration is through environment variables. `.env.example` lists every one.

| Variable | Meaning |
|---|---|
| `LLM_API_KEYS` | Comma-separated key pool, at least 2. Ids are `key_1`, `key_2`, ... in this order |
| `LLM_MODEL` | LiteLLM model name, e.g. `gemini/gemini-3.5-flash-lite`. Must match the provider of your keys |
| `DATABASE_URL` | SQLite location, e.g. `sqlite:////data/app.db` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins |
| `ENABLE_FAILURE_SIMULATION` | `true` enables the `X-Simulate-Failure` header |
| `CACHE_ENABLED` | Default `true` |
| `KEY_COOLDOWN_SECONDS` | How long a failed key rests before it is tried again. Default 5 |
| `MAX_HISTORY_MESSAGES` | Most recent messages sent to the model per request. Default 40 |
| `LLM_TIMEOUT_SECONDS` | Per-call provider timeout. Default 30 |
| `VITE_API_BASE_URL` | Frontend build-time backend URL |

## 3. Design decisions: key fallback, caching, SQLite session storage

### Key fallback

The gateway uses **LiteLLM's `Router`**. Each API key becomes one deployment (`key_1`, `key_2`, ...) and `fallbacks` chains each key to the ones after it. A request always starts at `key_1`. If a key returns 429, a quota error, a 5xx, or times out, the Router moves to the next key automatically. If every key fails, the API returns `503 {"detail": "all providers unavailable"}`; provider errors never reach the client, and key values are redacted from logs.

- `key_id` in the response is the deployment that answered. `fallback_attempts` is its position in the pool, i.e. how many earlier keys failed or were resting.
- A key that just failed is put on a short cooldown (`KEY_COOLDOWN_SECONDS`) so a rate-limited key is not hammered on every request.
- `X-Simulate-Failure` (only when `ENABLE_FAILURE_SIMULATION=true`) builds a throwaway Router for that one request in which the listed keys raise the given status. The failover itself still runs through LiteLLM, but the real Router's cooldown state is untouched. A simulated request skips the cache read so it always returns a real, live answer from a surviving key.

### Caching

LiteLLM's built-in cache (`litellm.enable_cache`, in-memory) is used, and a hit is detected from LiteLLM's own `cache_hit` flag. The cache key is the model plus the full message list sent to the model (system prompt + history + new message), so the same first message in a new session is a hit, and a different message or system prompt is never served from cache. On a hit the stored reply and its stored token usage are returned, `key_id` is `null` and `fallback_attempts` is 0.

Notes: LiteLLM 1.110 removed its disk cache backend, so the in-memory cache is used (allowed by the spec); it is empty after a restart, while conversation history is not. The cache-hit path is warmed up at startup so the first real hit is not slowed by lazy imports.

### SQLite session storage

- `messages` table exactly as specified, plus an index on `(session_id, id)` and a small `sessions` table that stores the per-session system prompt. The schema is created on startup.
- The user message and assistant reply are written in **one transaction, only after a successful reply**. A 422 or 503 therefore never appears in history, and an unknown or never-answered session is a 404.
- The client sends only the new message. The backend loads history, builds `[system, ...history, new message]`, and keeps turns within one session serialized so concurrent sends cannot interleave.
- A `system_prompt` in a request overrides the default for that session and is remembered for later requests.
- Token counts are per request (system + history + new message in, one reply out), never running totals.

## 4. Verification: how to see failover, cache hits and per-message token usage

Replace `$B` with the backend URL.

```bash
B=http://localhost:8000

# Health
curl $B/health

# Live inference, then a cache hit (same first message, NEW session)
curl -s -X POST $B/chat -H 'content-type: application/json' \
  -d '{"session_id":"demo-1","message":"What is cloud computing?"}'
curl -s -X POST $B/chat -H 'content-type: application/json' \
  -d '{"session_id":"demo-2","message":"What is cloud computing?"}'
#  -> second response: "resolution_source":"LLM_CACHE", same reply, key_id null, lower latency_ms

# Failover: key_1 "returns" 429, key_2 answers
curl -s -X POST $B/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"demo-3","message":"Name a prime number."}'
#  -> 200, "key_id":"key_2","fallback_attempts":1

# Every key fails: clean 503, nothing stored
curl -s -i -X POST $B/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"demo-4","message":"Hello"}'
curl -s -i $B/sessions/demo-4/messages        # 404 session not found

# Multi-turn memory and stored history
curl -s -X POST $B/chat -H 'content-type: application/json' \
  -d '{"session_id":"demo-5","message":"My name is harshita."}'
curl -s -X POST $B/chat -H 'content-type: application/json' \
  -d '{"session_id":"demo-5","message":"What is my name?"}'
curl -s $B/sessions/demo-5/messages

# Validation: 422
curl -s -i -X POST $B/chat -H 'content-type: application/json' -d '{"session_id":"s","message":""}'
```

In the UI, every assistant reply shows its source, model, latency and input/output tokens. Open **Session settings** to set a system prompt or to type a failure simulation such as `key_1=429` (sent as the `X-Simulate-Failure` header). Reload the page for a new session; send the same first message again to see `LLM_CACHE`.

## 5. Setup Used: your editor and AI tools


### Setup Used

- **Development Environment:** Windows
- **Code Editor:** Visual Studio Code
- **AI Assistance:** Claude (for coding guidance, debugging, and development support)
- **Containerization:** Docker Desktop
- **Backend Testing:** Pytest and FastAPI Swagger UI
- **Version Control:** Git and GitHub


## 6. Bonus features (if any): what you added beyond the requirements and why

- **Router cooldown** so a rate-limited key is not retried on every request.
- **Key redaction in logs**, because provider errors can echo request URLs.
- **Per-session system prompt** field in the UI, persisted for the session.
- **Failure-simulation field in the UI** so failover can be demonstrated without a terminal.
- **Per-session locking** so quick double-sends in the same session stay ordered.
- **History cap** (`MAX_HISTORY_MESSAGES`) to keep long chats inside the model's context window.
- **Cache warm-up at startup** so the first real cache hit is fast.

## Deployment notes

- **Backend** (Render, Railway, Fly.io, Hugging Face Spaces): deploy the `backend/` Dockerfile. Set `LLM_API_KEYS`, `LLM_MODEL`, `ALLOWED_ORIGINS` (your frontend URL), `ENABLE_FAILURE_SIMULATION=true`, and `DATABASE_URL`. To keep history across restarts, attach a persistent volume at `/data` (Render needs a paid disk; Fly.io volumes work well). Without one, SQLite still works but resets when the container is replaced.
- **Frontend** (Vercel, Netlify, Cloudflare Pages): root directory `frontend/`, build command `npm run build`, output `dist`, and set `VITE_API_BASE_URL` to the backend URL **before building**.
- Finally, add the frontend origin to the backend's `ALLOWED_ORIGINS` and redeploy.
