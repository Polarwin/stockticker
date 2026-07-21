#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="stockticker"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Resolve the project directory from the script location so it works when run
# as "bash install_service.sh" from the project folder.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="$(whoami)"

cat <<EOF | sudo tee "${SERVICE_FILE}" >/dev/null
[Unit]
Description=Stock price watcher with Telegram alerts
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/bin/python ${PROJECT_DIR}/main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

WEB_SERVICE_NAME="stockticker-web"
WEB_SERVICE_FILE="/etc/systemd/system/${WEB_SERVICE_NAME}.service"

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
for UNIT in "${SERVICE_NAME}" "${WEB_SERVICE_NAME}"; do
    if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then
        echo "${UNIT} is already running; restarting with updated unit..."
        sudo systemctl restart "${UNIT}"
    else
        sudo systemctl enable --now "${UNIT}"
    fi
done

echo ""
echo "Service status:"
sudo systemctl status --no-pager "${SERVICE_NAME}" "${WEB_SERVICE_NAME}"

echo ""
echo "Cheat sheet:"
echo "  Restart:  sudo systemctl restart ${SERVICE_NAME} ${WEB_SERVICE_NAME}"
echo "  Stop:     sudo systemctl stop ${SERVICE_NAME} ${WEB_SERVICE_NAME}"
echo "  Logs:     sudo journalctl -u ${SERVICE_NAME} -f"
echo "  Web logs: sudo journalctl -u ${WEB_SERVICE_NAME} -f"
echo "  Status:   sudo systemctl status ${SERVICE_NAME} ${WEB_SERVICE_NAME}"
echo "  Disable:  sudo systemctl disable --now ${SERVICE_NAME} ${WEB_SERVICE_NAME}"
