#!/usr/bin/env bash
# Remove the stockticker systemd services and the nginx reverse proxy site.
# The nginx package itself and the local database are left untouched.
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

NGINX_SITE_FILE="/etc/nginx/sites-available/stockticker"
NGINX_ENABLED_LINK="/etc/nginx/sites-enabled/stockticker"

if [ -L "${NGINX_ENABLED_LINK}" ] || [ -f "${NGINX_SITE_FILE}" ]; then
    echo "Removing nginx stockticker site..."
    sudo rm -f "${NGINX_ENABLED_LINK}" "${NGINX_SITE_FILE}"
    if systemctl is-active --quiet nginx 2>/dev/null; then
        sudo nginx -t && sudo systemctl reload nginx
    fi
fi

echo "stockticker services and nginx site uninstalled."
