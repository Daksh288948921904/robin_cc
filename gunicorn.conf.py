# ============================================================
# Gunicorn production configuration — robin cc
# ============================================================
# Uses uvicorn worker to run the FastAPI (ASGI) app.
# Single worker to preserve shared in-memory state (articles,
# summaries, image caches) across requests.
# ============================================================

import os

# ── Binding ──────────────────────────────────────────────────
bind     = f"0.0.0.0:{os.getenv('PORT', '5006')}"
backlog  = 128

# ── Workers ──────────────────────────────────────────────────
# NOTE: production uses `uvicorn wsgi:app` directly (see render.yaml / Render start command).
# This file is kept for local gunicorn usage only.
workers      = 1                                # MUST stay 1 (shared in-memory state)
worker_class = "uvicorn.workers.UvicornWorker"  # requires uvicorn[standard] installed

# ── Timeouts ─────────────────────────────────────────────────
timeout          = 300   # social scrape + AI generation can take ~2 min
graceful_timeout = 30
keepalive        = 5

# ── Logging ──────────────────────────────────────────────────
accesslog  = "-"
errorlog   = "-"
loglevel   = os.getenv("LOG_LEVEL", "info").lower()
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" %(D)sµs'

# ── Process naming ────────────────────────────────────────────
proc_name = "robin-cc"

# ── Security ─────────────────────────────────────────────────
limit_request_line   = 8190
limit_request_fields = 200

# ── Performance ──────────────────────────────────────────────
worker_tmp_dir = "/dev/shm"
