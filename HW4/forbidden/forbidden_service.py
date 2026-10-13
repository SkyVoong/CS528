import argparse
import json
import os
import sys
from datetime import datetime, timezone

from google.cloud import pubsub_v1


def format_line(event):
    return (f"[{event.get('time', datetime.now(timezone.utc).isoformat())}] "
            f"FORBIDDEN REQUEST: export to {event.get('country', '?')} is prohibited - "
            f"{event.get('method', '?')} {event.get('file', '') or '(no file)'} "
            f"from {event.get('client_ip', '?')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default=os.environ.get("PROJECT_ID"))
    ap.add_argument("--subscription", default=os.environ.get("SUBSCRIPTION"))
    args = ap.parse_args()
    if not args.project or not args.subscription:
        sys.exit("need --project/--subscription (or PROJECT_ID/SUBSCRIPTION)")

    subscriber = pubsub_v1.SubscriberClient()      # uses the VM's service account
    path = subscriber.subscription_path(args.project, args.subscription)

    def callback(message):
        try:
            event = json.loads(message.data.decode("utf-8"))
        except ValueError:
            event = {"country": "?", "file": message.data.decode("utf-8", "replace")}
        print(f"ERROR: {format_line(event)}", flush=True)
        message.ack()

    print(f"Listening on {path}", flush=True)
    future = subscriber.subscribe(path, callback=callback,
                                  flow_control=pubsub_v1.types.FlowControl(max_messages=100))
    with subscriber:
        try:
            future.result()
        except KeyboardInterrupt:
            future.cancel()
            future.result()


if __name__ == "__main__":
    main()