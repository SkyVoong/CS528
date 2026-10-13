#!/bin/bash
# Creates everything for HW4 and starts the programs on the VMs
set -euo pipefail

PROJECT="${1:?usage: $0 <PROJECT_ID>}"
BUCKET="${BUCKET:-skyvoong-cs528-hw2}"
PREFIX="${PREFIX:-files/}"
ZONE="${ZONE:-us-central1-a}"
REGION="${ZONE%-*}"
WEB_MACHINE="${WEB_MACHINE:-e2-micro}"
FORBIDDEN_MACHINE="${FORBIDDEN_MACHINE:-e2-micro}"
QUEUE_SIZE="${QUEUE_SIZE:-5}"
THREADED="${THREADED:-0}"

TOPIC=hw4-forbidden-requests
SUB=hw4-forbidden-requests-sub
WEB_SA_NAME=hw4-web-sa
FORBIDDEN_SA_NAME=hw4-forbidden-sa
WEB_SA="$WEB_SA_NAME@$PROJECT.iam.gserviceaccount.com"
FORBIDDEN_SA="$FORBIDDEN_SA_NAME@$PROJECT.iam.gserviceaccount.com"
IP_NAME=hw4-web-ip
WEB_VM=hw4-web
FORBIDDEN_VM=hw4-forbidden
FIREWALL=hw4-allow-http
HERE="$(cd "$(dirname "$0")" && pwd)"

G="gcloud --project=$PROJECT --quiet"

echo "== enabling APIs"
$G services enable compute.googleapis.com pubsub.googleapis.com logging.googleapis.com storage.googleapis.com

echo "== service accounts (least privilege)"
for pair in "$WEB_SA_NAME:hw4 web server" "$FORBIDDEN_SA_NAME:hw4 forbidden-requests service"; do
  name="${pair%%:*}"; display="${pair#*:}"
  $G iam service-accounts describe "$name@$PROJECT.iam.gserviceaccount.com" >/dev/null 2>&1 \
    || $G iam service-accounts create "$name" --display-name="$display"
done

echo "== Pub/Sub topic and subscription"
$G pubsub topics describe $TOPIC >/dev/null 2>&1 || $G pubsub topics create $TOPIC
$G pubsub subscriptions describe $SUB >/dev/null 2>&1 \
  || $G pubsub subscriptions create $SUB --topic=$TOPIC --ack-deadline=30

echo "== permissions"
# web server: read the bucket, write logs, publish to ONE topic
$G storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$WEB_SA" --role=roles/storage.objectViewer >/dev/null
$G projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$WEB_SA" --role=roles/logging.logWriter --condition=None >/dev/null
$G pubsub topics add-iam-policy-binding $TOPIC \
  --member="serviceAccount:$WEB_SA" --role=roles/pubsub.publisher >/dev/null
# forbidden service: consume ONE subscription, nothing else
$G pubsub subscriptions add-iam-policy-binding $SUB \
  --member="serviceAccount:$FORBIDDEN_SA" --role=roles/pubsub.subscriber >/dev/null

echo "== firewall (HTTP to VMs tagged hw4-web) and static IP"
$G compute firewall-rules describe $FIREWALL >/dev/null 2>&1 \
  || $G compute firewall-rules create $FIREWALL --network=default --direction=INGRESS \
       --allow=tcp:80 --target-tags=hw4-web --source-ranges=0.0.0.0/0
$G compute addresses describe $IP_NAME --region="$REGION" >/dev/null 2>&1 \
  || $G compute addresses create $IP_NAME --region="$REGION"
WEB_IP="$($G compute addresses describe $IP_NAME --region="$REGION" --format='value(address)')"

echo "== web server VM ($WEB_MACHINE, static IP $WEB_IP)"
$G compute instances describe $WEB_VM --zone="$ZONE" >/dev/null 2>&1 \
  || $G compute instances create $WEB_VM --zone="$ZONE" --machine-type="$WEB_MACHINE" \
       --image-family=debian-12 --image-project=debian-cloud \
       --address="$WEB_IP" --tags=hw4-web \
       --service-account="$WEB_SA" --scopes=cloud-platform \
       --metadata="project-id=$PROJECT,bucket=$BUCKET,prefix=$PREFIX,topic=$TOPIC,queue-size=$QUEUE_SIZE,threaded=$THREADED" \
       --metadata-from-file="startup-script=$HERE/scripts/startup_web.sh,app-py=$HERE/web/server.py"

echo "== forbidden-requests VM ($FORBIDDEN_MACHINE)"
$G compute instances describe $FORBIDDEN_VM --zone="$ZONE" >/dev/null 2>&1 \
  || $G compute instances create $FORBIDDEN_VM --zone="$ZONE" --machine-type="$FORBIDDEN_MACHINE" \
       --image-family=debian-12 --image-project=debian-cloud \
       --service-account="$FORBIDDEN_SA" --scopes=cloud-platform \
       --metadata="project-id=$PROJECT,subscription=$SUB" \
       --metadata-from-file="startup-script=$HERE/scripts/startup_forbidden.sh,app-py=$HERE/forbidden/forbidden_service.py"

cat <<EOF

Done. The programs install and start themselves; allow 2-3 minutes after creation.
  Web server:   http://$WEB_IP/files/0.html
  Check it:     gcloud compute ssh $WEB_VM --zone=$ZONE --project=$PROJECT --command='sudo systemctl status hw4-web --no-pager'
  Forbidden:    gcloud compute ssh $FORBIDDEN_VM --zone=$ZONE --project=$PROJECT --command='sudo journalctl -u hw4-forbidden -f'
EOF
