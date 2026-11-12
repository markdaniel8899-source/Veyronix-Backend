# Veyronix Deployment Guide

Production target: **Oracle Cloud Always Free VM (1 GB RAM)**, Python 3.12,
Flask/gunicorn, local SQLite, and Caddy with automatic HTTPS. No Docker and no
external graph database are used.

## 1. Architecture

```
Browser / Vercel frontend
        │  HTTPS + Authorization: Bearer <token>
        ▼
Caddy (TLS, compression, per-IP rate limit)
        │  http://127.0.0.1:5001
        ▼
gunicorn (1 worker, 4 threads) → Flask API
        │
        ├── local SQLite (WAL): backend/uploads/veyronix.db
        ├── asyncio simulation runner (background daemon threads)
        └── NVIDIA NIM: nemotron-3.5-super-vl-120b-a12b
```

Key properties:

- The LLM token bucket enforces `LLM_RPM=10` and `LLM_MAX_CONCURRENT=3`.
- Simulation rounds process agents in chunks of 3 with a ~6 second wait.
- Memory is consolidated every 5 rounds; raw interactions are deleted after
  their summary is stored.
- All API routes require a Bearer token. CORS is restricted by
  `CORS_ORIGINS`.
- SQLite runs in WAL mode with a 5 second busy timeout.
- No subprocess is used for simulations; the runner lives in the API process.

## 2. Server prerequisites

Ubuntu 22.04 or 24.04, 1 vCPU, 1 GB RAM, 48 GB disk.

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev \
  build-essential curl git sqlite3
```

Install `uv` (optional, recommended):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
```

Install Caddy with the rate-limit module:

```bash
sudo apt install -y debian-keyring debian-archive-keyring \
  apt-transport-https curl

curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy

# Caddy's apt package has no third-party modules. Add the rate limiter:
caddy add-package github.com/mholt/caddy-ratelimit
sudo systemctl restart caddy
```

Verify the module is present:

```bash
caddy list-modules | grep rate_limit
```

## 3. Backend setup

```bash
sudo useradd --system --create-home --shell /bin/bash veyronix
sudo mkdir -p /opt/veyronix
sudo chown veyronix:veyronix /opt/veyronix

sudo -u veyronix git clone \
  https://github.com/markdaniel8899-source/Veyronix-Backend.git \
  /opt/veyronix
cd /opt/veyronix/backend

sudo -u veyronix python3.12 -m venv .venv
sudo -u veyronix .venv/bin/pip install --upgrade pip
sudo -u veyronix .venv/bin/pip install -r requirements.txt
```

If you use `uv` instead:

```bash
sudo -u veyronix uv sync --python 3.12
```

Create the environment file:

```bash
sudo -u veyronix cp .env.example .env
sudo -u veyronix nano .env
```

Required values:

| Variable | Notes |
|---|---|
| `LLM_API_KEY` | NVIDIA NIM key from https://build.nvidia.com/ |
| `API_BEARER_TOKEN` | Long random token; the frontend must use the same value |
| `SECRET_KEY` | Long random Flask secret |
| `CORS_ORIGINS` | Exact frontend origin(s), comma-separated |
| `DATABASE_PATH` | Default `./uploads/veyronix.db` (resolved to `backend/uploads/...`) |
| `LOG_FILE` | Default `./logs/veyronix.log` |

Generate strong secrets:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Never commit `backend/.env`. It is ignored by git in both repositories.

## 4. Run the backend with gunicorn

One worker is intentional: simulation run state is held in process memory and
SQLite is the shared store. Threads handle concurrent HTTP requests; the
asyncio runner handles LLM concurrency.

```bash
cd /opt/veyronix/backend
sudo -u veyronix .venv/bin/gunicorn \
  --worker-class gthread \
  --workers 1 \
  --threads 4 \
  --bind 127.0.0.1:5001 \
  --timeout 300 \
  --access-logfile - \
  --error-logfile - \
  wsgi:app
```

`wsgi.py` validates required configuration and refuses to boot with a missing
LLM key or Bearer token.

### systemd unit

Create `/etc/systemd/system/veyronix.service`:

```ini
[Unit]
Description=Veyronix Backend
After=network.target

[Service]
Type=simple
User=veyronix
Group=veyronix
WorkingDirectory=/opt/veyronix/backend
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/veyronix/backend/.venv/bin/gunicorn \
  --worker-class gthread --workers 1 --threads 4 \
  --bind 127.0.0.1:5001 --timeout 300 \
  --access-logfile - --error-logfile - wsgi:app
Restart=always
RestartSec=5
TimeoutStopSec=30
MemoryMax=750M
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

The app loads `backend/.env` itself, so no systemd `EnvironmentFile` is
required. This avoids inline-comment parsing differences between systemd and
python-dotenv.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now veyronix
sudo systemctl status veyronix
curl http://127.0.0.1:5001/health
```

## 5. Caddy

Copy `Caddyfile` from the repository root to the server:

```bash
sudo cp /opt/veyronix/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile
```

Set the domain through the environment used by Caddy:

```bash
sudo systemctl edit caddy
```

Add:

```ini
[Service]
Environment=VEYRONIX_DOMAIN=api.example.com
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart caddy
sudo systemctl status caddy
```

Caddy obtains a Let's Encrypt certificate automatically once DNS points to the
VM and ports 80/443 are open in the Oracle security list and `ufw`/iptables.

Test:

```bash
curl https://api.example.com/health
curl -H "Authorization: Bearer <token>" https://api.example.com/api/graph/list
```

Without the rate-limit module installed, Caddy fails to start with an
"unknown directive rate_limit" error. Install it with the command in section 2
or remove the `rate_limit` block for a temporary test.

## 6. Frontend on Vercel

Frontend repository: `https://github.com/markdaniel8899-source/Veyronix`

Vercel project settings:

- Framework preset: **Vite**
- Build command: `npm run build`
- Output directory: `dist`

Environment variables:

```
VITE_API_BASE_URL=https://api.example.com
VITE_API_BEARER_TOKEN=<same token as API_BEARER_TOKEN>
```

Add the Vercel production origin to `CORS_ORIGINS` in `backend/.env`, then
restart the backend:

```bash
sudo systemctl restart veyronix
```

The Bearer token is embedded in the frontend bundle at build time. Treat it as
a shared application token, rotate it if it leaks, and never store per-user
secrets in it.

## 7. SQLite operations

Backup (safe while the service runs, thanks to WAL):

```bash
cd /opt/veyronix/backend
sqlite3 uploads/veyronix.db ".backup '/var/backups/veyronix-$(date +%F).db'"
```

Inspect size and integrity:

```bash
du -h uploads/veyronix.db*
sqlite3 uploads/veyronix.db "PRAGMA integrity_check;"
```

Restore:

```bash
sudo systemctl stop veyronix
cp /var/backups/veyronix-YYYY-MM-DD.db uploads/veyronix.db
rm -f uploads/veyronix.db-wal uploads/veyronix.db-shm
sudo systemctl start veyronix
```

WAL files (`-wal`, `-shm`) are expected. Do not copy only the main `.db` file
while writes are active.

## 8. Updating

```bash
cd /opt/veyronix
sudo -u veyronix git pull --ff-only origin main
cd backend
sudo -u veyronix .venv/bin/pip install -r requirements.txt
sudo systemctl restart veyronix
```

Frontend: push to the `Veyronix` repository; Vercel builds automatically.

## 9. Health and troubleshooting

| Symptom | Check |
|---|---|
| `502 Bad Gateway` | `systemctl status veyronix`; backend must listen on `127.0.0.1:5001` |
| `401 Unauthorized` | Frontend `VITE_API_BEARER_TOKEN` must equal `API_BEARER_TOKEN` |
| CORS error | Add the exact origin to `CORS_ORIGINS` and restart the backend |
| `429 Too Many Requests` | Caddy per-IP limit hit; wait for the `Retry-After` window |
| LLM `429` / slow simulations | Keep `LLM_RPM=10`, `LLM_MAX_CONCURRENT=3`; reduce rounds or agents |
| `database is locked` | Confirm WAL mode and that only one backend worker is running |
| Out of memory | Keep one gunicorn worker; lower `LLM_MAX_CONCURRENT`; check `MemoryMax` |

Logs:

```bash
journalctl -u veyronix -f
tail -f /opt/veyronix/backend/logs/veyronix.log
journalctl -u caddy -f
```

## 10. Security checklist

- [ ] `backend/.env` exists only on the server and is never committed.
- [ ] `API_BEARER_TOKEN` and `SECRET_KEY` are unique random values.
- [ ] `CORS_ORIGINS` lists exact production origins, not `*`.
- [ ] Caddy serves HTTPS; port 5001 is bound to loopback only.
- [ ] Oracle security list and host firewall expose only 22, 80, and 443.
- [ ] Backups run on a schedule and are stored outside the VM.
- [ ] `FLASK_DEBUG=False` in production.
- [ ] `LLM_RPM` and `LLM_MAX_CONCURRENT` match the NVIDIA plan limits.
