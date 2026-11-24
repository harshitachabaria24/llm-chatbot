# LLM Chatbot with Key Failover and Response Caching

A full-stack LLM chatbot built with React, FastAPI, SQLite, and LiteLLM. It supports multiple API keys with automatic failover, response caching, persistent chat sessions, and per-request metadata.

Every assistant reply displays its source (live inference or cache), the model used, response latency, and input/output token usage.

## 1. Live URLs

| Component | URL |
|---|---|
| Frontend | [Live Chatbot](https://llm-chatbot-phi.vercel.app/) |
| Backend | [Render Backend](https://llm-chatbot-backend-y1z1.onrender.com) |
| Backend Health | [Health Check](https://llm-chatbot-backend-y1z1.onrender.com/health) |
| API Documentation | [Swagger UI](https://llm-chatbot-backend-y1z1.onrender.com/docs) |
| Demo Video | [Watch Project Demo](https://drive.google.com/file/d/1imLfunQKLwCp9lGWHvllcBkpbCpO_3VQ/view?usp=sharing) |

## 2. Run Locally: With Docker and Without Docker

### With Docker

1. Copy `.env.example` to `.env`.
2. Add at least two valid API keys to `LLM_API_KEYS`.
3. Configure `LLM_MODEL` and the other environment variables.
4. Start the application:

```bash
docker compose up --build -d
```

Alternatively, use the Makefile:

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

### Without Docker

**Backend (Python 3.12+)**

Open a terminal in the project directory and run:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

Configure the environment variables in PowerShell:

```powershell
$env:LLM_API_KEYS="key-one,key-two"
$env:LLM_MODEL="gemini/gemini-3.5-flash-lite"
$env:DATABASE_URL="sqlite:///./app.db"
$env:ALLOWED_ORIGINS="http://localhost:3000"
$env:ENABLE_FAILURE_SIMULATION="true"
$env:CACHE_ENABLED="true"
```

Start the backend:

```powershell
uvicorn app.main:app --port 8000
```

**Frontend (Node.js)**

Open a separate terminal:

```powershell
cd frontend
npm install
$env:VITE_API_BASE_URL="http://localhost:8000"
npm run dev
```

**Run backend tests:**

From the backend directory, run:

```bash
pytest
```

The tests do not require real API keys or live provider calls.

### Configuration

All configuration is handled through environment variables. `.env.example` contains the configuration template.

| Variable | Description |
|---|---|
| `LLM_API_KEYS` | Comma-separated API key pool. At least two keys are required. |
| `LLM_MODEL` | LiteLLM model name, e.g. `gemini/gemini-3.5-flash-lite`. |
| `DATABASE_URL` | SQLite database location. |
| `ALLOWED_ORIGINS` | Comma-separated list of permitted frontend origins. |
| `ENABLE_FAILURE_SIMULATION` | Enables the `X-Simulate-Failure` header when set to `true`. |
| `CACHE_ENABLED` | Enables response caching. Default: `true`. |
| `KEY_COOLDOWN_SECONDS` | Cooldown period for a failed API key. Default: `5`. |
| `MAX_HISTORY_MESSAGES` | Maximum number of previous messages sent to the model. Default: `40`. |
| `LLM_TIMEOUT_SECONDS` | Provider request timeout. Default: `30`. |
| `VITE_API_BASE_URL` | Backend URL used by the frontend at build time. |

## 3. Design Decisions: Key Fallback, Caching, and SQLite Session Storage

### Key Fallback

The application uses LiteLLM's Router to manage multiple API keys.

- Each API key is configured as a separate deployment (`key_1`, `key_2`, etc.).
- Requests initially use the first available key.
- If a key encounters a rate limit (429), quota error, server error (5xx), or timeout, the Router attempts the next key.
- If all configured keys fail, the API returns a `503 Service Unavailable` response.
- API key values are redacted from logs.

The response metadata includes:

- `key_id`: Identifies the API key deployment that successfully generated the response.
- `fallback_attempts`: Number of earlier keys that failed or were unavailable.

A failed key is temporarily placed on cooldown to avoid repeatedly sending requests to a rate-limited provider.

The `X-Simulate-Failure` header allows controlled failure simulation when `ENABLE_FAILURE_SIMULATION=true`. It helps demonstrate failover without deliberately exhausting real API quotas.

### Response Caching

The application uses LiteLLM's built-in in-memory response cache.

- The cache key includes the model and the complete message list sent to the model.
- This includes the system prompt, conversation history, and new user message.
- Identical requests can be served from the cache, including the same first message in a new session.
- Different messages or system prompts produce different cache keys.
- Cache responses include the original reply and token usage.
- On a cache hit, `key_id` is `null` and `fallback_attempts` is `0`.

The cache is in-memory, so cached responses are cleared when the backend restarts. Conversation history is stored separately in SQLite.

### SQLite Session Storage

SQLite is used to store conversation history and session information.

- The `messages` table stores user and assistant messages.
- A `sessions` table stores the system prompt associated with each session.
- An index on `(session_id, id)` supports efficient message retrieval.
- User messages and assistant replies are saved together in one transaction, only after a successful response.
- Failed requests are not stored in conversation history.
- Unknown sessions return a `404 Not Found` response.
- The backend retrieves previous messages and sends the complete conversation context to the LLM.
- A custom system prompt can be set for a session and reused in subsequent requests.
- Input and output token counts are reported per request, not as cumulative session totals.

## 4. Verification: Failover, Cache Hits, and Token Usage

Replace `$B` with the backend URL.

The following examples use Bash or Git Bash:

```bash
B=http://localhost:8000

# Health check
curl $B/health

# Live inference
curl -s -X POST $B/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-1","message":"What is cloud computing?"}'

# Cache hit: same first message in a NEW session
curl -s -X POST $B/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-2","message":"What is cloud computing?"}'

# Expected: resolution_source = LLM_CACHE

# Failover: simulate key_1 returning 429
curl -s -X POST $B/chat \
  -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"demo-3","message":"Name a prime number."}'

# Expected: key_2 answers, fallback_attempts = 1

# Simulate all keys failing
curl -s -i -X POST $B/chat \
  -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"demo-4","message":"Hello"}'

# Expected: 503 response; failed request is not stored

# Retrieve conversation history
curl -s $B/sessions/demo-3/messages
```

The same checks can be performed using the FastAPI Swagger UI at:

[http://localhost:8000/docs](http://localhost:8000/docs)

In the frontend, every assistant reply displays its response source, model, latency, and input/output tokens.

Open **Session settings** to configure a system prompt or enter a failure simulation such as `key_1=429`.

## 5. Setup Used

- **Operating System:** Windows
- **Code Editor:** Visual Studio Code
- **AI Assistance:** Claude and ChatGPT (for coding guidance, debugging, and development support)
- **Backend:** Python, FastAPI, LiteLLM
- **Frontend:** React, Vite
- **Database:** SQLite
- **Containerization:** Docker Desktop and Docker Compose
- **Testing:** Pytest and FastAPI Swagger UI
- **Version Control:** Git and GitHub
- **Deployment:** Render (backend) and Vercel (frontend)

## 6. Bonus Features

- **Router cooldown:** Temporarily avoids retrying rate-limited API keys.
- **Key redaction:** Prevents sensitive API key values from appearing in logs.
- **Per-session system prompts:** Allows users to customize assistant behavior for individual sessions.
- **Failure simulation:** Makes it possible to demonstrate provider failover directly through the UI.
- **Per-session locking:** Keeps concurrent messages within a session ordered.
- **History cap:** Limits the conversation history sent to the model.
- **Cache warm-up:** Reduces the delay associated with the first cache hit.

## 7. Deployment

### Backend — Render

The FastAPI backend is deployed as a Docker Web Service using the `backend/` directory.

Configuration:

- Runtime: Docker
- Root directory: `backend`
- Docker build context: `.`
- Dockerfile: `Dockerfile`
- Health check path: `/health`

Environment variables are configured through the Render dashboard, including `LLM_API_KEYS`, `LLM_MODEL`, `DATABASE_URL`, `ALLOWED_ORIGINS`, `ENABLE_FAILURE_SIMULATION`, and `CACHE_ENABLED`.

The backend is hosted on Render's free instance. Free instances can spin down after inactivity, which may cause delays on the first request.

**Storage limitation:** The free Render instance does not provide a persistent disk. SQLite works during runtime, but deployed conversation history is not guaranteed to survive restarts, redeployments, or instance replacement. For persistent deployed SQLite storage, a persistent disk mounted at `/data` is required.

The local Docker setup uses a named volume mounted at `/data`, which preserves SQLite history across container restarts.

### Frontend — Vercel

The React frontend is deployed through Vercel using the GitHub repository.

Configuration:

- Framework: Vite
- Root directory: `frontend`
- Build command: `npm run build`
- Output directory: `dist`

The following environment variable is configured in Vercel:

```text
VITE_API_BASE_URL=https://llm-chatbot-backend-y1z1.onrender.com
```

The frontend origin is configured in the backend's `ALLOWED_ORIGINS` environment variable to allow cross-origin requests.

### Live Deployment

- Frontend: [https://llm-chatbot-phi.vercel.app/](https://llm-chatbot-phi.vercel.app/)
- Backend: [https://llm-chatbot-backend-y1z1.onrender.com](https://llm-chatbot-backend-y1z1.onrender.com)