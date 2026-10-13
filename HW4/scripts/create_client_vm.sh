set -euo pipefail
PROJECT="${1:?usage: $0 <PROJECT_ID> [ZONE]}"
ZONE="${2:-us-central1-a}"
gcloud --project="$PROJECT" --quiet compute instances create hw4-client --zone="$ZONE" \
  --machine-type=e2-small --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --no-service-account --no-scopes