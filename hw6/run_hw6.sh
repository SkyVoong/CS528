#!/bin/bash
set -euo pipefail

PROJECT_ID="cs320-508117"
REGION="us-central1"
ZONE="us-central1-a"
SQL_INSTANCE="hw5-sql"
DB_NAME="hw5db"
DB_USER="hw5user"
DB_SECRET="hw5-db-pass"
BUCKET="skyvoong-cs528-hw2"
OUT_PREFIX="hw6"
SA_NAME="hw6-models-sa"
SA="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
VM="hw6-models"
JOB="hw5-sql-stopper-hourly"
HERE="$(cd "$(dirname "$0")" && pwd)"
G="gcloud --project=$PROJECT_ID --quiet"
exists() { "$@" >/dev/null 2>&1; }

wait_sql_ops() {
    for _ in $(seq 1 120); do
        [ -z "$($G sql operations list --instance="$SQL_INSTANCE" --filter='status!=DONE' --format='value(name)' 2>/dev/null)" ] && return 0
        sleep 20
    done
    return 1
}

set_db() {
    wait_sql_ops || return 1
    $G sql instances patch "$SQL_INSTANCE" --activation-policy="$1" --async >/dev/null || return 1
    wait_sql_ops
}

cleanup() {
    set +e
    echo "== cleanup: deleting the VM and stopping the database"
    exists $G compute instances describe "$VM" --zone="$ZONE" && $G compute instances delete "$VM" --zone="$ZONE"
    for _ in 1 2 3; do set_db NEVER && break; sleep 30; done
    exists $G scheduler jobs describe "$JOB" --location="$REGION" && $G scheduler jobs resume "$JOB" --location="$REGION" >/dev/null
    echo "database: $($G sql instances describe "$SQL_INSTANCE" --format='value(settings.activationPolicy)')"
}
trap cleanup EXIT

echo "== starting the database"
exists $G scheduler jobs describe "$JOB" --location="$REGION" && $G scheduler jobs pause "$JOB" --location="$REGION" >/dev/null
set_db ALWAYS || { echo "The database did not start."; exit 1; }
until [ "$($G sql instances describe "$SQL_INSTANCE" --format='value(state)')" = "RUNNABLE" ]; do sleep 10; done
echo "database is running"

echo "== service account $SA_NAME (Cloud SQL client, the DB password secret, the bucket)"
exists $G iam service-accounts describe "$SA" || $G iam service-accounts create "$SA_NAME" --display-name="hw6 models"
$G projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" \
    --role=roles/cloudsql.client --condition=None >/dev/null
$G secrets add-iam-policy-binding "$DB_SECRET" --member="serviceAccount:$SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
$G storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" \
    --role=roles/storage.objectAdmin >/dev/null

echo "== clearing old results in gs://$BUCKET/$OUT_PREFIX/"
$G storage rm "gs://$BUCKET/$OUT_PREFIX/**" >/dev/null 2>&1 || true

echo "== creating VM $VM (it installs the libraries and runs the models itself)"
$G compute instances create "$VM" --zone="$ZONE" --machine-type=e2-standard-2 \
    --image-family=debian-12 --image-project=debian-cloud \
    --service-account="$SA" --scopes=cloud-platform \
    --metadata="project-id=$PROJECT_ID,region=$REGION,sql-instance=$SQL_INSTANCE,db-name=$DB_NAME,db-user=$DB_USER,db-secret=$DB_SECRET,bucket=$BUCKET,out-prefix=$OUT_PREFIX" \
    --metadata-from-file="startup-script=$HERE/startup.sh,app-py=$HERE/hw6_models.py,normalize-sql=$HERE/normalize.sql" >/dev/null

echo "== waiting for results (usually 3-5 minutes)"
for _ in $(seq 1 90); do
    if exists $G storage ls "gs://$BUCKET/$OUT_PREFIX/summary.txt"; then break; fi
    if exists $G storage ls "gs://$BUCKET/$OUT_PREFIX/error.txt"; then
        echo "The VM reported an error:"; $G storage cat "gs://$BUCKET/$OUT_PREFIX/error.txt" | tail -30; exit 1
    fi
    sleep 20
done
exists $G storage ls "gs://$BUCKET/$OUT_PREFIX/summary.txt" || { echo "Timed out waiting for results."; exit 1; }

echo; echo "===== gs://$BUCKET/$OUT_PREFIX/summary.txt ====="
$G storage cat "gs://$BUCKET/$OUT_PREFIX/summary.txt"
TMP="$(mktemp -d)"
for f in ip_country_test_predictions.csv income_test_predictions.csv; do
    $G storage cp "gs://$BUCKET/$OUT_PREFIX/$f" "$TMP/$f" >/dev/null 2>&1
    echo; echo "===== gs://$BUCKET/$OUT_PREFIX/$f (first 15 of $(wc -l < "$TMP/$f") lines) ====="
    head -15 "$TMP/$f"
done
rm -rf "$TMP"
echo