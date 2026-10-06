import json
import os
from datetime import datetime, timezone

import functions_framework 
from google.api_core.exceptions import NotFound
from google.cloud import pubsub_v1, storage

BUCKET = os.environ.get("BUCKET", "skyvoong-cs528-hw2")
PREFIX = os.environ.get("PREFIX", "files/")
TOPIC = os.environ.get("TOPIC", "")

FORBIDDEN_COUNTRIES = {
    "north korea", "iran", "cuba", "myanmar", "iraq",
    "libya", "sudan", "zimbabwe", "syria",
}

ALIASES = {"dprk": "north korea", "burma": "myanmar"}

SUPPORTED_METHODS = {"GET", "POST"}

_storage_client = None
_publisher = None

def _bucket():
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client()
    return _storage_client.bucket(BUCKET)

def _get_publisher():
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher

def log_error(severity, message, **fields):
    print(f"{severity}: {message}", flush=True)
    print(json.dumps({"severity": severity, "message": message, **fields}), flush=True)

def normalize_country(raw):
    if not raw:
        return ""
    name = " ".join(raw.replace("_", " ").replace("-", " ").split()).lower()
    return ALIASES.get(name, name)

def requested_file(request):
    if request.method == "GET":
        return request.path.lstrip("/")

    # POST: accept JSON, form data, or a plain-text body.
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        for key in ("file", "filename", "name"):
            if data.get(key):
                return str(data[key]).strip().lstrip("/")
    if isinstance(data, str):
        return data.strip().lstrip("/")
    if request.form.get("file"):
        return request.form["file"].strip().lstrip("/")
    return request.get_data(as_text=True).strip().lstrip("/")

def object_name(file_name):
    return file_name if file_name.startswith(PREFIX) else PREFIX + file_name


def report_forbidden(request, country, file_name):
    message = {
        "time": datetime.now(timezone.utc).isoformat(),
        "country": country,
        "method": request.method,
        "file": file_name,
        # The provided client sends a simulated source IP in X-client-IP.
        "client_ip": request.headers.get("X-client-IP")
                     or request.headers.get("X-Forwarded-For", request.remote_addr),
        "user_agent": request.headers.get("User-Agent", ""),
    }
    if not TOPIC:
        log_error("ERROR", "TOPIC is not configured; forbidden request not published")
        return
    future = _get_publisher().publish(TOPIC, json.dumps(message).encode("utf-8"))
    # Wait for the publish to finish: once the response is sent, the instance
    # may be throttled and a pending publish could be lost.
    future.result(timeout=30)


@functions_framework.http
def serve_file(request):
    method = request.method

    if method not in SUPPORTED_METHODS:
        log_error("WARNING", f"501 Not Implemented: method {method} on {request.path}",
                  status=501, httpMethod=method, path=request.path)
        return (f"501 Not Implemented: {method} is not supported\n", 501,
                {"Content-Type": "text/plain"})

    file_name = requested_file(request)
    raw_country = request.headers.get("X-country", "")
    country = normalize_country(raw_country)

    if country in FORBIDDEN_COUNTRIES:
        log_error("WARNING", f"400 Permission denied: {method} {file_name} from {raw_country}",
                  status=400, httpMethod=method, file=file_name, country=raw_country,
                  clientIp=request.headers.get("X-client-IP", ""))
        report_forbidden(request, raw_country, file_name)
        return (f"400 Permission denied: export to {raw_country} is prohibited\n", 400,
                {"Content-Type": "text/plain"})

    if not file_name:
        log_error("WARNING", f"404 Not Found: {method} request with no file name",
                  status=404, httpMethod=method, file="")
        return ("404 Not Found: no file name given\n", 404, {"Content-Type": "text/plain"})

    name = object_name(file_name)
    try:
        content = _bucket().blob(name).download_as_bytes()
    except NotFound:
        log_error("WARNING", f"404 Not Found: {method} {file_name}",
                  status=404, httpMethod=method, file=file_name, object=name)
        return (f"404 Not Found: {file_name}\n", 404, {"Content-Type": "text/plain"})

    return (content, 200, {"Content-Type": "text/html; charset=utf-8"})
