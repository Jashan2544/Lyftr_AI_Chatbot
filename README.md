# LLM Chatbot with Key Failover and Response Caching

React (Vite) frontend · FastAPI backend · LiteLLM gateway · SQLite storage · Docker.

```
/backend     FastAPI app, LiteLLM gateway, SQLite, pytest suite
/frontend    React chat UI (Vite), served by nginx in Docker
docker-compose.yml  Makefile  .env.example  render.yaml
```

## 1. Live URLs

| Tier | URL |
|---|---|
| Frontend | `https://<your-app>.vercel.app` |
| Backend | `https://<your-backend>.onrender.com` — try `/health` |
| Repository | `https://github.com/Jashan2544/Lyftr_AI_Chatbot` |

## 2. Run locally: with Docker and without

### With Docker

```bash
cp .env.example .env        # then put 2+ real keys in LLM_API_KEYS
make up                     # frontend http://localhost:3000, backend http://localhost:8000
make down
```

`make up` creates `.env` from the example if it doesn't exist. SQLite (`/data/app.db`) and the LiteLLM disk cache (`/data/llm_cache`) live on the named volume `app-data`, so history survives `make down && make up`. `make clean` also removes the volume.

You can try it without API keys by setting `MOCK_LLM=true`. Replies then come from LiteLLM's `mock_response`, and the cache, failover, tokens and history all behave the same way.

### Without Docker

```bash
# backend (Python 3.11+)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export LLM_API_KEYS=key1,key2 DATABASE_URL=sqlite:///./app.db ENABLE_FAILURE_SIMULATION=true CACHE_TYPE=local
uvicorn app.main:app --port 8000
python -m pytest -q          # 26 tests, offline (MOCK_LLM)

# frontend (Node 18+)
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

## 3. Design decisions: key fallback, caching, SQLite session storage

**Request flow (`POST /chat`)**

```
validate (422) → load session history from SQLite → build [system, ...history, user]
   → LiteLLM cache lookup (model + full message list) ── hit ──► LLM_CACHE, key_id=null
   → miss: key_1 → key_2 → … via litellm.acompletion(api_key=…, num_retries=0)
        429 / quota / 5xx / timeout / connection / auth error → next key
        success → write to LiteLLM cache → LIVE_INFERENCE
        all failed → 503 {"detail": "all providers unavailable"}
   → on success only: persist user + assistant turn in one transaction
```

**Key fallback**
- `LLM_API_KEYS` becomes an ordered pool of slots `key_1..key_n`. Each slot can have its own model through `LLM_MODELS`, so the pool can span models and providers.
- Failover order is fixed: key_1, then key_2, and so on. That keeps `key_id` and `fallback_attempts` predictable, which is what the contract and the `X-Simulate-Failure` table expect. LiteLLM's own per-key retries are turned off (`num_retries=0`) so a failing key hands over immediately instead of burning seconds on backoff.
- Any provider exception moves on to the next key. That covers LiteLLM `RateLimitError` (429, including Gemini `RESOURCE_EXHAUSTED` quota errors), 5xx, timeouts, connection errors and invalid or revoked keys. The user never sees a stack trace. Validation errors return 422, total outage returns 503, and every other error returns 500 with `{"detail": ...}` JSON.
- Key secrets never leave the server. Responses carry only `key_id`.
- **`X-Simulate-Failure`** only works when `ENABLE_FAILURE_SIMULATION=true`. It is parsed per request, and a listed key raises the HTTP status it was given right before its provider call, so it goes through exactly the same failover path as a real error. Requests that carry the header skip the cache *read* (results are still written). Otherwise a previously cached question would return `LLM_CACHE` with `key_id=null`, and the failover demo would prove nothing.

**Caching**
- Uses LiteLLM's built-in `litellm.caching.Cache`: `local` (in-memory) or `disk` (persists on the Docker volume), chosen by `CACHE_TYPE`.
- The cache key is `Cache.get_cache_key(model, messages)`, where `messages` is the **full** list sent to the LLM (system prompt, history and new message). The same first message in a new session therefore hits the cache. A different message, a different system prompt or different history never does.
- The gateway calls the cache explicitly instead of passing `caching=True`. LiteLLM writes its implicit cache in a background logging task, so a request sent immediately afterwards could miss. Calling it directly makes the `LLM_CACHE` / `LIVE_INFERENCE` label exact and testable.
- On a hit, the response returns the stored reply text and the stored usage tokens, with `key_id=null` and `fallback_attempts=0`. Hits take only a few milliseconds, against hundreds of milliseconds for a live call.

**SQLite session storage**
- `DATABASE_URL=sqlite:////data/app.db`. The schema (the required `messages` table plus a `sessions` table that stores the per-session system prompt, and an index on `(session_id, id)`) is created on startup. WAL mode is enabled.
- The user and assistant turns are written **in one transaction after the LLM succeeds**, so a request that fails with 422 or 503 never appears in history.
- The client sends only the new message. The server rebuilds context from SQLite, capped at the last `HISTORY_LIMIT` messages to bound token cost. The system prompt is stored per session and never returned by `GET /sessions/{id}/messages`.
- Tokens are always per request: `input_tokens` is the provider's prompt token count for that one call (system, history and new message), never a running total.

## 4. Verification: how to see failover, cache hits and per-message token usage

```bash
B=https://<your-backend>   # or http://localhost:8000
curl $B/health
# {"status":"ok","keys_configured":2,"cache_enabled":true}

# Live call, then a cache hit (same first message, new session)
curl -s -XPOST $B/chat -H 'Content-Type: application/json' -d '{"session_id":"s-a","message":"What is the capital of France?"}'
curl -s -XPOST $B/chat -H 'Content-Type: application/json' -d '{"session_id":"s-b","message":"What is the capital of France?"}'
#   → resolution_source LLM_CACHE, identical reply, key_id null, lower latency_ms

# Failover
curl -s -XPOST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=429' -d '{"session_id":"s-a","message":"And of Germany?"}'
#   → 200, key_id key_2, fallback_attempts 1
curl -s -XPOST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: *=429' -d '{"session_id":"s-a","message":"x"}'
#   → 503 {"detail":"all providers unavailable"}, not added to history

# Multi-turn history and 404
curl -s $B/sessions/s-a/messages
curl -s $B/sessions/unknown/messages     # 404 {"detail":"session not found"}

# Validation
curl -s -XPOST $B/chat -H 'Content-Type: application/json' -d '{"session_id":"s-a","message":"   "}'   # 422
```

**In the UI:** every assistant reply shows chips for the source (`LIVE_INFERENCE` in green, `LLM_CACHE` in purple), the model, latency in ms, `in`/`out` tokens for that request, the `key_id`, and a failover badge. The **Failover test** dropdown sends `X-Simulate-Failure` (`key_1=429`, `*=429`, …), so the whole demo can be done from the browser. Errors appear as red messages in the chat. Reloading the page starts a new session, and so does **New chat**.

**Tests:** `make test` (or `cd backend && pytest -q`) runs 26 tests covering the contract shape, cache hit and miss, per-request tokens, multi-turn memory and session isolation, 404, every 422 case, simulated and real LiteLLM-exception failover, 503 with nothing persisted, and simulation turned off.

## 5. Setup Used

- Editor: VS Code
- AI tools: Claude (architecture, code generation, tests, README)
- Python 3.12, Node 20, Docker Desktop

## 6. Bonus features (if any)

- **Failover dropdown in the UI**, so `X-Simulate-Failure` can be demonstrated without curl.
- **`fallback_attempts` and `key_id` shown on each reply**, so failover is visible as well as logged.
- **Disk-backed LiteLLM cache** on the Docker volume, so cache hits survive restarts just like history.
- **Per-key models (`LLM_MODELS`)**, so the pool can span models and providers (e.g. `gemini/gemini-2.5-flash,gemini/gemini-2.5-flash-lite`).
- **`MOCK_LLM=true` offline mode** using LiteLLM's `mock_response`, for CI and keyless local runs.
- **Runtime frontend config**: the nginx image writes `env.js` from `API_URL` at container start, so one image works against any backend.
- **Hardening**: non-root container user, Docker `HEALTHCHECK`, a history window that bounds token cost, and a catch-all JSON 500 handler.
