import os

import functions_framework
import google.auth
from google.auth.transport.requests import AuthorizedSession

PROJECT_ID = os.environ["PROJECT_ID"]
SQL_INSTANCE = os.environ["SQL_INSTANCE"]
API = f"https://sqladmin.googleapis.com/v1/projects/{PROJECT_ID}/instances/{SQL_INSTANCE}"


@functions_framework.http
def stop_sql_if_running(request):
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(creds)

    resp = session.get(API)
    resp.raise_for_status()
    instance = resp.json()
    policy = instance["settings"]["activationPolicy"]
    state = instance.get("state", "?")

    if policy == "ALWAYS":
        patch = session.patch(API, json={"settings": {"activationPolicy": "NEVER"}})
        patch.raise_for_status()
        msg = f"{SQL_INSTANCE} was running (state {state}); stop requested (operation {patch.json().get('name')})"
    else:
        msg = f"{SQL_INSTANCE} is not running (activation policy {policy}, state {state}); nothing to do"
    print(msg, flush=True)
    return msg, 200
