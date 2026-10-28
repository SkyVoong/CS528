import os
import subprocess

from google.cloud.sql.connector import Connector
from google.oauth2.credentials import Credentials

PROJECT_ID = "cs320-508117"
REGION = "us-central1"
SQL_INSTANCE = "hw5-sql"
DB_NAME = "hw5db"
DB_USER = "hw5user"
DB_SECRET = "hw5-db-pass"

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS requests (
        request_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        country         VARCHAR(64)  NULL,
        client_ip       VARCHAR(45)  NULL,
        gender          VARCHAR(32)  NULL,
        age             VARCHAR(32)  NULL,
        income          VARCHAR(32)  NULL,
        is_banned       BOOLEAN      NOT NULL DEFAULT FALSE,
        time_of_day     DATETIME     NOT NULL,
        requested_file  VARCHAR(255) NULL,
        PRIMARY KEY (request_id)
    )""",
    """CREATE TABLE IF NOT EXISTS failed_requests (
        failure_id      BIGINT UNSIGNED   NOT NULL AUTO_INCREMENT,
        request_time    DATETIME          NOT NULL,
        requested_file  VARCHAR(255)      NULL,
        error_code      SMALLINT UNSIGNED NOT NULL,
        PRIMARY KEY (failure_id)
    )""",
]


def gcloud(*args):
    return subprocess.run(["gcloud", *args], check=True, capture_output=True, text=True).stdout.strip()


def connect():
    """Returns (connector, connection). Close both when done."""
    password = os.environ.get("DB_PASS") or gcloud(
        "secrets", "versions", "access", "latest", f"--secret={DB_SECRET}", f"--project={PROJECT_ID}")
    connector = Connector(credentials=Credentials(token=gcloud("auth", "print-access-token")))
    conn = connector.connect(f"{PROJECT_ID}:{REGION}:{SQL_INSTANCE}", "pymysql",
                             user=DB_USER, password=password, db=DB_NAME, autocommit=True)
    return connector, conn


def main():
    connector, conn = connect()
    try:
        with conn.cursor() as cur:
            for statement in SCHEMA:
                cur.execute(statement)
            cur.execute("SHOW TABLES")
            print("tables:", ", ".join(row[0] for row in cur.fetchall()))
    finally:
        conn.close()
        connector.close()


if __name__ == "__main__":
    main()
