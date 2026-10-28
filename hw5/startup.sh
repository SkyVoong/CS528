if [ -f /var/log/startup_already_done ]; then
    echo "Startup script already ran once. Skipping."
    exit 0
fi

set -euxo pipefail

MD=http://metadata.google.internal/computeMetadata/v1/instance/attributes
attr()    { curl -fsS -H 'Metadata-Flavor: Google' "$MD/$1"; }
attr_or() { curl -fsS -H 'Metadata-Flavor: Google' "$MD/$1" 2>/dev/null || echo "$2"; }

ROLE="$(attr role)"
PROJECT_ID="$(attr project-id)"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv

if [ "$ROLE" = "web" ]; then
    APP=/opt/hw5-web
    mkdir -p "$APP"
    python3 -m venv "$APP/venv"
    # requests is listed explicitly: it avoids the google-auth metadata bug from lecture 8
    "$APP/venv/bin/pip" install google-cloud-storage google-cloud-pubsub google-cloud-logging \
        "cloud-sql-python-connector[pymysql]" sqlalchemy requests
    attr app-py > "$APP/web_server.py"

    # Database password: read from Secret Manager with the VM's service account.
    DB_PASS="$(gcloud secrets versions access latest --secret="$(attr db-secret)" --project="$PROJECT_ID")"

    umask 077                                   # env file holds the password: root-only
    cat > /etc/hw5-web.env <<EOF
PROJECT_ID=$PROJECT_ID
REGION=$(attr region)
SQL_INSTANCE=$(attr sql-instance)
DB_NAME=$(attr db-name)
DB_USER=$(attr db-user)
DB_PASS=$DB_PASS
BUCKET=$(attr_or bucket skyvoong-cs528-hw2)
PREFIX=$(attr_or prefix files/)
TOPIC=$(attr topic)
PORT=80
EOF
    umask 022

    cat > /etc/systemd/system/hw5-web.service <<EOF
[Unit]
Description=HW5 web server
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/hw5-web.env
ExecStart=$APP/venv/bin/python -u $APP/web_server.py
User=nobody
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    SERVICE=hw5-web

elif [ "$ROLE" = "forbidden" ]; then
    APP=/opt/hw5-forbidden
    mkdir -p "$APP"
    python3 -m venv "$APP/venv"
    "$APP/venv/bin/pip" install google-cloud-pubsub requests
    attr app-py > "$APP/forbidden_service.py"

    cat > /etc/hw5-forbidden.env <<EOF
PROJECT_ID=$PROJECT_ID
SUBSCRIPTION=$(attr subscription)
EOF

    cat > /etc/systemd/system/hw5-forbidden.service <<EOF
[Unit]
Description=HW5 forbidden-requests service
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/hw5-forbidden.env
ExecStart=$APP/venv/bin/python -u $APP/forbidden_service.py
User=nobody
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    SERVICE=hw5-forbidden

else
    echo "unknown role '$ROLE'" >&2
    exit 1
fi

systemctl daemon-reload
systemctl enable --now "$SERVICE"

# Create the lock file to prevent future runs
touch /var/log/startup_already_done
