# Veyronix SaaS Product — Complete Project Report (A to Z)

> **Generated:** Wed Nov 11 2026  
> **Project Location:** `C:\Scrap\Veyronix`  
> **Product Name:** Veyronix (renamed from MiroFish)  
> **Status:** Code compiles successfully — Ready for PLAN.md implementation

---

## 1. Executive Summary

**Project Name:** Veyronix (previously MiroFish)  
**Type:** Multi-Agent Swarm Intelligence Prediction SaaS Engine  
**Core Purpose:** User uploads a seed document (PDF/MD/TXT), describes a prediction requirement in natural language, and thousands of AI agents simulate a parallel digital world to generate a prediction report. Users can then chat with agents or the ReportAgent in the simulated world.  
**Current Status:** Renamed from MiroFish → Veyronix, Chinese content removed, all Python files compile successfully, ready for backend/frontend changes per PLAN.md.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          USER BROWSER                           │
│                    (Vue 3 + Vite + Tailwind)                     │
└─────────────────────────────┬───────────────────────────────────┘
                              │ HTTPS/HTTP API Calls (Axios)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FLASK BACKEND (Python 3.12 + uv)           │
│                    (Port 5001, Caddy Reverse Proxy)             │
└─────────────────────────────┬───────────────────────────────────┘
                              │ LLM API Calls (OpenAI-compatible)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    GROQ API OR NVIDIA NIM (Free Tier)           │
└─────────────────────────────────────────────────────────────────┘
```

**Storage:** Local SQLite/JSON files on disk (Zep Cloud is being skipped per PLAN.md, though current code still imports Zep).

---

## 3. Directory Structure

```
C:\Scrap\Veyronix\
├── PLAN.md                          # Deployment & implementation plan
├── README.md                        # Project documentation (English)
├── CURRENT_PROJECT_REPORT.md        # This file
├── .env.example                     # Environment variables template
├── Dockerfile                       # Docker deployment config
├── docker-compose.yml               # Docker compose
├── package.json                     # Root npm config
├── backend\                         # Flask Python backend
│   ├── run.py                       # Backend entry point
│   ├── requirements.txt             # Python dependencies
│   ├── pyproject.toml               # uv project config
│   ├── app\
│   │   ├── __init__.py              # Flask app factory (create_app)
│   │   ├── config.py                # Configuration (SECRET_KEY, LLM, Zep, paths)
│   │   ├── api\
│   │   │   ├── __init__.py          # Blueprint registration
│   │   │   ├── graph.py             # Graph/Ontology API routes
│   │   │   ├── simulation.py        # Simulation management API routes
│   │   │   └── report.py            # Report generation & chat API routes
│   │   ├── models\
│   │   │   ├── __init__.py
│   │   │   ├── project.py           # Project data model & persistence
│   │   │   └── task.py              # Task model (async job tracking)
│   │   ├── services\
│   │   │   ├── __init__.py
│   │   │   ├── graph_builder.py     # Zep knowledge graph construction
│   │   │   ├── oasis_profile_generator.py  # Agent persona generation
│   │   │   ├── simulation_config_generator.py  # Sim config (time, events, agents)
│   │   │   ├── simulation_manager.py # Simulation lifecycle manager
│   │   │   ├── simulation_runner.py  # Background simulation execution
│   │   │   ├── simulation_ipc.py     # Inter-process communication
│   │   │   ├── text_processor.py     # Document text extraction & chunking
│   │   │   ├── report_agent.py       # ReportAgent (ReACT mode report gen)
│   │   │   ├── zep_entity_reader.py  # Zep entity extraction
│   │   │   ├── zep_graph_memory_updater.py  # Zep graph memory updates
│   │   │   ├── zep_tools.py          # Zep retrieval tools (InsightForge, Panorama, QuickSearch)
│   │   │   └── ontology_generator.py # Ontology generation from documents
│   │   ├── utils\
│   │   │   ├── __init__.py
│   │   │   ├── llm_client.py         # LLM client (OpenAI-compatible)
│   │   │   ├── openai_chat_compat.py # OpenAI SDK compatibility layer
│   │   │   ├── zep.py                # Zep Cloud client wrapper
│   │   │   ├── zep_paging.py         # Zep paginated fetch
│   │   │   ├── zep_lifecycle.py      # Zep graph lifecycle locking
│   │   │   ├── retry.py              # Retry with backoff
│   │   │   ├── logger.py             # Logging setup
│   │   │   ├── locale.py             # i18n translation helper
│   │   │   ├── ontology.py           # Ontology utilities
│   │   │   └── file_parser.py        # MD/TXT/PDF parsing
│   │   └── utils\logger.py
│   ├── scripts\                      # Simulation runner scripts
│   │   ├── run_twitter_simulation.py
│   │   ├── run_reddit_simulation.py
│   │   ├── run_parallel_simulation.py
│   │   ├── action_logger.py
│   │   └── validate_zep_cloud_integration.py
│   └── tests\                        # Backend test suite
├── frontend\                         # Vue 3 + Vite frontend
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   ├── public\
│   │   └── icon.png
│   ├── src\
│   │   ├── main.js                   # Vue app entry
│   │   ├── App.vue                   # Root component
│   │   ├── i18n\index.js             # English translations
│   │   ├── router\index.js           # Vue Router (6 routes)
│   │   ├── api\
│   │   │   ├── index.js              # Axios instance (baseURL: localhost:5001)
│   │   │   ├── graph.js              # Graph API calls
│   │   │   ├── report.js             # Report API calls
│   │   │   └── simulation.js         # Simulation API calls
│   │   ├── assets\logo\              # Veyronix logo images
│   │   ├── components\
│   │   │   ├── GraphPanel.vue        # Knowledge graph SVG visualization
│   │   │   ├── HistoryDatabase.vue   # Simulation history
│   │   │   ├── LanguageSwitcher.vue  # EN/ZH toggle
│   │   │   ├── Step1GraphBuild.vue   # Step 1 component
│   │   │   ├── Step2EnvSetup.vue     # Step 2 component
│   │   │   ├── Step3Simulation.vue   # Step 3 component
│   │   │   ├── Step4Report.vue       # Step 4 component
│   │   │   └── Step5Interaction.vue  # Step 5 component
│   │   ├── views\
│   │   │   ├── Home.vue              # Landing / dashboard
│   │   │   ├── MainView.vue          # Process wrapper (Step 1)
│   │   │   ├── Process.vue           # Step 1: Graph Build view
│   │   │   ├── SimulationView.vue    # Step 2: Env Setup view
│   │   │   ├── SimulationRunView.vue # Step 3: Run Simulation view
│   │   │   ├── ReportView.vue        # Step 4: Report Generation view
│   │   │   └── InteractionView.vue   # Step 5: Deep Interaction view
│   │   └── store\pendingUpload.js    # Pinia-like state for file uploads
├── locales\
│   ├── en.json                       # English UI strings (667 lines)
│   └── languages.json                # Language config
├── static\                           # Images, logos, screenshots
├── scripts\                          # Star history scripts
├── tests\                            # Root-level tests
└── .github\                          # CI/CD workflows
```

---

## 4. Frontend — Vue 3 Application

### Tech Stack
- **Vue 3** (Composition API, `<script setup>`)
- **Vite** (build tool, dev server)
- **Tailwind CSS** (styling)
- **Vue Router** (file-based navigation via `router/index.js`)
- **Axios** (HTTP client, baseURL: `http://localhost:5001`)
- **Vue I18n** (internationalization, English locale in `locales/en.json`)

### Routes (`frontend\src\router\index.js`)
| Route | Component | Purpose |
|-------|-----------|---------|
| `/` | `Home.vue` | Landing page — file upload, simulation requirement input, start engine |
| `/process/:projectId` | `MainView.vue` → `Process.vue` | Step 1: Graph Build (knowledge graph visualization) |
| `/simulation/:simulationId` | `SimulationView.vue` | Step 2: Environment Setup (agent personas, platform config) |
| `/simulation/:simulationId/start` | `SimulationRunView.vue` | Step 3: Run Simulation (live agent actions, timeline) |
| `/report/:reportId` | `ReportView.vue` | Step 4: Report Generation (sections, key facts, entities) |
| `/interaction/:reportId` | `InteractionView.vue` | Step 5: Deep Interaction (chat with agents, surveys, interviews) |

### Key Views
- **Home.vue:** Upload seed documents (PDF/MD/TXT), enter simulation requirement text, click "Start Engine" to begin Step 1.
- **Process.vue:** Real-time knowledge graph SVG visualization with node/edge details panel.
- **SimulationView.vue:** Dual-platform (Twitter/Reddit) environment setup — agent count, rounds, platform config, narrative direction.
- **SimulationRunView.vue:** Live simulation execution with agent posts, graph memory updates, real-time status.
- **ReportView.vue:** Report generation progress, section-by-section output, key facts, core entities, relation chains.
- **InteractionView.vue:** Chat with ReportAgent or individual simulated agents, send surveys, conduct virtual interviews.

### GraphPanel.vue ("Omniverse" / Knowledge Graph)
- Renders nodes and edges as SVG
- Supports node selection, detail panel (name, UUID, attributes, properties)
- Zoom/pan, refresh, maximize/restore
- Real-time memory update indicator during simulation
- Displays node count, edge count statistics

---

## 5. Backend — Flask Application

### Tech Stack
- **Python 3.12** (mandatory — system Python 3.13/3.14 is NOT compatible)
- **Flask** (web framework)
- **uv** (Python package manager, fast installs)
- **python-dotenv** (environment variables)
- **openai** SDK (LLM client, OpenAI-compatible API)
- **zep-cloud** SDK (currently imported, being skipped per PLAN.md)
- **Flask-CORS** (cross-origin requests)

### Config (`backend\app\config.py`)
- `SECRET_KEY`, `DEBUG`
- `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL_NAME` (Groq/NVIDIA compatible)
- `ZEP_API_KEY` (currently required by code, being removed)
- `MAX_CONTENT_LENGTH` (50MB file upload limit)
- `UPLOAD_FOLDER`, `ALLOWED_EXTENSIONS` (pdf, md, txt, markdown)
- `DEFAULT_CHUNK_SIZE` (500), `DEFAULT_CHUNK_OVERLAP` (50)
- `OASIS_DEFAULT_MAX_ROUNDS` (10), `OASIS_SIMULATION_DATA_DIR`
- Twitter/Reddit action lists
- Report agent settings

### Models
- **Project** (`project.py`): project_id, name, status, files, ontology, graph_id, simulation_requirement, etc.
  - Statuses: CREATED → ONTOLOGY_GENERATED → GRAPH_BUILDING → GRAPH_COMPLETED → FAILED
- **Task** (`task.py`): task_id, task_type, status, progress, message, result, error

### Services (Core Logic)

| Service | File | Purpose |
|---------|------|---------|
| Graph Builder | `graph_builder.py` | Builds Zep knowledge graph from uploaded documents |
| Oasis Profile Generator | `oasis_profile_generator.py` | Converts Zep entities into OASIS agent personas (bio, MBTI, age, gender, etc.) |
| Simulation Config Generator | `simulation_config_generator.py` | LLM generates time config, event config, agent config |
| Simulation Manager | `simulation_manager.py` | Manages simulation lifecycle (created→preparing→ready→running→completed) |
| Simulation Runner | `simulation_runner.py` | Executes simulation in background subprocess, tracks agent actions |
| Report Agent | `report_agent.py` | ReACT-mode report generation with planning, section writing, tool use |
| Zep Entity Reader | `zep_entity_reader.py` | Reads/filter entities from Zep graph |
| Zep Graph Memory Updater | `zep_graph_memory_updater.py` | Updates graph memory during simulation |
| Zep Tools | `zep_tools.py` | Retrieval tools: InsightForge, PanoramaSearch, QuickSearch, InterviewSubAgent |
| Ontology Generator | `ontology_generator.py` | Generates ontology structure from document content |
| Text Processor | `text_processor.py` | Extracts and chunks text from uploaded documents |
| File Parser | `file_parser.py` | Parses PDF/MD/TXT files |

### Utils
- **llm_client.py:** OpenAI-compatible LLM client with response format fallback
- **zep.py:** Zep Cloud client wrapper with retry logic
- **zep_paging.py:** Paginated Zep node/edge fetching
- **zep_lifecycle.py:** Graph lifecycle locking for concurrent access
- **retry.py:** Exponential backoff retry
- **logger.py:** Logging setup
- **locale.py:** i18n translation helper
- **ontology.py:** Ontology attribute normalization
- **openai_chat_compat.py:** OpenAI SDK compatibility layer
- **file_parser.py:** Document parsing (PDF/MD/TXT)

---

## 6. 5-Phase Simulation Workflow

### Phase 1: Graph Building
1. User uploads seed document(s) via Home.vue
2. Frontend calls `/api/graph/ontology/generate` → LLM analyzes document, generates ontology
3. Frontend calls `/api/graph/build` → GraphBuilder chunks text, sends to Zep, builds knowledge graph
4. Result: Graph with nodes (entities) and edges (relationships), stored with `graph_id`

### Phase 2: Environment Setup
1. User configures simulation (agent count, platforms, rounds, etc.)
2. Backend reads entities from Zep graph
3. OasisProfileGenerator creates agent personas (bio, MBTI, age, gender, country, profession, topics)
4. SimulationConfigGenerator uses LLM to create:
   - `time_config` (simulation duration, rounds, peak/off-peak hours)
   - `event_config` (initial posts, hot topics)
   - `agent_configs` (per-agent behavior: activity level, sentiment bias, influence weight)
   - `platform_configs` (Twitter/Reddit recommendation algorithms)

### Phase 3: Simulation
1. SimulationRunner spawns background subprocess (`run_twitter_simulation.py`, `run_reddit_simulation.py`)
2. Agents post, like, repost, follow, comment on dual platforms (Twitter + Reddit)
3. Dynamic graph memory updates continuously (ZepGraphMemoryManager)
4. Real-time status: agent actions, posts, timeline
5. Can be paused, stopped, or force-stopped

### Phase 4: Report Generation
1. ReportAgent (ReACT mode) plans report outline based on simulation requirement
2. Generates sections sequentially: Key Facts, Core Entities, Relation Chains, Sub-queries, etc.
3. Uses Zep tools (InsightForge, PanoramaSearch, QuickSearch) for deep retrieval
4. Saves report sections as markdown files
5. Output: Full report with executive summary, findings, predictions

### Phase 5: Deep Interaction
1. User chats with ReportAgent (has access to 4 tools: InsightForge, PanoramaSearch, QuickSearch, InterviewSubAgent)
2. User chats with individual simulated agents in the world
3. User can send surveys to multiple agents simultaneously
4. User can conduct virtual interviews (batch API calls to agents)

---

## 7. Complete API Inventory

### Graph API (`/api/graph/`)
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/graph/ontology/generate` | POST | Upload documents + requirement → generate ontology |
| `/api/graph/build` | POST | Build knowledge graph from ontology |
| `/api/graph/task/{taskId}` | GET | Check graph build task status |
| `/api/graph/data/{graphId}` | GET | Get graph nodes/edges for frontend visualization |
| `/api/graph/project/{projectId}` | GET | Get project details |

### Simulation API (`/api/simulation/`)
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/simulation/create` | POST | Create new simulation (needs project_id, graph_id) |
| `/api/simulation/prepare` | POST | Prepare simulation (agents, config) — async task |
| `/api/simulation/prepare/status` | POST | Check preparation progress |
| `/api/simulation/{id}` | GET | Get simulation status |
| `/api/simulation/{id}/run` | POST | Start simulation run |
| `/api/simulation/{id}/report` | GET | Get final report |
| `/api/simulation/history` | GET | List past simulations |
| `/api/simulation/{id}/profiles` | GET | Get agent profiles |
| `/api/simulation/{id}/profiles/realtime` | GET | Real-time profile generation status |
| `/api/simulation/{id}/config` | GET | Get simulation configuration |
| `/api/simulation/{id}/config/realtime` | GET | Real-time config generation status |
| `/api/simulation/{id}/start` | POST | Start simulation execution |
| `/api/simulation/{id}/stop` | POST | Stop simulation |
| `/api/simulation/{id}/run-status` | GET | Get run status |
| `/api/simulation/{id}/posts` | GET | Get simulation posts |
| `/api/simulation/{id}/timeline` | GET | Get simulation timeline by rounds |
| `/api/simulation/{id}/agent-stats` | GET | Get agent statistics |
| `/api/simulation/{id}/actions` | GET | Get action history |
| `/api/simulation/{id}/close-env` | POST | Close simulation environment |
| `/api/simulation/{id}/env-status` | POST | Get environment status |
| `/api/simulation/interview/batch` | POST | Batch interview agents |
| `/api/script/{script_name}/download` | GET | Download simulation scripts |

### Report API (`/api/report/`)
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/report/generate` | POST | Generate report (async task) |
| `/api/report/generate/status` | POST | Check report generation progress |
| `/api/report/{report_id}` | GET | Get report details |
| `/api/report/by-simulation/{simulation_id}` | GET | Get report by simulation ID |
| `/api/report/list` | GET | List all reports |
| `/api/report/{report_id}/download` | GET | Download report (Markdown) |
| `/api/report/{report_id}` | DELETE | Delete report |
| `/api/report/chat` | POST | Chat with ReportAgent |
| `/api/report/{report_id}/progress` | GET | Get report generation progress |
| `/api/report/{report_id}/sections` | GET | Get report sections |
| `/api/report/{report_id}/section/{index}` | GET | Get single section content |
| `/api/report/check/{simulation_id}` | GET | Check if report exists & interview unlocked |
| `/api/report/{report_id}/agent-log` | GET | Get agent execution log |
| `/api/report/{report_id}/agent-log/stream` | GET | Get full agent log |
| `/api/report/{report_id}/console-log` | GET | Get console log |
| `/api/report/{report_id}/console-log/stream` | GET | Get full console log |
| `/api/report/tools/search` | POST | Graph search tool (debug) |
| `/api/report/tools/statistics` | POST | Graph statistics tool (debug) |

---

## 8. Data Flow — How Code Communicates (A to Z)

### Complete End-to-End Flow:

**Step 1: Document Upload → Ontology → Graph**
```
User uploads PDF/MD/TXT on Home.vue
    ↓ (Axios POST /api/graph/ontology/generate)
Frontend → backend/app/api/graph.py → ontology_generator.py
    ↓ (LLM analyzes document)
Ontology saved to Project model
    ↓ (Axios POST /api/graph/build)
GraphBuilder chunks text → sends to Zep Cloud → builds graph
    ↓
Graph stored with graph_id, node_count, edge_count
    ↓ (Axios GET /api/graph/data/{graphId})
GraphPanel.vue renders SVG nodes & edges
```

**Step 2: Environment Setup**
```
User configures simulation parameters
    ↓ (Axios POST /api/simulation/create)
SimulationManager creates SimulationState
    ↓ (Axios POST /api/simulation/prepare)
SimulationManager.prepare_simulation():
  → ZepEntityReader reads entities from graph
  → OasisProfileGenerator creates agent personas (LLM)
  → SimulationConfigGenerator creates time/event/agent config (LLM)
    ↓
State saved: PREPARING → READY
```

**Step 3: Run Simulation**
```
User clicks Start
    ↓ (Axios POST /api/simulation/{id}/start)
SimulationRunner spawns subprocess (run_twitter_simulation.py / run_reddit_simulation.py)
    ↓
Agents post/comment/like on dual platforms
    ↓
ZepGraphMemoryUpdater updates graph memory in real-time
    ↓
Frontend polls /api/simulation/{id}/run-status for live updates
    ↓
Simulation reaches terminal state: COMPLETED or STOPPED
```

**Step 4: Report Generation**
```
User clicks Generate Report
    ↓ (Axios POST /api/report/generate)
ReportAgent initialized with graph_id, simulation_id, requirement
    ↓
ReportAgent plans outline (LLM)
    ↓
For each section: ReACT loop — LLM thinks → calls Zep tools (InsightForge/Panorama/QuickSearch) → writes section
    ↓
Sections saved as markdown files
    ↓
Report status: COMPLETED
    ↓ (Axios GET /api/report/{report_id})
ReportView.vue displays sections
```

**Step 5: Deep Interaction**
```
User chats with ReportAgent or individual agent
    ↓ (Axios POST /api/report/chat or /api/simulation/interview/batch)
ReportAgent or agent responds using Zep retrieval tools
    ↓
User can send surveys to multiple agents at once
```

---

## 9. Current Requirements (What is Needed Right Now)

### Backend Requirements
1. **Python 3.12** (must be installed separately — system Python 3.13/3.14 won't work)
2. **uv** (Python package manager — already installed)
3. **Flask** + python-dotenv + Flask-CORS
4. **openai** SDK (for Groq/NVIDIA NIM API)
5. **zep-cloud** SDK (currently imported — to be removed per PLAN.md)
6. **LLM API Key** (Groq or NVIDIA NIM — OpenAI-compatible)
7. **Oracle Cloud Always Free VM** (1 GB RAM, 48 GB disk) for production
8. **Caddy** (reverse proxy, auto SSL) for production

### Frontend Requirements
1. **Node.js 18+** + npm
2. **Vue 3** + Vite + Tailwind CSS
3. **Vercel** account for frontend hosting (free tier)

### Environment Variables (`.env`)
```
SECRET_KEY=your-secret-key
FLASK_DEBUG=False
LLM_API_KEY=your_groq_or_nvidia_key
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL_NAME=llama-3.1-8b-instant
ZEP_API_KEY=your_zep_key  # to be removed
STORAGE_TYPE=local
STORAGE_PATH=./uploads/simulations
OASIS_DEFAULT_MAX_ROUNDS=5
```

---

## 10. What the Project Can Do (Features)

| Feature | Description |
|---------|-------------|
| **Document Upload** | Upload PDF, MD, TXT files as seed material |
| **Knowledge Graph** | Auto-extract entities & relationships, visualize as interactive SVG graph |
| **Agent Personas** | LLM generates detailed agent profiles (bio, MBTI, age, gender, profession, topics) |
| **Dual-Platform Simulation** | Twitter-style + Reddit-style parallel agent interaction |
| **Real-time Monitoring** | Watch agent posts, actions, timeline as simulation runs |
| **Report Generation** | Multi-section prediction report with Key Facts, Entities, Relation Chains |
| **ReportAgent Chat** | Converse with AI agent that has access to 4 retrieval tools |
| **Agent Chat** | Talk to individual simulated agents in the world |
| **Surveys** | Send questions to multiple agents simultaneously |
| **Virtual Interviews** | Conduct structured interviews with selected agents |
| **History** | View past simulations and reports |
| **i18n** | English/Chinese language toggle (Chinese locale removed) |

---

## 11. Current Scenario / Status (After Your Changes)

### ✅ Completed Changes
1. **MiroFish folder deleted** — All files moved to `Veyronix/` root
2. **Renamed MiroFish → Veyronix** — All code, comments, file paths updated
3. **Chinese content removed** — `README-ZH.md` and `locales/zh.json` deleted; Chinese removed from all non-code text files
4. **ATLAS references removed** — PLAN.md no longer mentions ATLAS
5. **PLAN.md rewritten** — Clean English deployment plan
6. **All Python files compile** — 0 syntax errors
7. **Cache cleaned** — All `__pycache__` and `.pyc` files removed

### ⚠️ Remaining Code Issues
- **Backend code still imports Zep** — `zep.py`, `zep_entity_reader.py`, `zep_graph_memory_updater.py`, `zep_tools.py`, `zep_lifecycle.py`, `zep_paging.py` still exist and are imported by `simulation.py`, `report.py`, `graph_builder.py`, etc.
- **Chinese characters remain in code comments/docstrings** — These were left intentionally because removing them from within Python strings/docstrings broke syntax. The code compiles and runs, but comments contain some Chinese text.
- **No code logic changed** — As requested, no functional code changes were made, only renames and text cleanup.

### 🚧 What Needs to Happen for PLAN.md Implementation
1. **Backend:** Replace Zep services with local SQLite/JSON storage (`local_memory.py`, `storage_manager.py`)
2. **Backend:** Update `config.py` — remove Zep validation, add SQLite path config
3. **Frontend:** Change API baseURL from `localhost:5001` to Oracle Cloud HTTPS URL
4. **Frontend:** Add CORS configuration on Oracle backend (Caddy) for Vercel domain
5. **Frontend:** Add 3 new router routes: `/predict`, `/results`, `/history`
6. **Frontend:** Create new view components for prediction form and results

---

## 12. Risks & Limitations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Zep Cloud still in code | Backend won't work without Zep API key | Replace Zep services with local storage per PLAN.md |
| 1 GB RAM Oracle VM | OOM with multiple simulations | Queue system (1 simulation at a time) |
| LLM API rate limits | Groq free tier is rate-limited | Start with fewer agents/rounds |
| Frontend URL hardcoded | `localhost:5001` won't work in production | Use `VITE_API_BASE_URL` env var on Vercel |
| CORS | Browser blocks cross-origin requests | Configure Caddy CORS headers for Vercel domain |

---

## 13. What the Project Can Predict

**Veyronix is NOT limited to SaaS product names.** It is a **universal prediction engine**.

| Input | Output/Prediction |
|-------|------------------|
| Seed document (PDF/MD/TXT) + requirement | Agents simulate and future is rehearsed |
| SaaS idea document | Predicts whether it will be successful |
| News/policy document | Future trends and outcomes |
| Novel/story | Lost endings or plot predictions |
| Any natural language requirement | Agents simulate based on that requirement |

**Frontend meta title:** `Veyronix - Predict Everything`  
**Hero tagline:** `Concise & Universal Swarm Intelligence Engine`  
**Hero title:** `Upload Reports, Predict the Future`

**SaaS product name prediction is just ONE example use case.** The system can predict anything based on user-uploaded seed documents and natural language requirements.

---

## Summary

**Veyronix is a complete multi-agent prediction SaaS engine** with 5 phases (Graph Build → Env Setup → Simulation → Report → Interaction). It uses Vue 3 + Flask + LLM (Groq/NVIDIA) + Zep knowledge graphs. The code has been renamed to Veyronix, cleaned of Chinese/ATLAS content, and all Python files compile. To make it production-ready per PLAN.md, Zep needs to be replaced with local storage, frontend API URL needs to point to Oracle Cloud, and 3 new frontend routes need to be added.