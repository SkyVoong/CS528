#!/bin/bash
# VM startup script for the web server VM. Runs as root on every boot.
# Everything it needs comes from instance metadata, so there is no git or
# bucket dependency:
#   attribute app-py      the contents of web/server.py
#   attributes bucket, prefix, topic, project-id, queue-size, threaded
set -euxo pipefail

MD=http://metadata.google.internal/computeMetadata/v1/instance/attributes
attr() { curl -fsS -H 'Metadata-Flavor: Google' "$MD/$1"; }
attr_or() { curl -fsS -H 'Metadata-Flavor: Google' "$MD/$1" 2>/dev/null || echo "$2"; }

APP=/opt/hw4-web
mkdir -p "$APP"

if [ ! -x "$APP/venv/bin/python" ]; then          # first boot only
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y python3-venv
  python3 -m venv "$APP/venv"
  # requests is listed explicitly: it avoids the google-auth/OAuth metadata bug from lecture 8
  "$APP/venv/bin/pip" install google-cloud-storage google-cloud-pubsub google-cloud-logging requests
fi

attr app-py > "$APP/server.py"                     # refresh the code on every boot

cat > /etc/hw4-web.env <<EOF
PROJECT_ID=$(attr project-id)
BUCKET=$(attr_or bucket skyvoong-cs528-hw2)
PREFIX=$(attr_or prefix files/)
TOPIC=$(attr_or topic hw4-forbidden-requests)
QUEUE_SIZE=$(attr_or queue-size 5)
THREADED=$(attr_or threaded 0)
PORT=80
EOF

cat > /etc/systemd/system/hw4-web.service <<EOF
[Unit]
Description=HW4 web server
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/hw4-web.env
ExecStart=$APP/venv/bin/python -u $APP/server.py
User=nobody
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable hw4-web
systemctl restart hw4-web