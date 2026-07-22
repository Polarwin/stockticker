#!/usr/bin/env bash
# Install the stockticker-web systemd service (web UI with built-in Telegram
# notifications: earnings reminders/reports, post-earnings watch, and
# optional price alerts toggled from the UI) and deploy nginx as a reverse
# proxy so the web UI is reachable on the LAN at http://<LAN-IP>/stockticker
# while Flask keeps listening on localhost only.
set -euo pipefail

WEB_SERVICE_NAME="stockticker-web"
WEB_SERVICE_FILE="/etc/systemd/system/${WEB_SERVICE_NAME}.service"

NGINX_SITE_NAME="stockticker"
NGINX_SITE_FILE="/etc/nginx/sites-available/${NGINX_SITE_NAME}"
NGINX_ENABLED_LINK="/etc/nginx/sites-enabled/${NGINX_SITE_NAME}"
URL_PATH="/stockticker"

# Resolve the project directory from the script location so it works when run
# as "bash install_service.sh" from the project folder.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${PROJECT_DIR}"
USER_NAME="$(whoami)"

# --- systemd service --------------------------------------------------------

cat <<EOF | sudo tee "${WEB_SERVICE_FILE}" >/dev/null
[Unit]
Description=Stockticker web UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/bin/python ${PROJECT_DIR}/web.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload

# Idempotent start/restart: enable & start if not running, otherwise restart
# so the updated unit file takes effect.
if systemctl is-active --quiet "${WEB_SERVICE_NAME}" 2>/dev/null; then
    echo "${WEB_SERVICE_NAME} is already running; restarting with updated unit..."
    sudo systemctl restart "${WEB_SERVICE_NAME}"
else
    sudo systemctl enable --now "${WEB_SERVICE_NAME}"
fi

# --- nginx reverse proxy ----------------------------------------------------

# Read the Flask bind address from settings.json (fallback: 127.0.0.1:8010).
read -r WEB_HOST WEB_PORT <<< "$("${PROJECT_DIR}/bin/python" - <<'EOF'
import json
try:
    with open("settings.json") as f:
        s = json.load(f)
except Exception:
    s = {}
print(s.get("web_host", "127.0.0.1"), s.get("web_port", 8010))
EOF
)"

echo "Proxy target: http://${WEB_HOST}:${WEB_PORT} (Flask backend)"

# Install nginx if missing.
if ! command -v nginx >/dev/null 2>&1; then
    echo "Installing nginx..."
    sudo apt-get update
    sudo apt-get install -y nginx
fi

# Write the site config. The trailing slash on proxy_pass strips the
# /stockticker prefix, so Flask sees its normal / and /api/... paths.
cat <<EOF | sudo tee "${NGINX_SITE_FILE}" >/dev/null
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    location = ${URL_PATH} {
        return 301 ${URL_PATH}/;
    }

    location ${URL_PATH}/ {
        proxy_pass http://${WEB_HOST}:${WEB_PORT}/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Prefix ${URL_PATH};
    }
}
EOF

# The stock "default" site also claims port 80 as default_server; remove it so
# this config takes over. (Only affects the placeholder welcome page.)
if [ -L /etc/nginx/sites-enabled/default ]; then
    echo "Removing default nginx site..."
    sudo rm /etc/nginx/sites-enabled/default
fi

sudo ln -sfn "${NGINX_SITE_FILE}" "${NGINX_ENABLED_LINK}"

echo "Testing nginx config..."
sudo nginx -t

if systemctl is-active --quiet nginx 2>/dev/null; then
    echo "Reloading nginx..."
    sudo systemctl reload nginx
else
    echo "Enabling and starting nginx..."
    sudo systemctl enable --now nginx
fi

# --- summary ----------------------------------------------------------------

LAN_IP="$(hostname -I | awk '{print $1}')"

echo ""
echo "Service status:"
sudo systemctl status --no-pager "${WEB_SERVICE_NAME}"

echo ""
echo "Web UI: http://${LAN_IP}${URL_PATH}"
echo "(If ufw is active, allow HTTP first: sudo ufw allow 80/tcp)"

echo ""
echo "Cheat sheet:"
echo "  Restart:  sudo systemctl restart ${WEB_SERVICE_NAME}"
echo "  Stop:     sudo systemctl stop ${WEB_SERVICE_NAME}"
echo "  Logs:     sudo journalctl -u ${WEB_SERVICE_NAME} -f"
echo "  Status:   sudo systemctl status ${WEB_SERVICE_NAME}"
echo "  Disable:  sudo systemctl disable --now ${WEB_SERVICE_NAME}"
echo "  Nginx:    sudo systemctl reload nginx"
