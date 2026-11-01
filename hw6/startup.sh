#!/bin/bash
if [ -f /var/log/startup_already_done ]; then
    echo "Startup script already ran once. Skipping."
    exit 0
fi

MD=http://metadata.google.internal/computeMetadata/v1/instance/attributes
attr() { curl -fsS -H 'Metadata-Flavor: Google' "$MD/$1"; }

BUCKET="$(attr bucket)"
OUT_PREFIX="$(attr out-prefix)"
LOG=/var/log/hw6.log

fail() {
    echo "FAILED: $1" >> "$LOG"
    gcloud storage cp "$LOG" "gs://$BUCKET/$OUT_PREFIX/error.txt"
    exit 1
}

install() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update || return 1
    apt-get install -y python3-venv || return 1
    mkdir -p /opt/hw6 || return 1
    python3 -m venv /opt/hw6/venv || return 1
    /opt/hw6/venv/bin/pip install -q scikit-learn google-cloud-storage \
        "cloud-sql-python-connector[pymysql]" requests || return 1
    attr app-py > /opt/hw6/hw6_models.py || return 1
    attr normalize-sql > /opt/hw6/normalize.sql || return 1
}

install >> "$LOG" 2>&1 || fail "installing libraries"

PROJECT_ID="$(attr project-id)"
REGION="$(attr region)"
SQL_INSTANCE="$(attr sql-instance)"
DB_NAME="$(attr db-name)"
DB_USER="$(attr db-user)"
export PROJECT_ID REGION SQL_INSTANCE DB_NAME DB_USER BUCKET OUT_PREFIX
DB_PASS="$(gcloud secrets versions access latest --secret="$(attr db-secret)" --project="$PROJECT_ID")" \
    || fail "reading the database password"
export DB_PASS

/opt/hw6/venv/bin/python -u /opt/hw6/hw6_models.py >> "$LOG" 2>&1 || fail "running hw6_models.py"

touch /var/log/startup_already_done
