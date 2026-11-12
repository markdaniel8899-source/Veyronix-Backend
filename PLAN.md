# 🚀 Veyronix SaaS Product — Advanced Deployment Plan

> **Status:** Advanced Plan (Ultra-Lightweight, Crash-Proof, RAM-Optimized)  
> **Product:** Veyronix SaaS Product — standalone name, theme, brand  
> **Engine:** Veyronix (multi-agent swarm intelligence prediction engine)  
> **Primary LLM:** NVIDIA Nemotron 3.5 Super VL 120B A12B (via NVIDIA NIM)  
> **Storage:** Local SQLite (JSON1 + FTS5) — Zep Cloud REMOVED  
> **Execution:** Asyncio + Semaphore — Subprocess REMOVED  
> **Target:** Oracle Cloud Always Free VM (1 GB RAM, 48 GB Disk)

---

## 1. Executive Summary

Hum Veyronix ko ek **ultra-lightweight SaaS product** ke tor par deploy kar rahe hain jisme koi Zep Cloud dependency nahi hai aur koi subprocess nahi hai. Puri backend logic local SQLite (JSON1 + FTS5) aur asyncio par chalti hai taake 1 GB RAM Oracle VM par OOM crashes na hon. Product ka apna naam, apni theme, apna branding hoga — koi ATLAS ya third-party branding nahi.

**Deployment Stack:**
- **Frontend:** Vercel (Free Tier) — React + Vite + Tailwind
- **Backend:** Oracle Cloud Always Free VM — Python 3.12 + Flask + uv + asyncio + sqlite3
- **Reverse Proxy:** Caddy (Automatic HTTPS, Rate Limiting, Bearer Token Auth)
- **Storage:** Local SQLite (JSON1 + FTS5) on Oracle server disk
- **LLM Provider:** NVIDIA NIM (Free Tier, OpenAI-compatible API) — **Nemotron 3.5 Super VL 120B A12B** for both actions and reports
- **Cost:** Mostly ₹0 to start

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                            │
│                   (Vue 3 + Vite + Tailwind)                      │
└───────────────────────────────────────┬─────────────────────────┘
                                        │ HTTPS API Calls (Bearer Token)
                                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    VERCEL FRONTEND (Free Tier)                   │
│         React + Vite + Tailwind + Custom Branding                │
│      https://your-product.vercel.app                             │
└───────────────────────────────────────┬─────────────────────────┘
                                        │ CORS + Bearer Token Auth
                                        ▼
┌─────────────────────────────────────────────────────────────────┐
│              ORACLE CLOUD VM — Always Free Tier                  │
│              Ubuntu | 1 GB RAM | 48 GB Disk                      │
│                                                                  │
│  ┌─────────────────┐  ┌──────────────────────────────────────┐ │
│  │     Caddy       │──│  Reverse Proxy + Auto SSL +          │ │
│  │  (Port 80/443)  │──│  Rate Limiting + Bearer Token Auth   │ │
│  └─────────────────┘  └──────────────────────────────────────┘ │
│                                                                  │
│  ┌─────────────────┐  ┌──────────────────────────────────────┐ │
│  │  Flask Backend  │──│  Python 3.12 + uv + asyncio +        │ │
│  │   (Port 5001)   │──│  Local SQLite (JSON1 + FTS5)         │ │
│  └─────────────────┘  └──────────────────────────────────────┘ │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Local Storage: /data/veyronix.db (SQLite WAL mode)     │  │
│  │  Available for user data: ~25–30 GB                      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  LLM Router: Nemotron 3.5 Super VL 120B A12B (actions)  │  │
│  │  LLM Router: Nemotron 3.5 Super VL 120B A12B (reports)  │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                                        │ LLM API Calls (OpenAI-compatible)
                                        ▼
┌─────────────────────────────────────────────────────────────────┐
│           NVIDIA NIM (Free Tier, OpenAI-compatible)             │
│   Model: nemotron-3.5-super-vl-120b-a12b                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. What Is Simulation? (5 Phases + Advanced Features)

Veyronix ka simulation ye hai:

| Phase | Kya Hota Hai |
|-------|-------------|
| **1. Graph Building** | Seed document se entities aur relationships nikal kar local SQLite knowledge graph banaya jata hai. |
| **2. Environment Setup** | Har AI agent ki personality (persona) generate hoti hai. Agent configuration inject ki jati hai. |
| **3. Simulation** | Agents ek doosre se messages bhejte hain (Twitter/Reddit style). Dynamic memory update hoti rehti hai. Asyncio + Semaphore control karta hai RAM usage. |
| **4. Report Generation** | ReportAgent simulation ke baad final prediction report nikalta hai (Nemotron 3.5 Super VL 120B A12B model use karta hai reports ke liye). |
| **5. Deep Interaction** | User kisi agent ya ReportAgent se chat kar sakta hai simulated world mein. |

### 🆕 Advanced New Features

| Feature | Description |
|---------|-------------|
| **Memory Consolidation** | Har 5 rounds ke baad, agent state ka 2-sentence summary LLM se banwaya jata hai, save kiya jata hai `memory_summaries` table mein, aur raw interactions delete kar diye jaate hain taake RAM bache. |
| **Market Shock Injection** | Mid-simulation mein koi naya event (jaise: policy change, news crash) inject kiya ja sakta hai active asyncio loop mein taake real-world scenarios test kiye ja saken. |
| **Domain Availability Check** | Frontend results page par check hota hai ke suggested SaaS product name ka domain available hai ya nahi. |

---

## 3.5 Chunked Simulation Execution with Rate Limiting

### Why Chunking is Needed

NVIDIA NIM and Groq free tiers enforce strict **RPM (Requests Per Minute)** limits — typically **10 RPM** by default. If all agents in a round fire LLM calls simultaneously, the system will immediately hit **429 Too Many Requests** errors, which can lead to temporary API key bans and simulation failure. Continuous, unthrottled agent calls are not viable on free-tier LLM providers for long-running simulations.

### How Chunking Works

Instead of firing all agent calls at once, agents are grouped into **small chunks** and executed sequentially with controlled delays between chunks.

**Example:** A simulation round with 50 agents is split into chunks of 3 agents each:
- Chunk 1: Agents 1–3 → execute → wait 6 seconds
- Chunk 2: Agents 4–6 → execute → wait 6 seconds
- ...
- Chunk 17: Agents 49–50 → execute → round complete

This spacing respects the 10 RPM limit (6 seconds between chunks = ~10 requests per minute).

A **Token Bucket Rate Limiter** controls the flow, ensuring tokens are consumed per request and replenished over time. If a 429 error is received, **exponential backoff** is applied (1s → 2s → 4s → 8s...) before retrying.

**Configurable via environment variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_RPM` | `10` | Maximum requests per minute allowed by LLM provider |
| `LLM_MAX_CONCURRENT` | `3` | Maximum concurrent LLM calls within a chunk |

### Implementation Details

- **New file:** `backend/app/services/rate_limiter.py` — Implements token bucket rate limiting logic and exponential backoff for 429 errors.
- **`simulation_runner.py`** refactored to use `asyncio` + `rate_limiter.py` — **NO subprocess**. The runner processes agents in chunks, awaiting the rate limiter before each chunk.
- **Real-time progress feedback** is sent to the frontend:
  - `Round 3/10 — Chunk 2/5 — Waiting 6s for rate limit...`
  - `Round 3/10 — Chunk 3/5 — Processing agents...`

### Benefits

| Benefit | Description |
|---------|-------------|
| ✅ API limits never hit | Chunking + token bucket keeps requests within free-tier RPM |
| ✅ Smooth long-running simulations | Simulations can run for hours without 429 errors |
| ✅ Realistic agent "thinking time" | Delays between chunks simulate natural agent deliberation |
| ✅ Free-tier friendly | Works within Groq / NVIDIA NIM free-tier quotas |

---

## 4. Tech Stack & Decisions

| Layer | Technology | Decision | Reason |
|-------|-----------|----------|--------|
| Frontend Framework | React 19 + Vite | ✅ Same as Veyronix | Fast, modern |
| Styling | Tailwind CSS | ✅ Custom theme | Separate branding |
| Backend | Flask + Python 3.12 | ✅ Required | `pyproject.toml` says `>=3.11,<3.13` |
| Async Execution | asyncio + Semaphore(3) | ✅ NEW | Prevents OOM on 1 GB RAM |
| Database | SQLite (JSON1 + FTS5) | ✅ NEW | Replaces Zep Cloud, zero external cost |
| Package Manager | uv | ✅ Fast Python installs | Already installed on system |
| Reverse Proxy | Caddy | ✅ Auto SSL + Rate Limiting + Bearer Auth | Lightweight reverse proxy |
| Memory/Graph | Zep Cloud | ❌ REMOVED | Replaced by local SQLite |
| Local Storage | SQLite / JSON files | ✅ Decision: Local SQLite | No monthly cost, unlimited control, RAM-optimized |
| LLM Router (Small) | nemotron-3.5-super-vl-120b-a12b | ✅ NEW | For agent actions, fast & cheap |
| LLM Router (Large) | nemotron-3.5-super-vl-120b-a12b | ✅ NEW | For report generation, high accuracy |
| LLM Provider | NVIDIA NIM | ✅ Free Tier | OpenAI-compatible API, nemotron-3.5-super-vl-120b-a12b model |
| Frontend Hosting | Vercel | ✅ Free Tier | Easy deploy, custom domain later |
| Backend Hosting | Oracle Cloud Always Free | ✅ 1 GB RAM, 48 GB Disk | Free to start |

---

## 5. File Paths (Current System)

| File/Folder | Path |
|-------------|------|
| Veyronix Repo | `C:\Scrap\Veyronix` |
| Veyronix Backend | `C:\Scrap\Veyronix\backend` |
| Veyronix Frontend | `C:\Scrap\Veyronix\frontend` |
| Final Plan | `C:\Scrap\Veyronix\PLAN.md` |
| Python Required | **3.12** (must install separately) |
| Local DB Path | `C:\Scrap\Veyronix\backend\uploads\veyronix.db` |

---

## 6. Code Changes Required (Backend)

### Files to Delete (Zep Removal)
1. `backend/app/utils/zep.py` — Delete
2. `backend/app/utils/zep_paging.py` — Delete
3. `backend/app/utils/zep_lifecycle.py` — Delete
4. `backend/app/services/zep_tools.py` — Delete
5. `backend/app/services/zep_entity_reader.py` — Delete
6. `backend/app/services/zep_graph_memory_updater.py` — Delete

### Files to Modify
1. `backend/app/services/graph_builder.py` — Replace Zep with local SQLite graph builder
2. `backend/app/config.py` — Remove Zep validation, add SQLite path + LLM Router config
3. `backend/app/services/simulation_runner.py` — Replace subprocess with asyncio + Semaphore

### New Files to Add
1. `backend/app/services/local_memory.py` — SQLite (JSON1 + FTS5) core storage with memory consolidation
2. `backend/app/services/async_simulation_runner.py` — Asyncio-based simulation runner with Semaphore(3)

### Environment Variables (`.env` at Veyronix root):
```env
SECRET_KEY=your-secret-key
FLASK_DEBUG=False

# ===== NVIDIA NIM — Primary LLM Provider =====
# Nemotron 3.5 Super VL 120B A12B model via NVIDIA NIM
# Paste your NVIDIA API key below (get it from https://build.nvidia.com/)
LLM_API_KEY=your_nvidia_api_key_here
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL_NAME=nemotron-3.5-super-vl-120b-a12b

# LLM Router (Dynamic — both use Nemotron 3.5 Super VL 120B A12B)
LLM_ROUTER_SMALL=nemotron-3.5-super-vl-120b-a12b
LLM_ROUTER_LARGE=nemotron-3.5-super-vl-120b-a12b

# ZEP — REMOVED (local SQLite instead)
# ZEP_API_KEY=not_needed

# Storage
STORAGE_TYPE=local
STORAGE_PATH=./uploads
DATABASE_PATH=./uploads/veyronix.db

# Simulation
OASIS_DEFAULT_MAX_ROUNDS=5
MEMORY_CONSOLIDATION_INTERVAL=5  # Every 5 rounds

# Rate Limiting (for NVIDIA NIM free tier — typically 10 RPM)
LLM_RPM=10
LLM_MAX_CONCURRENT=3

# Security
API_BEARER_TOKEN=your-secure-bearer-token

# Logging
LOG_LEVEL=INFO
LOG_FILE=./logs/veyronix.log
```

---

## 7. Security Upgrades

| Security Measure | Implementation |
|-----------------|----------------|
| **Bearer Token Auth** | All Flask API endpoints require `Authorization: Bearer <token>` header |
| **Caddy Rate Limiting** | Caddyfile configured with `rate_limit` to prevent abuse |
| **CORS** | Only Vercel domain allowed |
| **Input Validation** | All JSON payloads validated before processing |

---

## 7.5 Logging Configuration

Veyronix writes structured logs to both console and file for production debugging on Oracle Cloud.

**Log Configuration:**
- **Log Level:** `INFO` (configurable via `LOG_LEVEL` env var)
- **Log File:** `./logs/veyronix.log` (configurable via `LOG_FILE` env var)
- **Log Rotation:** Handled by `logrotate` on Oracle VM (daily, keep 7 days)
- **Log Format:** JSON structured logs with timestamp, level, module, message

**Backend Log Location:**
```
/home/ubuntu/Veyronix/logs/veyronix.log
```

**View Logs:**
```bash
# Tail the log file
tail -f /home/ubuntu/Veyronix/logs/veyronix.log

# View errors only
grep "ERROR" /home/ubuntu/Veyronix/logs/veyronix.log

# View last 100 lines
tail -n 100 /home/ubuntu/Veyronix/logs/veyronix.log
```

**Log Categories:**
| Category | Description |
|----------|-------------|
| `Veyronix.api` | HTTP request/response logs |
| `Veyronix.llm` | LLM API call logs (NVIDIA NIM) |
| `Veyronix.simulation` | Simulation execution logs |
| `Veyronix.memory` | SQLite memory store logs |
| `Veyronix.rate_limiter` | Rate limiter throttling logs |

---

## 8. Oracle Cloud Deployment Steps

### Step 1: Create VM
- Oracle Cloud → Always Free → Create VM Instance
- Image: Ubuntu 22.04 LTS
- Shape: ARM Ampere A1 (1 OCPU, 1 GB RAM) OR AMD (1/8 OCPU, 1 GB RAM)
- Boot Volume: 48 GB (default free)
- SSH Key: Generate and save
- **Security List:** Open port 80 (HTTP), 443 (HTTPS), 22 (SSH)

### Step 2: Connect via SSH
```bash
ssh -i your-key.pem ubuntu@<oracle-public-ip>
```

### Step 3: Install Python 3.12
```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev curl git
```

### Step 4: Install uv
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 5: Clone Veyronix & Setup Backend
```bash
git clone https://github.com/666ghj/Veyronix.git
cd Veyronix/backend
uv python install 3.12
uv sync --python 3.12
```

### Step 6: Create `.env`
```bash
cp .env.example .env
nano .env
```
Add LLM API key, Bearer Token, storage config, etc.

### Step 7: Install Caddy
```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy

# The rate_limit directive needs the third-party module
caddy add-package github.com/mholt/caddy-ratelimit
sudo systemctl restart caddy
```

### Step 8: Configure Caddy (`/etc/caddy/Caddyfile`)
The repository ships a ready-to-use `Caddyfile` at the project root. Copy it and
set the domain through the Caddy service environment:

```bash
sudo cp /opt/veyronix/Caddyfile /etc/caddy/Caddyfile
sudo systemctl edit caddy   # add: Environment=VEYRONIX_DOMAIN=api.example.com
sudo systemctl restart caddy
```

```
{$VEYRONIX_DOMAIN:localhost} {
    encode zstd gzip

    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy no-referrer
        -Server
    }

    rate_limit {
        zone api_read {
            match {
                path /api/*
                method GET
            }
            key    {http.request.remote.host}
            window 1m
            events 180
        }
        zone api_write {
            match {
                path /api/*
                method POST PUT DELETE
            }
            key    {http.request.remote.host}
            window 1m
            events 60
        }
    }

    reverse_proxy 127.0.0.1:5001 {
        header_up X-Real-IP {http.request.remote.host}
        header_up X-Forwarded-Proto {scheme}
    }
}
```

Notes:
- CORS is handled by Flask (`CORS_ORIGINS` in `backend/.env`). Caddy must not
  add `Access-Control-*` headers or the browser sees duplicates.
- `Authorization` headers are forwarded unchanged for the Bearer token check
  that Flask performs on every `/api/*` route.

### Step 9: Start Backend
```bash
cd /opt/veyronix/backend
source .venv/bin/activate  # or use uv sync
.venv/bin/gunicorn --worker-class gthread --workers 1 --threads 4 \
  --bind 127.0.0.1:5001 --timeout 300 wsgi:app
```

`wsgi:app` validates the configuration (LLM key + Bearer token) before the
server starts. One worker is intentional: simulation run state lives in the
process and SQLite is the shared store. Use the systemd unit in
`DEPLOYMENT.md` for a production service.

### Step 10: Start Caddy
```bash
sudo systemctl enable caddy
sudo systemctl start caddy
```

Backend will be available at: `https://<oracle-ip-or-domain>:443`

---

## 9. RAM & Storage Calculations (Oracle 1 GB RAM)

### Idle State (No Simulation Running)
| Component | RAM |
|-----------|-----|
| Ubuntu OS | ~250–350 MB |
| Caddy (idle) | ~50–100 MB |
| Python + Flask + asyncio (idle) | ~100–150 MB |
| SQLite (WAL mode, idle) | ~10–30 MB |
| **Total Idle** | **~410–630 MB** |
| **Available** | **~370–590 MB** |

### 1 User, 1 Simulation (Small: 10 agents, 5 rounds)
| Component | RAM |
|-----------|-----|
| Idle setup | ~500 MB |
| Semaphore(3) concurrent LLM calls | ~150–250 MB |
| SQLite queries (streamed) | ~20–50 MB |
| **Total** | **~670 MB – 800 MB** |

### Multiple Users
| Scenario | Result |
|----------|--------|
| 1 user, small simulation | ✅ Works comfortably |
| 2+ users, simultaneous simulations | ⚠️ Semaphore(3) queues requests — no OOM crash |
| Multiple users, queued (1 at a time) | ✅ Safe with Semaphore |

**Storage (48 GB Disk):**
| Component | Space |
|-----------|-------|
| OS | ~8 GB |
| Backend + dependencies | ~2 GB |
| SQLite DB (WAL mode) | ~2–5 GB |
| Logs | ~2 GB |
| **Available for user data** | **~25–30 GB** |
| Per simulation (avg) | ~1–5 MB |
| Estimated users (1000+) | ✅ Fits easily |

---

## 10. Cost Breakdown (Monthly)

| Service | Cost | Notes |
|---------|------|-------|
| Vercel Frontend | ₹0 | Free tier |
| Oracle Cloud VM | ₹0 | Always Free tier (1 GB RAM, 48 GB disk) |
| Python 3.12 | ₹0 | Free |
| Caddy | ₹0 | Open source |
| Local SQLite Storage | ₹0 | Oracle disk |
| NVIDIA NIM API | ₹0 | Free tier (rate limited) — Nemotron 3.5 Super VL 120B A12B |
| Custom Domain | Paid | Optional, later |
| Zep Cloud | ₹0 | REMOVED |

**Total to start: ₹0**

---

## 11. API Endpoints (Backend → Frontend)

All endpoints require `Authorization: Bearer <token>` header.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/graph/ontology/generate` | POST | Upload documents + requirement → generate ontology |
| `/api/graph/build` | POST | Build local SQLite knowledge graph |
| `/api/graph/data/{graphId}` | GET | Get graph nodes/edges from SQLite |
| `/api/simulation/create` | POST | Create new simulation |
| `/api/simulation/prepare` | POST | Prepare simulation (agents, config) — async |
| `/api/simulation/{id}/start` | POST | Start asyncio simulation |
| `/api/simulation/{id}/stop` | POST | Stop simulation |
| `/api/simulation/{id}/report` | GET | Get final prediction report |
| `/api/simulation/history` | GET | List past simulations |
| `/api/report/generate` | POST | Generate report (Nemotron 3.5 Super VL 120B A12B) |
| `/api/report/chat` | POST | Chat with ReportAgent |
| `/api/simulation/{id}/market-shock` | POST | Inject market shock event |

---

## 12. Step-by-Step Execution Roadmap

### Phase 1: Local Setup (Your PC)
1. Install Python 3.12 (side-by-side with 3.13)
2. Install uv (already installed)
3. Clone Veyronix repo (already exists)
4. Create `.env` with NVIDIA API key + Bearer Token
5. Run backend locally: `uv run python run.py`
6. Run frontend locally: `npm run dev`

### Phase 2: Zep Removal & SQLite Migration
1. Delete all Zep files (`zep.py`, `zep_paging.py`, `zep_lifecycle.py`, `zep_tools.py`, `zep_entity_reader.py`, `zep_graph_memory_updater.py`)
2. Create `backend/app/services/local_memory.py` with SQLite WAL mode
3. Create `backend/app/services/async_simulation_runner.py` with asyncio + Semaphore(3)
4. Update `config.py` — remove Zep, add SQLite + LLM Router
5. Update `config.py` — set LLM_MODEL_NAME to nemotron-3.5-super-vl-120b-a12b
5. Update `requirements.txt` — remove zep-cloud
6. Test locally

### Phase 3: Advanced Features
1. Implement Memory Consolidation (every 5 rounds)
2. Implement Market Shock Injection endpoint
3. Add Domain Availability Check to frontend results page
4. Add Bearer Token auth middleware to Flask
5. Configure Caddy rate limiting

### Phase 4: Oracle Cloud Deploy
1. Launch Oracle Always Free VM
2. Install Python 3.12, uv, Caddy
3. Deploy backend, configure Caddyfile with rate limiting
4. Start backend + Caddy
5. Test API from browser

### Phase 5: Vercel Deploy
1. Build frontend: `npm run build`
2. Install Vercel CLI: `npm i -g vercel`
3. Deploy: `vercel --prod`
4. Connect Vercel frontend to Oracle backend via Caddy HTTPS

### Phase 6: Live Testing
1. Upload documentation as seed
2. Requirement: "Predict SaaS product names"
3. Run simulation, verify Memory Consolidation works at round 5
4. Test Market Shock Injection
5. Test Domain Availability Check in results
6. Fix bugs, iterate

---

## 13. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| 1 GB RAM too small | High | Semaphore(3) limits concurrent LLM calls; Memory Consolidation frees RAM every 5 rounds |
| SQLite write lock | Medium | WAL mode allows concurrent reads + 1 write |
| LLM API rate limit (NVIDIA NIM free) | Medium | Dynamic routing: Nemotron 3.5 Super VL 120B A12B for all calls; Token Bucket Rate Limiter prevents 429 errors |
| CORS issues (Vercel → Oracle) | Low | Caddy CORS headers configured |
| Bearer token exposure | Low | HTTPS only via Caddy auto SSL |
| Disk full (48 GB) | Low | SQLite DB stays small (~1-5 MB per simulation); cleanup old simulations |

---

## 14. Final Notes

- **This is a separate SaaS product** — no third-party branding, no third-party name, no third-party theme.
- **Primary LLM:** NVIDIA Nemotron 3.5 Super VL 120B A12B (via NVIDIA NIM OpenAI-compatible API)
- **Zep Cloud is REMOVED** — replaced by local SQLite (JSON1 + FTS5) with WAL mode.
- **Subprocess is REMOVED** — replaced by asyncio + Semaphore(3) to prevent OOM crashes.
- **Logging:** Structured logs written to `./logs/veyronix.log` (configurable via `LOG_FILE` env var)
- **Python 3.12 is mandatory** — system Python 3.13/3.14 will not work with Veyronix.
- **No Python 3.13+ features** — all code compatible with Python 3.12.
- **Start small:** 1 Oracle instance, Semaphore(3), few agents, few rounds. Scale only after proving the concept.

---

**Plan Implementation Status: COMPLETE**

All planned changes have been implemented:

✅ **PLAN.md** - Updated with Nemotron 3.5 Super VL 120B A12B, Chunked Simulation Execution, Logging section
✅ **config.py** - Removed Zep, added SQLite + Nemotron LLM Router config
✅ **.env / .env.example** - Created with NVIDIA NIM / Nemotron configuration
✅ **rate_limiter.py** - Created (Token Bucket + exponential backoff for 429)
✅ **local_memory.py** - Created (SQLite WAL mode + memory consolidation)
✅ **simulation_runner.py** - Rewritten with asyncio + Semaphore (no subprocess)
✅ **graph_builder.py** - Rewritten to use LocalMemory instead of Zep
✅ **simulation.py** - Removed Zep imports, uses LocalMemory
✅ **report.py** - Removed Zep imports, uses LocalMemory
✅ **graph.py** - Removed Zep imports, uses LocalMemory
✅ **__init__.py** - Updated exports
✅ **report_agent.py** - Replaced zep_tools with local tools
✅ **oasis_profile_generator.py** - Removed zep imports
✅ **simulation_manager.py** - Replaced zep_entity_reader with LocalMemory
✅ **simulation_config_generator.py** - Replaced EntityNode with Dict
✅ **Zep files deleted** - 6 files removed
✅ **zep-cloud removed** - from requirements.txt and pyproject.toml
✅ **Docker files deleted** - Dockerfile and docker-compose.yml removed
✅ **Docker references removed** - from README.md and PLAN.md
✅ **CHANGES_REPORT.md** - Created with complete changes documentation

**Next Steps:**
1. Paste your NVIDIA API key into `.env` (get it from https://build.nvidia.com/)
2. Push backend to GitHub: `git add -A && git commit -m "..." && git push -u origin master`
3. Deploy to Oracle Cloud Always Free VM per Section 8 of PLAN.md