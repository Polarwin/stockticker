#!/usr/bin/env bash
# Deploy nginx as a reverse proxy so the stockticker web UI is reachable at
# http://<LAN-IP>/stockticker (Flask keeps listening on localhost only).
set -euo pipefail

SITE_NAME="stockticker"
SITE_FILE="/etc/nginx/sites-available/${SITE_NAME}"
ENABLED_LINK="/etc/nginx/sites-enabled/${SITE_NAME}"
URL_PATH="/stockticker"

# Resolve the project directory from the script location so it works when run
# as "bash install_nginx.sh" from the project folder.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${PROJECT_DIR}"

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
cat <<EOF | sudo tee "${SITE_FILE}" >/dev/null
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

sudo ln -sfn "${SITE_FILE}" "${ENABLED_LINK}"

echo "Testing nginx config..."
sudo nginx -t

if systemctl is-active --quiet nginx 2>/dev/null; then
    echo "Reloading nginx..."
    sudo systemctl reload nginx
else
    echo "Enabling and starting nginx..."
    sudo systemctl enable --now nginx
fi

LAN_IP="$(hostname -I | awk '{print $1}')"

echo ""
echo "Done. Open from any device on the LAN:"
echo "  http://${LAN_IP}${URL_PATH}"
echo ""
echo "If ufw is active, allow HTTP first:  sudo ufw allow 80/tcp"
echo ""
echo "Cheat sheet:"
echo "  Config:   ${SITE_FILE}"
echo "  Reload:   sudo systemctl reload nginx"
echo "  Logs:     sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log"
echo "  Remove:   sudo rm ${ENABLED_LINK} && sudo systemctl reload nginx"
