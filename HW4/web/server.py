import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from google.api_core.exceptions import NotFound
from google.cloud import logging as cloud_logging          # alias: do not clash with stdlib logging
from google.cloud import pubsub_v1, storage
from google.cloud.logging_v2.handlers import CloudLoggingHandler

BUCKET = os.environ.get("BUCKET", "skyvoong-cs528-hw2")
PREFIX = os.environ.get("PREFIX", "files/")
TOPIC = os.environ.get("TOPIC", "")
PORT = int(os.environ.get("PORT", "80"))
QUEUE_SIZE = int(os.environ.get("QUEUE_SIZE", "5"))
THREADED = os.environ.get("THREADED", "0") == "1"
LOG_NAME = os.environ.get("LOG_NAME", "hw4-web-server")

FORBIDDEN_COUNTRIES = {
    "north korea", "iran", "cuba", "myanmar", "iraq",
    "libya", "sudan", "zimbabwe", "syria",
}
ALIASES = {"dprk": "north korea", "burma": "myanmar"}

NOT_IMPLEMENTED_METHODS = ("PUT", "DELETE", "HEAD", "CONNECT", "OPTIONS", "TRACE", "PATCH")

# Clients are created once and shared by all requests.
_cloud_logging_client = cloud_logging.Client()
_storage = storage.Client()
_publisher = pubsub_v1.PublisherClient() if TOPIC else None
_topic_path = (_publisher.topic_path(os.environ.get("PROJECT_ID") or _storage.project, TOPIC)
               if TOPIC else "")

# Structured logging. CloudLoggingHandler sends entries from a background thread,
# so logging never blocks request handling. Fields passed in json_fields become
# the jsonPayload of the entry; the Python log level becomes its severity.
log = logging.getLogger("hw4")
log.setLevel(logging.INFO)
log.addHandler(CloudLoggingHandler(_cloud_logging_client, name=LOG_NAME))
log.addHandler(logging.StreamHandler(sys.stdout))   # also visible in journalctl

# Request counters, printed every few seconds so throughput can be read from the journal.
_counts = {}
_counts_lock = threading.Lock()


def count(status):
    with _counts_lock:
        _counts[status] = _counts.get(status, 0) + 1


def stats_loop(interval=10):
    last = {}
    while True:
        time.sleep(interval)
        with _counts_lock:
            snap = dict(_counts)
        delta = {k: snap[k] - last.get(k, 0) for k in snap}
        total = sum(delta.values())
        print(f"STATS last {interval}s: {total} requests ({total / interval:.1f}/s) "
              f"by status {delta}; totals {snap}", flush=True)
        last = snap


def normalize_country(raw):
    if not raw:
        return ""
    name = " ".join(raw.replace("_", " ").replace("-", " ").split()).lower()
    return ALIASES.get(name, name)


def object_name(file_name):
    """Only objects under PREFIX are served."""
    name = file_name.lstrip("/")
    if ".." in name.split("/"):
        return PREFIX + "__invalid__"
    return name if name.startswith(PREFIX) else PREFIX + name


def publish_forbidden(handler, country, method, file_name):
    """Send the event to the second service through Pub/Sub without blocking."""
    if not _publisher:
        log.error("TOPIC is not configured; forbidden request not published",
                  extra={"json_fields": {"event": "publish_skipped"}})
        return
    event = {
        "time": datetime.now(timezone.utc).isoformat(),
        "country": country,
        "method": method,
        "file": file_name,
        "client_ip": handler.headers.get("X-client-IP") or handler.client_address[0],
        "user_agent": handler.headers.get("User-Agent", ""),
    }
    future = _publisher.publish(_topic_path, json.dumps(event).encode("utf-8"))

    def done(f):
        try:
            f.result()
        except Exception as exc:
            log.error(f"could not publish forbidden request: {exc}",
                      extra={"json_fields": {"event": "publish_failed", "error": str(exc)}})

    future.add_done_callback(done)


class Handler(BaseHTTPRequestHandler):
    server_version = "hw4-server/1.0"

    # ---- helpers -------------------------------------------------------
    def reply(self, status, body, content_type="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        count(status)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt, *args):  # silence the per-request stderr line (slows stress tests)
        pass

    def fields(self, **extra):
        return {"json_fields": {"httpMethod": self.command, "path": self.path,
                                "clientIp": self.headers.get("X-client-IP", ""),
                                "country": self.headers.get("X-country", ""), **extra}}

    # ---- main request path ---------------------------------------------
    def serve(self, file_name):
        raw_country = self.headers.get("X-country", "")
        if normalize_country(raw_country) in FORBIDDEN_COUNTRIES:
            log.critical(f"400 Permission denied: {self.command} {file_name} from {raw_country}",
                         extra=self.fields(status=400, file=file_name))
            publish_forbidden(self, raw_country, self.command, file_name)
            return self.reply(400, f"400 Permission denied: export to {raw_country} is prohibited\n")

        if not file_name:
            log.warning(f"404 Not Found: {self.command} request with no file name",
                        extra=self.fields(status=404, file=""))
            return self.reply(404, "404 Not Found: no file name given\n")

        name = object_name(file_name)
        try:
            content = _storage.bucket(BUCKET).blob(name).download_as_bytes()
        except NotFound:
            log.warning(f"404 Not Found: {self.command} {file_name}",
                        extra=self.fields(status=404, file=file_name, object=name))
            return self.reply(404, f"404 Not Found: {file_name}\n")
        except Exception as exc:
            log.error(f"500 backend error reading {name}: {exc}",
                      extra=self.fields(status=500, file=file_name, error=str(exc)))
            return self.reply(500, "500 Internal Server Error\n")
        return self.reply(200, content, "text/html; charset=utf-8")

    def do_GET(self):
        self.serve(unquote(urlsplit(self.path).path).lstrip("/"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        file_name = ""
        if ctype == "application/json":
            try:
                data = json.loads(body)
            except ValueError:
                data = None
            if isinstance(data, dict):
                for key in ("file", "filename", "name"):
                    if data.get(key):
                        file_name = str(data[key])
                        break
            elif isinstance(data, str):
                file_name = data
        elif ctype == "application/x-www-form-urlencoded":
            # curl -d '5.html' sends the body as a form without a key: use it as-is then
            file_name = (parse_qs(body).get("file") or [body if "=" not in body else ""])[0]
        else:
            file_name = body
        self.serve(file_name.strip().lstrip("/"))

    def not_implemented(self):
        log.error(f"501 Not Implemented: {self.command} {self.path}",
                  extra=self.fields(status=501))
        self.reply(501, f"501 Not Implemented: {self.command} is not supported\n")

    do_PUT = do_DELETE = do_HEAD = do_CONNECT = do_OPTIONS = do_TRACE = do_PATCH = not_implemented


class QueueServer(HTTPServer):
    request_queue_size = QUEUE_SIZE          # listen() backlog; the stdlib default is 5


class ThreadedQueueServer(ThreadingHTTPServer):
    request_queue_size = QUEUE_SIZE
    daemon_threads = True


def main():
    cls = ThreadedQueueServer if THREADED else QueueServer
    httpd = cls(("0.0.0.0", PORT), Handler)
    threading.Thread(target=stats_loop, daemon=True).start()
    print(f"Serving gs://{BUCKET}/{PREFIX} on port {PORT} "
          f"({'threaded' if THREADED else 'single-threaded'}, backlog {QUEUE_SIZE}, "
          f"topic {_topic_path or 'none'})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
