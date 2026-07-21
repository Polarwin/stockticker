#!/usr/bin/env bash
set -euo pipefail

for SERVICE_NAME in stockticker stockticker-web; do
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        echo "Stopping ${SERVICE_NAME}..."
        sudo systemctl stop "${SERVICE_NAME}"
    fi

    if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
        echo "Disabling ${SERVICE_NAME}..."
        sudo systemctl disable "${SERVICE_NAME}"
    fi

    if [ -f "${SERVICE_FILE}" ]; then
        echo "Removing ${SERVICE_FILE}..."
        sudo rm "${SERVICE_FILE}"
    fi
done

sudo systemctl daemon-reload

echo "stockticker services uninstalled."
