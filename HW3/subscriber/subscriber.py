import argparse
import json
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import pubsub_v1, storage
from google.oauth2.credentials import Credentials

TOKEN_LIFETIME = timedelta(minutes=50) 


def impersonated_credentials(service_account):
    gcloud = shutil.which("gcloud")
    if gcloud is None:
        sys.exit("gcloud CLI not found on PATH")

    def fetch_token(request=None, scopes=None):
        result = subprocess.run(
            [gcloud, "auth", "print-access-token",
             f"--impersonate-service-account={service_account}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"gcloud could not impersonate {service_account}:\n"
                               f"{result.stderr.strip()}")
        token = result.stdout.strip().splitlines()[-1]
        expiry = datetime.now(timezone.utc).replace(tzinfo=None) + TOKEN_LIFETIME
        return token, expiry

    token, expiry = fetch_token()
    return Credentials(token=token, expiry=expiry, refresh_handler=fetch_token)


def format_line(event):
    return (f"[{event.get('time', datetime.now(timezone.utc).isoformat())}] "
            f"FORBIDDEN REQUEST: export to {event.get('country', '?')} is prohibited - "
            f"{event.get('method', '?')} {event.get('file', '') or '(no file)'} "
            f"from {event.get('client_ip', '?')}")


class BucketLog:
    def __init__(self, bucket, object_name):
        self.blob_name = object_name
        self.bucket = bucket
        self.lock = threading.Lock()

    def append(self, line, max_attempts=10):
        with self.lock:
            for _ in range(max_attempts):
                blob = self.bucket.blob(self.blob_name)
                try:
                    blob.reload()
                    generation = blob.generation
                    current = blob.download_as_text(if_generation_match=generation)
                except NotFound:
                    generation, current = 0, ""  # 0 = "only create if it doesn't exist"
                try:
                    blob.upload_from_string(current + line + "\n",
                                            content_type="text/plain",
                                            if_generation_match=generation)
                    return
                except PreconditionFailed:
                    continue  # someone else wrote in between; retry
            raise RuntimeError(f"could not append to gs://{self.bucket.name}/{self.blob_name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--subscription", required=True, help="subscription ID")
    ap.add_argument("--service-account", required=True, help="service account email")
    ap.add_argument("--bucket", default="skyvoong-cs528-hw2")
    ap.add_argument("--log-object", default="forbidden/forbidden_requests.log",
                    help="object the error lines are appended to")
    args = ap.parse_args()

    creds = impersonated_credentials(args.service_account)
    subscriber = pubsub_v1.SubscriberClient(credentials=creds)
    log = BucketLog(storage.Client(project=args.project, credentials=creds).bucket(args.bucket),
                    args.log_object)
    subscription = subscriber.subscription_path(args.project, args.subscription)

    def callback(message):
        try:
            event = json.loads(message.data.decode("utf-8"))
        except ValueError:
            event = {"country": "?", "file": message.data.decode("utf-8", "replace")}
        line = format_line(event)
        print(f"ERROR: {line}", flush=True)
        try:
            log.append(line)
        except Exception as exc:  # keep the message so Pub/Sub redelivers it
            print(f"  could not write to bucket ({exc}); will retry", file=sys.stderr, flush=True)
            message.nack()
            return
        message.ack()

    print(f"Running as {args.service_account}")
    print(f"Listening on {subscription}; appending to gs://{args.bucket}/{args.log_object}")
    print("Press Ctrl+C to stop.", flush=True)
    future = subscriber.subscribe(subscription, callback=callback,
                                  flow_control=pubsub_v1.types.FlowControl(max_messages=10))
    with subscriber:
        try:
            future.result()
        except KeyboardInterrupt:
            future.cancel()
            future.result()


if __name__ == "__main__":
    main()
