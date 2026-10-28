import functools
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import sqlalchemy
from google.api_core.exceptions import NotFound
from google.cloud import logging as cloud_logging         
from google.cloud import pubsub_v1, storage
from google.cloud.logging_v2.handlers import CloudLoggingHandler
from google.cloud.sql.connector import Connector

PROJECT_ID = os.environ.get("PROJECT_ID", "")
REGION = os.environ.get("REGION", "us-central1")
SQL_INSTANCE = os.environ.get("SQL_INSTANCE", "hw5-sql")
DB_NAME = os.environ.get("DB_NAME", "hw5db")
DB_USER = os.environ.get("DB_USER", "hw5user")
DB_PASS = os.environ.get("DB_PASS", "")
BUCKET = os.environ.get("BUCKET", "skyvoong-cs528-hw2")
PREFIX = os.environ.get("PREFIX", "files/")
TOPIC = os.environ.get("TOPIC", "")
PORT = int(os.environ.get("PORT", "80"))
LOG_NAME = os.environ.get("LOG_NAME", "hw5-web-server")

FORBIDDEN_COUNTRIES = {
    "north korea", "iran", "cuba", "myanmar", "iraq",
    "libya", "sudan", "zimbabwe", "syria",
}
ALIASES = {"dprk": "north korea", "burma": "myanmar"}

# ---------------------------------------------------------------- clients
_storage = storage.Client()
_bucket = _storage.bucket(BUCKET)
_publisher = pubsub_v1.PublisherClient() if TOPIC else None
_topic_path = _publisher.topic_path(PROJECT_ID or _storage.project, TOPIC) if TOPIC else ""

log = logging.getLogger("hw5")
log.setLevel(logging.INFO)
log.addHandler(CloudLoggingHandler(cloud_logging.Client(), name=LOG_NAME))
log.addHandler(logging.StreamHandler(sys.stdout))

_connector = Connector()


def _getconn():
    return _connector.connect(f"{PROJECT_ID}:{REGION}:{SQL_INSTANCE}", "pymysql",
                              user=DB_USER, password=DB_PASS, db=DB_NAME)


engine = sqlalchemy.create_engine("mysql+pymysql://", creator=_getconn,
                                  pool_size=4, max_overflow=4,
                                  pool_pre_ping=True, pool_recycle=1800)

INSERT_REQUEST = sqlalchemy.text(
    "INSERT INTO requests (country, client_ip, gender, age, income, is_banned, "
    "time_of_day, requested_file) VALUES (:country, :client_ip, :gender, :age, "
    ":income, :is_banned, :time_of_day, :requested_file)")
INSERT_FAILED = sqlalchemy.text(
    "INSERT INTO failed_requests (request_time, requested_file, error_code) "
    "VALUES (:request_time, :requested_file, :error_code)")


class Timings:
    """Thread-safe count / total / min / max (nanoseconds) per stage."""

    def __init__(self):
        self.lock = threading.Lock()
        self.data = {}

    def add(self, name, ns):
        with self.lock:
            d = self.data.setdefault(name, [0, 0, None, 0])
            d[0] += 1
            d[1] += ns
            d[2] = ns if d[2] is None else min(d[2], ns)
            d[3] = max(d[3], ns)

    def report(self):
        with self.lock:
            snap = {k: list(v) for k, v in self.data.items()}
        parts = []
        for name in ("extract_request_info", "read_file", "send_response_to_client", "insert_into_db"):
            if name in snap:
                n, total, lo, hi = snap[name]
                parts.append(f"{name}: n={n} avg={total / n / 1e6:.3f}ms "
                             f"min={lo / 1e6:.3f}ms max={hi / 1e6:.3f}ms total={total / 1e9:.1f}s")
        return "TIMING " + " | ".join(parts)


TIMINGS = Timings()


def timed(name):
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            start = time.perf_counter_ns()
            try:
                return fn(*args, **kwargs)
            finally:
                TIMINGS.add(name, time.perf_counter_ns() - start)
        return inner
    return wrap


_counts = {}
_counts_lock = threading.Lock()


def count(status):
    with _counts_lock:
        _counts[status] = _counts.get(status, 0) + 1


def stats_loop(interval=30):
    last = {}
    while True:
        time.sleep(interval)
        with _counts_lock:
            snap = dict(_counts)
        delta = {k: snap[k] - last.get(k, 0) for k in snap}
        total = sum(delta.values())
        print(f"STATS last {interval}s: {total} requests ({total / interval:.1f}/s) "
              f"by status {delta}; totals {snap}", flush=True)
        print(TIMINGS.report(), flush=True)
        last = snap


# ---------------------------------------------------------------- helpers
def normalize_country(raw):
    if not raw:
        return ""
    name = " ".join(raw.replace("_", " ").replace("-", " ").split()).lower()
    return ALIASES.get(name, name)


def parse_time(raw):
    """X-time header from the client, e.g. '2026-10-25 13:00:00'. Falls back to now (UTC)."""
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(raw, fmt).time()
            return datetime.combine(datetime.now(timezone.utc).date(), t)
        except ValueError:
            pass
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def object_name(file_name):
    """Only objects under PREFIX are served."""
    name = file_name.lstrip("/")
    if ".." in name.split("/"):
        return PREFIX + "__invalid__"
    return name if name.startswith(PREFIX) else PREFIX + name


def _clip(value, size):
    value = (value or "").strip()
    return value[:size] if value else None


def publish_forbidden(info):
    if not _publisher:
        return
    event = {"time": datetime.now(timezone.utc).isoformat(), "country": info["country"],
             "method": info["method"], "file": info["file_name"],
             "client_ip": info["client_ip"], "user_agent": info["user_agent"]}
    future = _publisher.publish(_topic_path, json.dumps(event).encode("utf-8"))

    def done(f):
        try:
            f.result()
        except Exception as exc:
            log.error(f"could not publish forbidden request: {exc}",
                      extra={"json_fields": {"event": "publish_failed", "error": str(exc)}})

    future.add_done_callback(done)


# ---------------------------------------------------------------- the four timed stages
@timed("extract_request_info")
def extract_request_info(handler):
    """Parse the request line, headers and (for POST) the body."""
    h = handler.headers
    method = handler.command
    file_name = ""
    if method == "GET":
        file_name = unquote(urlsplit(handler.path).path)
    elif method == "POST":
        length = int(h.get("Content-Length") or 0)
        body = handler.rfile.read(length).decode("utf-8", "replace") if length else ""
        ctype = (h.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype == "application/json":
            try:
                data = json.loads(body)
            except ValueError:
                data = None
            if isinstance(data, dict):
                file_name = next((str(data[k]) for k in ("file", "filename", "name") if data.get(k)), "")
            elif isinstance(data, str):
                file_name = data
        elif ctype == "application/x-www-form-urlencoded":
            file_name = (parse_qs(body).get("file") or [body if "=" not in body else ""])[0]
        else:
            file_name = body
    else:
        file_name = unquote(urlsplit(handler.path).path)
    country = h.get("X-country", "")
    return {
        "method": method,
        "path": handler.path,
        "file_name": file_name.strip().lstrip("/"),
        "country": country,
        "client_ip": h.get("X-client-IP") or handler.client_address[0],
        "gender": h.get("X-gender", ""),
        "age": h.get("X-age", ""),
        "income": h.get("X-income", ""),
        "time": parse_time(h.get("X-time")),
        "is_banned": normalize_country(country) in FORBIDDEN_COUNTRIES,
        "user_agent": h.get("User-Agent", ""),
    }


@timed("read_file")
def read_file(name):
    """Download one object from Cloud Storage (raises NotFound)."""
    return _bucket.blob(name).download_as_bytes()


@timed("send_response_to_client")
def send_response_to_client(handler, status, body, content_type="text/plain; charset=utf-8"):
    if isinstance(body, str):
        body = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)
    handler.wfile.flush()


@timed("insert_into_db")
def insert_into_db(info, status):
    """One row in `requests` for every request; plus one in `failed_requests` if not 200."""
    row = {
        "country": _clip(info["country"], 64),
        "client_ip": _clip(info["client_ip"], 45),
        "gender": _clip(info["gender"], 32),
        "age": _clip(info["age"], 32),
        "income": _clip(info["income"], 32),
        "is_banned": bool(info["is_banned"]),
        "time_of_day": info["time"],
        "requested_file": _clip(info["file_name"], 255),
    }
    with engine.begin() as conn:                     
        conn.execute(INSERT_REQUEST, row)
        if status != 200:
            conn.execute(INSERT_FAILED, {"request_time": info["time"],
                                         "requested_file": row["requested_file"],
                                         "error_code": status})


# ---------------------------------------------------------------- request handling
class Handler(BaseHTTPRequestHandler):
    server_version = "hw5-server/1.0"

    def log_message(self, fmt, *args):  
        pass

    def fields(self, info, **extra):
        return {"json_fields": {"httpMethod": info["method"], "path": info["path"],
                                "clientIp": info["client_ip"], "country": info["country"],
                                **extra}}

    def handle_request(self):
        info = extract_request_info(self)
        status, body, ctype = self.decide(info)
        count(status)
        send_response_to_client(self, status, body, ctype)
        try:
            insert_into_db(info, status)           
        except Exception as exc:
            log.error(f"database insert failed: {exc}",
                      extra=self.fields(info, event="db_insert_failed", error=str(exc)))

    def decide(self, info):
        text = "text/plain; charset=utf-8"
        if info["method"] not in ("GET", "POST"):
            log.error(f"501 Not Implemented: {info['method']} {info['path']}",
                      extra=self.fields(info, status=501))
            return 501, f"501 Not Implemented: {info['method']} is not supported\n", text

        if info["is_banned"]:
            log.critical(f"400 Permission denied: {info['method']} {info['file_name']} from {info['country']}",
                         extra=self.fields(info, status=400, file=info["file_name"]))
            publish_forbidden(info)
            return 400, f"400 Permission denied: export to {info['country']} is prohibited\n", text

        if not info["file_name"]:
            log.warning(f"404 Not Found: {info['method']} request with no file name",
                        extra=self.fields(info, status=404, file=""))
            return 404, "404 Not Found: no file name given\n", text

        name = object_name(info["file_name"])
        try:
            return 200, read_file(name), "text/html; charset=utf-8"
        except NotFound:
            log.warning(f"404 Not Found: {info['method']} {info['file_name']}",
                        extra=self.fields(info, status=404, file=info["file_name"], object=name))
            return 404, f"404 Not Found: {info['file_name']}\n", text
        except Exception as exc:
            log.error(f"500 backend error reading {name}: {exc}",
                      extra=self.fields(info, status=500, file=info["file_name"], error=str(exc)))
            return 500, "500 Internal Server Error\n", text

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_CONNECT = do_OPTIONS = do_TRACE = do_PATCH = \
        handle_request


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64


def main():
    httpd = Server(("0.0.0.0", PORT), Handler)
    threading.Thread(target=stats_loop, daemon=True).start()
    print(f"Serving gs://{BUCKET}/{PREFIX} on port {PORT}; database "
          f"{PROJECT_ID}:{REGION}:{SQL_INSTANCE}/{DB_NAME}; topic {_topic_path or 'none'}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print(TIMINGS.report(), flush=True)
        httpd.server_close()
        _connector.close()


if __name__ == "__main__":
    main()
