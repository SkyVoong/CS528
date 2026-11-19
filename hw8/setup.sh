#!/bin/bash
set -euo pipefail

PROJECT_ID="cs320-508117"
REGION="us-central1"
ZONES=("us-central1-a" "us-central1-b")
BUCKET="skyvoong-cs528-hw2"
PREFIX="files/"
TOPIC="hw8-forbidden-requests"
SUB="hw8-forbidden-requests-sub"
WEB_SA_NAME="hw8-web-sa"
FORBIDDEN_SA_NAME="hw8-forbidden-sa"
WEB_SA="$WEB_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
FORBIDDEN_SA="$FORBIDDEN_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
FORBIDDEN_VM="hw8-forbidden"
FIREWALL="hw8-allow-http"
HC="hw8-health-check"
BACKEND="hw8-backend"
IP_NAME="hw8-lb-ip"
FR="hw8-lb"
HERE="$(cd "$(dirname "$0")" && pwd)"
HW4="$HERE/../HW4"
G="gcloud --project=$PROJECT_ID --quiet"
exists() { "$@" >/dev/null 2>&1; }

echo "== enabling APIs"
$G services enable compute.googleapis.com pubsub.googleapis.com logging.googleapis.com storage.googleapis.com

echo "== service accounts"
exists $G iam service-accounts describe "$WEB_SA" \
    || $G iam service-accounts create "$WEB_SA_NAME" --display-name="hw8 web server"
exists $G iam service-accounts describe "$FORBIDDEN_SA" \
    || $G iam service-accounts create "$FORBIDDEN_SA_NAME" --display-name="hw8 forbidden-requests service"

echo "== Pub/Sub"
exists $G pubsub topics describe "$TOPIC" || $G pubsub topics create "$TOPIC"
exists $G pubsub subscriptions describe "$SUB" \
    || $G pubsub subscriptions create "$SUB" --topic="$TOPIC" --ack-deadline=30

echo "== permissions"
$G storage buckets add-iam-policy-binding "gs://$BUCKET" \
    --member="serviceAccount:$WEB_SA" --role=roles/storage.objectViewer >/dev/null
$G projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$WEB_SA" --role=roles/logging.logWriter --condition=None >/dev/null
$G pubsub topics add-iam-policy-binding "$TOPIC" \
    --member="serviceAccount:$WEB_SA" --role=roles/pubsub.publisher >/dev/null
$G pubsub subscriptions add-iam-policy-binding "$SUB" \
    --member="serviceAccount:$FORBIDDEN_SA" --role=roles/pubsub.subscriber >/dev/null

echo "== firewall (clients and health checks to port 80)"
exists $G compute firewall-rules describe "$FIREWALL" \
    || $G compute firewall-rules create "$FIREWALL" --network=default --direction=INGRESS \
         --allow=tcp:80 --target-tags=hw8-web --source-ranges=0.0.0.0/0

for ZONE in "${ZONES[@]}"; do
    VM="hw8-web-${ZONE##*-}"
    IG="hw8-ig-${ZONE##*-}"
    echo "== web server $VM in $ZONE"
    exists $G compute instances describe "$VM" --zone="$ZONE" \
        || $G compute instances create "$VM" --zone="$ZONE" --machine-type=e2-micro \
             --image-family=debian-12 --image-project=debian-cloud --tags=hw8-web \
             --service-account="$WEB_SA" --scopes=cloud-platform \
             --metadata="project-id=$PROJECT_ID,bucket=$BUCKET,prefix=$PREFIX,topic=$TOPIC,queue-size=64,threaded=1" \
             --metadata-from-file="startup-script=$HW4/scripts/startup_web.sh,app-py=$HERE/server.py"
    exists $G compute instance-groups unmanaged describe "$IG" --zone="$ZONE" \
        || $G compute instance-groups unmanaged create "$IG" --zone="$ZONE"
    MEMBERS="$($G compute instance-groups unmanaged list-instances "$IG" --zone="$ZONE" --format='value(instance)')"
    [[ "$MEMBERS" == *"$VM"* ]] \
        || $G compute instance-groups unmanaged add-instances "$IG" --zone="$ZONE" --instances="$VM"
done

echo "== forbidden-requests VM"
exists $G compute instances describe "$FORBIDDEN_VM" --zone="${ZONES[0]}" \
    || $G compute instances create "$FORBIDDEN_VM" --zone="${ZONES[0]}" --machine-type=e2-micro \
         --image-family=debian-12 --image-project=debian-cloud \
         --service-account="$FORBIDDEN_SA" --scopes=cloud-platform \
         --metadata="project-id=$PROJECT_ID,subscription=$SUB" \
         --metadata-from-file="startup-script=$HW4/scripts/startup_forbidden.sh,app-py=$HW4/forbidden/forbidden_service.py"

echo "== health check (every 2 s, 2 failures = unhealthy, 2 successes = healthy)"
exists $G compute health-checks describe "$HC" --region="$REGION" \
    || $G compute health-checks create http "$HC" --region="$REGION" --port=80 \
         --request-path=/health --check-interval=2s --timeout=2s \
         --healthy-threshold=2 --unhealthy-threshold=2

echo "== backend service"
exists $G compute backend-services describe "$BACKEND" --region="$REGION" \
    || $G compute backend-services create "$BACKEND" --region="$REGION" \
         --load-balancing-scheme=EXTERNAL --protocol=TCP \
         --health-checks="$HC" --health-checks-region="$REGION"
for ZONE in "${ZONES[@]}"; do
    IG="hw8-ig-${ZONE##*-}"
    GROUPS_NOW="$($G compute backend-services describe "$BACKEND" --region="$REGION" --format='value(backends[].group)')"
    [[ "$GROUPS_NOW" == *"/$IG"* ]] \
        || $G compute backend-services add-backend "$BACKEND" --region="$REGION" \
             --instance-group="$IG" --instance-group-zone="$ZONE"
done

echo "== load balancer IP and forwarding rule"
exists $G compute addresses describe "$IP_NAME" --region="$REGION" \
    || $G compute addresses create "$IP_NAME" --region="$REGION"
exists $G compute forwarding-rules describe "$FR" --region="$REGION" \
    || $G compute forwarding-rules create "$FR" --region="$REGION" \
         --load-balancing-scheme=EXTERNAL --ip-protocol=TCP --ports=80 \
         --address="$IP_NAME" --backend-service="$BACKEND"
LB_IP="$($G compute addresses describe "$IP_NAME" --region="$REGION" --format='value(address)')"

cat <<EOF

Done. The VMs install and start the server themselves; allow 2-3 minutes.
  Load balancer:  http://$LB_IP/files/0.html
  Backend health: gcloud compute backend-services get-health $BACKEND --region=$REGION --project=$PROJECT_ID
  Client:         python3 client.py $LB_IP
EOF
