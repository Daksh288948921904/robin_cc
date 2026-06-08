# ============================================================
# Gunicorn production configuration — robin cc
# ============================================================
# IMPORTANT: We intentionally use 1 worker + multiple threads.
#
# The Flask app keeps all article data, AI-image URLs, summaries,
# and other caches in plain Python dicts. Multiple OS processes
# would each have their own separate memory space, so a scrape
# triggered to worker-1 would be invisible to worker-2.
#
# 1 worker + 4 threads gives safe concurrency without splitting state.
# ============================================================

import multiprocessing, os

# ── Binding ──────────────────────────────────────────────────
# Render assigns a dynamic PORT env var; fall back to 5001 for local
bind        = f"0.0.0.0:{os.getenv('PORT', '5006')}"
backlog     = 128

# ── Workers ──────────────────────────────────────────────────
workers     = 1                  # MUST stay 1 (shared in-memory state)
threads     = 4                  # concurrent request handling per worker
worker_class = "gthread"

# ── Timeouts ─────────────────────────────────────────────────
timeout         = 300   # social scrape + AI generation can take ~2 min
graceful_timeout = 30
keepalive        = 5

# ── Logging ──────────────────────────────────────────────────
accesslog  = "-"   # stdout (Render captures this)
errorlog   = "-"   # stderr
loglevel   = os.getenv("LOG_LEVEL", "info").lower()
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" %(D)sµs'

# ── Process naming ────────────────────────────────────────────
proc_name  = "robin-cc"

# ── Security ─────────────────────────────────────────────────
limit_request_line   = 8190
limit_request_fields = 200

# ── Performance ──────────────────────────────────────────────
worker_tmp_dir = "/dev/shm"   # use RAM for temp files on Linux
