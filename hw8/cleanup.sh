#!/bin/bash
set -uo pipefail

PROJECT_ID="cs320-508117"
REGION="us-central1"
G="gcloud --project=$PROJECT_ID --quiet"
exists() { "$@" >/dev/null 2>&1; }

echo "== load balancer"
exists $G compute forwarding-rules describe hw8-lb --region=$REGION \
    && $G compute forwarding-rules delete hw8-lb --region=$REGION
exists $G compute backend-services describe hw8-backend --region=$REGION \
    && $G compute backend-services delete hw8-backend --region=$REGION
exists $G compute health-checks describe hw8-health-check --region=$REGION \
    && $G compute health-checks delete hw8-health-check --region=$REGION
exists $G compute addresses describe hw8-lb-ip --region=$REGION \
    && $G compute addresses delete hw8-lb-ip --region=$REGION

echo "== instance groups and VMs"
for Z in a b; do
    exists $G compute instance-groups unmanaged describe hw8-ig-$Z --zone=$REGION-$Z \
        && $G compute instance-groups unmanaged delete hw8-ig-$Z --zone=$REGION-$Z
    exists $G compute instances describe hw8-web-$Z --zone=$REGION-$Z \
        && $G compute instances delete hw8-web-$Z --zone=$REGION-$Z
done
exists $G compute instances describe hw8-forbidden --zone=$REGION-a \
    && $G compute instances delete hw8-forbidden --zone=$REGION-a

echo "== firewall and Pub/Sub"
exists $G compute firewall-rules describe hw8-allow-http && $G compute firewall-rules delete hw8-allow-http
exists $G pubsub subscriptions describe hw8-forbidden-requests-sub \
    && $G pubsub subscriptions delete hw8-forbidden-requests-sub
exists $G pubsub topics describe hw8-forbidden-requests && $G pubsub topics delete hw8-forbidden-requests

echo "Done."
