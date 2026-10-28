set -uo pipefail

PROJECT_ID="cs320-508117"
REGION="us-central1"
ZONE="us-central1-a"
SQL_INSTANCE="hw5-sql"
TOPIC="hw5-forbidden-requests"
SUB="hw5-forbidden-requests-sub"
IP_NAME="hw5-web-ip"
FIREWALL="hw5-allow-http"
JOB="hw5-sql-stopper-hourly"

G="gcloud --project=$PROJECT_ID --quiet"
exists() { "$@" >/dev/null 2>&1; }

echo "== deleting VMs"
for vm in hw5-web hw5-forbidden hw5-client; do
    exists $G compute instances describe "$vm" --zone="$ZONE" \
        && $G compute instances delete "$vm" --zone="$ZONE"
done

echo "== releasing the static IP"
exists $G compute addresses describe "$IP_NAME" --region="$REGION" \
    && $G compute addresses delete "$IP_NAME" --region="$REGION"

echo "== deleting the firewall rule"
exists $G compute firewall-rules describe "$FIREWALL" && $G compute firewall-rules delete "$FIREWALL"

echo "== deleting Pub/Sub subscription and topic"
exists $G pubsub subscriptions describe "$SUB" && $G pubsub subscriptions delete "$SUB"
exists $G pubsub topics describe "$TOPIC" && $G pubsub topics delete "$TOPIC"

echo "== stopping Cloud SQL (not deleting)"
exists $G sql instances describe "$SQL_INSTANCE" \
    && $G sql instances patch "$SQL_INSTANCE" --activation-policy=NEVER

echo "== making sure the hourly stopper job is enabled"
exists $G scheduler jobs describe "$JOB" --location="$REGION" \
    && $G scheduler jobs resume "$JOB" --location="$REGION"

echo "Done."
