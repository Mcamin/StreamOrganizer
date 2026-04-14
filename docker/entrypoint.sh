#!/usr/bin/env bash
set -euo pipefail

: "${CRON_SCHEDULE:=0 * * * *}"
: "${TZ:=UTC}"
: "${RUN_ON_START:=false}"

export TZ
ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
echo "$TZ" > /etc/timezone

printenv | sed 's/^/export /' > /etc/profile.d/container_env.sh
chmod +x /etc/profile.d/container_env.sh

cat >/etc/cron.d/sync-watched <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
TZ=$TZ
$CRON_SCHEDULE root . /etc/profile.d/container_env.sh; cd /app && python3 /app/sync_watched.py >> /proc/1/fd/1 2>> /proc/1/fd/2
EOF

chmod 0644 /etc/cron.d/sync-watched
crontab /etc/cron.d/sync-watched

echo "[sync-watched] timezone: $TZ"
echo "[sync-watched] schedule: $CRON_SCHEDULE"

if [[ "$RUN_ON_START" == "true" ]]; then
  echo "[sync-watched] running once on container start"
  python3 /app/sync_watched.py
fi

echo "[sync-watched] cron started"
exec cron -f
