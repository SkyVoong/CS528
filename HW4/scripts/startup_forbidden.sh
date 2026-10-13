set -euxo pipefail

MD=http://metadata.google.internal/computeMetadata/v1/instance/attributes
attr() { curl -fsS -H 'Metadata-Flavor: Google' "$MD/$1"; }

APP=/opt/hw4-forbidden
mkdir -p "$APP"

if [ ! -x "$APP/venv/bin/python" ]; then          # first boot only
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y python3-venv
  python3 -m venv "$APP/venv"
  "$APP/venv/bin/pip" install google-cloud-pubsub requests
fi

attr app-py > "$APP/forbidden_service.py"

cat > /etc/hw4-forbidden.env <<EOF
PROJECT_ID=$(attr project-id)
SUBSCRIPTION=$(attr subscription)
EOF

cat > /etc/systemd/system/hw4-forbidden.service <<EOF
[Unit]
Description=HW4 forbidden-requests service
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/hw4-forbidden.env
ExecStart=$APP/venv/bin/python -u $APP/forbidden_service.py
User=nobody
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable hw4-forbidden
systemctl restart hw4-forbidden