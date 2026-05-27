#!/usr/bin/env bash
# ============================================================
# robin cc — One-shot Hostinger VPS Setup Script
# Tested on: Ubuntu 22.04 LTS
#
# Run as root on a FRESH VPS:
#   chmod +x setup_hostinger.sh
#   sudo bash setup_hostinger.sh
#
# What this does:
#   1. System update + essential packages
#   2. Python 3.11 + pip
#   3. Nginx install + config
#   4. Project clone + virtualenv + pip install
#   5. Log directories + permissions
#   6. systemd service enable + start
#   7. SSL with Let's Encrypt (optional step)
# ============================================================

set -euo pipefail

# ── CONFIG — edit before running ─────────────────────────────
DOMAIN="your-domain.com"          # e.g. news.example.com
REPO_URL="https://github.com/YOUR_ORG/osi-news-automation.git"
APP_DIR="/var/www/robin-cc"
APP_USER="www-data"
PYTHON="python3.11"
# ─────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

info "=== robin cc Hostinger VPS Setup ==="

# ── 1. System update ─────────────────────────────────────────
info "Updating system packages..."
apt-get update -y
apt-get upgrade -y
apt-get install -y \
    git curl wget unzip build-essential \
    nginx certbot python3-certbot-nginx \
    python3.11 python3.11-venv python3.11-dev python3-pip \
    libxml2-dev libxslt1-dev libjpeg-dev libpng-dev \
    libssl-dev libffi-dev

# ── 2. Create app directory ───────────────────────────────────
info "Setting up application directory at $APP_DIR..."
mkdir -p "$APP_DIR"
mkdir -p /var/log/robin-cc
mkdir -p /var/log/nginx

# ── 3. Clone repository ───────────────────────────────────────
if [ -d "$APP_DIR/.git" ]; then
    info "Repository already exists — pulling latest..."
    cd "$APP_DIR"
    git pull origin main
else
    info "Cloning repository..."
    git clone "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

# ── 4. Python virtual environment ────────────────────────────
info "Creating Python virtualenv..."
$PYTHON -m venv "$APP_DIR/venv"
source "$APP_DIR/venv/bin/activate"

info "Upgrading pip..."
pip install --upgrade pip wheel setuptools

info "Installing production dependencies (this takes ~5 min for torch CPU)..."
pip install -r requirements-hostinger.txt

# ── 5. Create required directories ───────────────────────────
info "Creating runtime directories..."
mkdir -p "$APP_DIR/output/json"
mkdir -p "$APP_DIR/output/images"
mkdir -p "$APP_DIR/output/logs"
mkdir -p "$APP_DIR/src/frontend/static/ai_images"

# ── 6. Environment file ───────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
    warn ".env file not found — copying from .env.example"
    warn ">>> EDIT $APP_DIR/.env with real credentials before starting the service <<<"
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi

# Set a random SECRET_KEY if not already set
if ! grep -q "^SECRET_KEY=" "$APP_DIR/.env" || grep -q "SECRET_KEY=$" "$APP_DIR/.env"; then
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    echo "SECRET_KEY=$SECRET" >> "$APP_DIR/.env"
    info "Generated SECRET_KEY and appended to .env"
fi

# ── 7. File permissions ───────────────────────────────────────
info "Setting file permissions..."
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
chown -R "$APP_USER":"$APP_USER" /var/log/robin-cc
chmod -R 755 "$APP_DIR"
chmod 600 "$APP_DIR/.env"           # protect credentials
chmod 775 "$APP_DIR/output"
chmod 775 "$APP_DIR/src/frontend/static/ai_images"

# ── 8. Nginx configuration ────────────────────────────────────
info "Configuring nginx..."
sed "s/YOUR_DOMAIN/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf" \
    > /etc/nginx/sites-available/robin-cc

# Enable site, disable default
ln -sf /etc/nginx/sites-available/robin-cc /etc/nginx/sites-enabled/robin-cc
rm -f /etc/nginx/sites-enabled/default

nginx -t && info "Nginx config OK"
systemctl reload nginx

# ── 9. systemd service ───────────────────────────────────────
info "Installing systemd service..."
sed "s|/var/www/robin-cc|$APP_DIR|g" "$APP_DIR/deploy/robin-cc.service" \
    > /etc/systemd/system/robin-cc.service

systemctl daemon-reload
systemctl enable robin-cc
systemctl start robin-cc
sleep 3
systemctl is-active --quiet robin-cc && info "robin-cc service is running" \
    || error "Service failed to start — check: journalctl -u robin-cc -n 50"

# ── 10. SSL (Let's Encrypt) ───────────────────────────────────
read -rp "Set up SSL with Let's Encrypt now? (y/N): " SSL_CONFIRM
if [[ "$SSL_CONFIRM" =~ ^[Yy]$ ]]; then
    info "Running certbot..."
    certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" --non-interactive \
        --agree-tos -m "admin@$DOMAIN" --redirect
    systemctl reload nginx
    info "SSL configured. Auto-renewal is handled by certbot.timer."
else
    warn "Skipping SSL. Run manually: certbot --nginx -d $DOMAIN"
fi

# ── Done ──────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  robin cc is deployed!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "  App URL     : http://$DOMAIN"
echo "  App dir     : $APP_DIR"
echo "  Service logs: journalctl -u robin-cc -f"
echo "  Nginx logs  : tail -f /var/log/nginx/robin-cc.access.log"
echo "  Gunicorn log: tail -f /var/log/robin-cc/error.log"
echo ""
echo -e "${YELLOW}  IMPORTANT: Edit $APP_DIR/.env with your real API keys${NC}"
echo -e "${YELLOW}  Then restart: sudo systemctl restart robin-cc${NC}"
echo ""
