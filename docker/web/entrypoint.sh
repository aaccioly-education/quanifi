#!/bin/sh
set -e
python manage.py migrate --noinput
if [ -n "${QUANIFI_ADMIN_PASSWORD:-}" ]; then
    python manage.py bootstrap_admin
else
    echo "QUANIFI_ADMIN_PASSWORD is not set; skipping the administrator bootstrap"
fi
exec "$@"
