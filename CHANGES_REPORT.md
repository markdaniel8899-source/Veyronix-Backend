# Veyronix — Final Changes Report (PLAN.md audit → deployment-ready)

**Date:** Wed Nov 11 2026
**Project:** Veyronix SaaS product
**Workspace:** `C:\Scrap\Veyronix`
**Target runtime:** Oracle Cloud Always Free VM, 1 vCPU / 1 GB RAM, Ubuntu, Python 3.12

---

## 1. Outcome

`PLAN.md` was audited against the running code, every deviation was fixed, and the
result is deployment-ready without Docker, without Zep Cloud, and without any
subprocess.

| Area | Before | After |
|---|---|---|
| Knowledge graph | Zep Cloud (external, paid, removed from deps but still wired in code) | Local SQLite graph (JSON1 + FTS5, WAL) built by **LLM entity extraction** |
| Simulation | Subprocess scripts (`run_*_simulation.py`, OASIS/camel) | In-process **asyncio** runner with token bucket, `Semaphore(3)`, 3-agent chunks + ~6 s waits |
| Memory | Zep cloud store | Local SQLite: agents, edges, interactions, summaries, market shocks, consolidation every 5 rounds |
| Auth | none / optional | **Bearer token** on every `/api/*` route (constant-time compare) + Caddy TLS/rate limiting |
| Locale | fell back to a missing `zh.json` → API errors returned raw keys | `en` default + `en` fallback, `Accept-Language` negotiation, English/Roman-Urdu-only LLM output |
| LLM | mixed providers | `nemotron-3.5-super-vl-120b-a12b` via NVIDIA NIM for both router tiers |
| Tests | 60 (several tied to Zep) | **71 passing** (local pipeline, locale, config, API) |

---

## 2. New files

| File | Purpose |
|---|---|
| `Caddyfile` | Reverse proxy to `127.0.0.1:5001`, automatic HTTPS, zstd/gzip, security headers, per-IP rate-limit zones (`rate_limit` module) |
| `DEPLOYMENT.md` | Full production runbook: server prerequisites, Caddy + rate-limit module, gunicorn/systemd unit, Vercel wiring, SQLite backup/restore, troubleshooting, security checklist |
| `backend/wsgi.py` | Production entry point — validates config (LLM key + Bearer token) before booting, exposes `app` |
| `backend/.env.example` | Complete, commented env template (no secrets) |
| `backend/app/services/entity_extractor.py` | LLM entity + relationship extraction, ontology-normalised types, de-duplication, bounded fallback |
| `backend/app/services/entity_utils.py` | Shared entity normalisation helpers (name/uuid/type/summary/attributes) |
| `backend/tests/test_local_pipeline.py` | End-to-end coverage: memory CRUD + graph scoping, edges, shocks, consolidation, extraction de-dup, fake-LLM simulation, API endpoints |
| `backend/tests/test_locale_and_config.py` | Locale fallback, no-Chinese guarantee, config validation, absolute path resolution |
| `frontend/src/components/DomainAvailability.vue` | Domain availability check (RDAP + DNS-over-HTTPS fallback) |
| `CHANGES_REPORT.md` | This report |

---

## 3. Deleted files

| File | Reason |
|---|---|
| `backend/app/utils/zep.py`, `zep_paging.py`, `zep_lifecycle.py` | Zep Cloud removed |
| `backend/app/services/zep_tools.py`, `zep_entity_reader.py`, `zep_graph_memory_updater.py` | Zep Cloud removed |
| `backend/app/services/simulation_ipc.py` | Dead IPC layer for the old subprocess protocol |
| `backend/scripts/run_twitter_simulation.py`, `run_reddit_simulation.py`, `run_parallel_simulation.py` | Subprocess simulation scripts (OASIS) replaced by the asyncio runner |
| `backend/scripts/action_logger.py` | Only used by the deleted scripts |
| `backend/scripts/validate_zep_cloud_integration.py` | Zep Cloud validation script |
| `backend/tests/test_zep_*.py` (9 files) | Tests for removed Zep code |
| `frontend/src/assets/logo/MiroFish_logo_left.jpeg` | Renamed to `Veyronix_logo_left.jpeg` (branding) |

---

## 4. Backend changes

### 4.1 Local memory (`backend/app/services/local_memory.py`) — rewritten
- Absolute DB path resolution, WAL + `synchronous=NORMAL` + `busy_timeout=5000`.
- Schema: `agents`, `interactions`, `memory_summaries`, `market_shocks`, **`graph_edges`**, plus FTS5 index with a `LIKE` fallback when FTS5/JSON1 are unavailable.
- Graph scoping for every read: `list_agents(graph_id)`, `count_agents(graph_id)`, `delete_agents_by_graph`, `list_edges`, `count_edges`, `delete_edges_by_graph`.
- Persona stored as JSON; entities carry `uuid`, `name`, `labels`, `summary`, `attributes`, `graph_id`.
- Market shock queue (`add`, `get_pending`, `consume`, `list`) and memory consolidation (summary + raw interaction cleanup).

### 4.2 Graph building
- `EntityExtractor.extract_graph()` extracts real entities/relationships with the LLM instead of storing raw document chunks as "agents"; chunks are only kept (max 200, evenly sampled) when extraction finds nothing.
- `api/graph.py`: entities/edges stored per `graph_id`; `GET /api/graph/data/<graph_id>` returns `GraphPanel.vue`-shaped nodes (`uuid`, `name`, `labels`, `summary`, `attributes`) and edges (`source_node_uuid`, `target_node_uuid`, `name`, `fact`).
- Graph deletion now removes local agents **and** edges after the active-consumer check.

### 4.3 Simulation
- `simulation_runner.py`: background daemon thread + asyncio loop, token-bucket rate limiter (`LLM_RPM=10`), `Semaphore(LLM_MAX_CONCURRENT=3)`, 3 agents per chunk with `SIMULATION_CHUNK_WAIT_SECONDS=6` waits, `actions.jsonl` action log, market-shock injection, consolidation every 5 rounds, interviews saved to `interviews.json`.
- Removed the unused `time` import and the dead `CHINA_TIMEZONE_CONFIG` lookup.
- `simulation_manager.py`, `oasis_profile_generator.py`, `simulation_config_generator.py` now use local dict entities (no Zep `EntityNode`).
- New market-shock endpoints:
  - `POST /api/simulation/<id>/market-shock` (event + optional `round_num`)
  - `GET /api/simulation/<id>/market-shocks`
- Simulation entity endpoints are scoped by `graph_id`.
- `get_run_instructions()` no longer returns subprocess commands — the runner is in-process.

### 4.4 Report agent
- `ZepToolsService` → `LocalToolsService` (backed by `LocalMemory`); all report tools (insight forge, panorama, quick search, interviews, graph stats) read the local graph.
- All prompt templates, tool descriptions and tool outputs rewritten in English; report/section fallbacks are English.

### 4.5 API, auth and config
- `app/__init__.py`: Bearer authentication (`hmac.compare_digest`) on `/api/*`, `503` when the token is not configured, CORS restricted to `CORS_ORIGINS`, security headers, 413/500 handlers, simulation cleanup hook.
- `config.py`: env loading from `backend/.env` → root `.env` → process env; absolute paths for DB/logs/uploads; `LLM_RPM`, `LLM_MAX_CONCURRENT`, `SIMULATION_CHUNK_WAIT_SECONDS`, `MEMORY_CONSOLIDATION_INTERVAL`, `CORS_ORIGINS`, `REPORT_AGENT_*`; `Config.validate()`.
- `utils/logger.py`: rotating file handler + console logging, absolute log path.
- `run.py`: validated startup, `FLASK_HOST`/`FLASK_PORT`, UTF-8 safe stdout.

### 4.6 Language and locale (critical bug fix)
- `utils/locale.py` defaulted to `zh`, but `zh.json` no longer exists → **every `t()` call returned the raw key** (e.g. `api.simulationNotFound`). Now: `en` default, `en` fallback, `Accept-Language` negotiation with base-language matching, `set_locale()` validation.
- `locales/languages.json` and `frontend/locales/languages.json` were mojibake-corrupted (`EspaA�ol`, `FranAis`, …) and listed locales with no translation files. Both rewritten as clean UTF-8, English only, with the rule “respond in English or Roman Urdu only”.
- All user-facing strings, API errors, prompts, log messages and tool outputs translated to English; prompts explicitly forbid Chinese/Japanese/Korean/Arabic/Cyrillic output.
- Profile/config/report prompts previously asked for Chinese content (e.g. “country: use Chinese”) — now English.

### 4.7 Dependencies
- Removed `camel-oasis==0.2.5` and `camel-ai==0.2.78` (nothing imports them any more; they were only needed by the deleted subprocess scripts and would have bloated a 1 GB VM install).
- Removed `zep-cloud` (earlier phase).
- Added `gunicorn>=21.2.0` (Linux only) for production serving.
- `requirements.txt` rewritten with clean English comments (the old file had mojibake headers).

---

## 5. Frontend changes

| Change | Detail |
|---|---|
| Branding | `Veyronix_logo_left.jpeg` replaces `MiroFish_logo_left.jpeg` |
| i18n path | `src/i18n/index.js` loads `../../locales/*.json` correctly with `en` fallback |
| Locales | `frontend/locales/en.json` (+ `domainCheck` keys), `languages.json` cleaned |
| Domain Availability Check | New `DomainAvailability.vue`: extracts candidate product names from the report (labelled names, bold names, title), checks `<name>.com/.io/.ai` via public RDAP (`https://rdap.org/domain/<domain>`) with a Cloudflare DNS-over-HTTPS fallback, renders available/taken/unverified states — no API key needed |
| Integration | Mounted in `Step4Report.vue` below the report header, fed by the generated section content |
| Build | `npm run build` succeeds (692 modules) |

---

## 6. Deployment assets

**Caddy** (`Caddyfile`) — TLS, compression, security headers, and two per-IP limit
zones: reads `180 req/min`, writes `60 req/min`. CORS is intentionally *not*
set in Caddy (Flask owns it) to avoid duplicate `Access-Control-*` headers.

**gunicorn** — `--worker-class gthread --workers 1 --threads 4 --bind 127.0.0.1:5001 --timeout 300 wsgi:app`.
One worker is required because simulation run state is in-process memory.

**systemd** unit, SQLite backup/restore and troubleshooting are in `DEPLOYMENT.md`.

### Environment checklist (production)
```
LLM_API_KEY=<NVIDIA NIM key>            LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL_NAME=nemotron-3.5-super-vl-120b-a12b
LLM_ROUTER_SMALL=<same>                 LLM_ROUTER_LARGE=<same>
LLM_RPM=10                              LLM_MAX_CONCURRENT=3
SIMULATION_CHUNK_WAIT_SECONDS=6         MEMORY_CONSOLIDATION_INTERVAL=5
API_BEARER_TOKEN=<shared secret>        CORS_ORIGINS=https://<frontend-domain>
SECRET_KEY=<random>                     FLASK_DEBUG=False
DATABASE_PATH=./uploads/veyronix.db     LOG_FILE=./logs/veyronix.log
```
Frontend (Vercel): `VITE_API_BASE_URL=https://<api-domain>`, `VITE_API_BEARER_TOKEN=<same token>`.

---

## 7. Verification

| Check | Result |
|---|---|
| `python -m pytest tests -q` (backend) | **71 passed** |
| `npm run build` (frontend) | ✅ built (692 modules) |
| `git check-ignore backend/.env frontend/.env` | ignored in both repos — no secrets committed |
| Locale regression test | `t()` returns English text, never raw keys |
| End-to-end simulation test | fake LLM, 2 rounds, market shock consumed, interviews stored, actions/timeline/stats available |

---

## 8. Git

| Repository | Remote | Branch | Commit |
|---|---|---|---|
| Backend/root mirror | `https://github.com/markdaniel8899-source/Veyronix-Backend.git` | `master` → `main` | `6b53da2` |
| Frontend | `https://github.com/markdaniel8899-source/Veyronix.git` | `main` | `10febc9` |

Both pushes verified against the remote (`git ls-remote`).

---

## 9. Remaining risks / next steps

1. **Live LLM end-to-end run** — the pipeline is verified with a fake LLM; one real NVIDIA NIM run (graph build → simulation → report) is still worth doing to confirm quota behaviour at `LLM_RPM=10`.
2. **Rotating secrets** — `backend/.env` currently holds real values (NVIDIA key, secret key, bearer token). Rotate them before production and keep `.env` off the machine that holds the deploy.
3. **Caddy rate-limit module** — the apt package has no third-party modules; run `caddy add-package github.com/mholt/caddy-ratelimit` on the VM (documented in `DEPLOYMENT.md`) or temporarily remove the `rate_limit` block.
4. **1 GB RAM** — keep one gunicorn worker, `LLM_MAX_CONCURRENT=3`; if OOM occurs, lower concurrency and `MemoryMax` in the systemd unit.
5. **Roman Urdu** — the model is instructed to answer in English or Roman Urdu; only English is currently shipped as a UI locale.
