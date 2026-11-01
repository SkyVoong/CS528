import csv
import io
import os
from collections import Counter

from google.cloud import storage
from google.cloud.sql.connector import Connector
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder
from sklearn.tree import DecisionTreeClassifier

ENV = os.environ
SQL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "normalize.sql")
SEED = 42


def connect():
    connector = Connector()
    conn = connector.connect(f"{ENV['PROJECT_ID']}:{ENV['REGION']}:{ENV['SQL_INSTANCE']}", "pymysql",
                             user=ENV["DB_USER"], password=ENV["DB_PASS"], db=ENV["DB_NAME"])
    return connector, conn


def sql_sections(path):
    sections, name, lines = {}, None, []
    for line in open(path):
        if line.startswith("-- ====="):
            if name:
                sections[name] = lines
            name, lines = line.strip("-= \n"), []
        elif name and not line.lstrip().startswith("--"):
            lines.append(line)
    sections[name] = lines
    return {k: [s.strip() for s in "".join(v).split(";") if s.strip()] for k, v in sections.items()}


def migrate(conn, log):
    sql = sql_sections(SQL_FILE)
    with conn.cursor() as cur:
        for stmt in sql["SCHEMA"]:
            cur.execute(stmt)
        cur.execute("SELECT COUNT(*) FROM requests_3nf")
        if cur.fetchone()[0] == 0:
            for stmt in sql["MIGRATE"]:
                cur.execute(stmt)
            conn.commit()
            log("Migration: copied the HW5 data into the 3NF tables.")
        else:
            log("Migration: 3NF tables already filled, skipped.")
        counts = {}
        for t in ("requests", "countries", "client_ips", "requests_3nf"):
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            counts[t] = cur.fetchone()[0]
        cur.execute(sql["CHECKS"][0])
        conflicts = cur.fetchone()[0]
        cur.execute(sql["CHECKS"][1])
        matching = cur.fetchone()[0]
    log(f"Row counts: {counts}")
    log(f"IPs seen with more than one country: {conflicts}")
    log(f"3NF rows that rebuild the old country/is_banned exactly: {matching} of {counts['requests']}")


def load(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT r.client_ip, c.country_name, r.gender, r.age, r.income
            FROM requests_3nf r
            JOIN client_ips i ON i.client_ip = r.client_ip
            JOIN countries  c ON c.country_id = i.country_id
            WHERE r.gender IS NOT NULL AND r.age IS NOT NULL AND r.income IS NOT NULL""")
        return cur.fetchall()


def ip_features(ip):
    o = ip.split(".")
    return [int(o[0]), int(o[1]), int(o[2])]


def model_ip_to_country(rows, log):
    X = [ip_features(r[0]) for r in rows]
    y = [r[1] for r in rows]
    ips = [r[0] for r in rows]
    X_tr, X_te, y_tr, y_te, _, ip_te = train_test_split(X, y, ips, test_size=0.2, random_state=SEED)
    model = DecisionTreeClassifier(random_state=SEED).fit(X_tr, y_tr)
    pred = model.predict(X_te)
    acc = accuracy_score(y_te, pred)
    log(f"Model 1 (IP -> country): decision tree on the first 3 IP octets, "
        f"{len(X_tr)} train / {len(X_te)} test rows, {model.get_n_leaves()} leaves")
    log(f"Model 1 test accuracy: {acc:.4%}")
    out = [["client_ip", "actual_country", "predicted_country", "correct"]]
    out += [[i, a, p, int(a == p)] for i, a, p in zip(ip_te, y_te, pred)]
    return acc, out


def model_income(rows, log):
    feats = [[r[3], r[2]] for r in rows]
    y = [r[4] for r in rows]
    enc = OneHotEncoder(handle_unknown="ignore")
    X = enc.fit_transform(feats)
    X_tr, X_te, y_tr, y_te, f_tr, f_te = train_test_split(X, y, feats, test_size=0.2, random_state=SEED)
    model = DecisionTreeClassifier(max_depth=6, random_state=SEED).fit(X_tr, y_tr)
    pred = model.predict(X_te)
    acc = accuracy_score(y_te, pred)
    baseline = Counter(y_te).most_common(1)[0][1] / len(y_te)
    log(f"Model 2 (age + gender -> income): decision tree (max depth 6) on one-hot age and gender, "
        f"{len(y_tr)} train / {len(y_te)} test rows")
    log(f"Model 2 test accuracy: {acc:.4%}   (always guessing the most common income: {baseline:.4%})")
    accs = []
    for s in (1, 7, 123, 2024, 31337):
        a_tr, a_te, b_tr, b_te = train_test_split(X, y, test_size=0.2, random_state=s)
        accs.append(accuracy_score(b_te, DecisionTreeClassifier(max_depth=6, random_state=s)
                                   .fit(a_tr, b_tr).predict(a_te)))
    log("Model 2 accuracy with other split seeds: " + ", ".join(f"{a:.2%}" for a in accs))
    log("Why about 33% is the ceiling: the client picks income as the age group's index plus -1, 0 or +1 "
        "(each 1/3 likely, wrapping around), independent of everything else. Knowing the age narrows "
        "income to 3 equally likely values, so no model can beat about 1/3 on average; scores above "
        "33.3% come from the random test split.")
    learned = {}
    for (age, gender), p in zip(f_te, pred):
        learned.setdefault(age, Counter())[p] += 1
    for age in sorted(learned, key=lambda a: int(a.split("-")[0].rstrip("+"))):
        log(f"  age {age:>6} -> predicted income {learned[age].most_common(1)[0][0]}")

    out = [["age", "gender", "actual_income", "predicted_income", "correct"]]
    out += [[f[0], f[1], a, p, int(a == p)] for f, a, p in zip(f_te, y_te, pred)]
    return acc, out


def upload(bucket, name, text):
    bucket.blob(f"{ENV['OUT_PREFIX']}/{name}").upload_from_string(text, content_type="text/plain")


def to_csv(rows):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue()


def main():
    lines = []

    def log(msg):
        print(msg, flush=True)
        lines.append(msg)

    bucket = storage.Client().bucket(ENV["BUCKET"])
    connector, conn = connect()
    try:
        migrate(conn, log)
        rows = load(conn)
    finally:
        conn.close()
        connector.close()
    log(f"Loaded {len(rows)} requests with country, gender, age and income from the 3NF tables.")

    _, out1 = model_ip_to_country(rows, log)
    _, out2 = model_income(rows, log)

    upload(bucket, "ip_country_test_predictions.csv", to_csv(out1))
    upload(bucket, "income_test_predictions.csv", to_csv(out2))
    upload(bucket, "summary.txt", "\n".join(lines) + "\n")
    print(f"Wrote results to gs://{ENV['BUCKET']}/{ENV['OUT_PREFIX']}/", flush=True)


if __name__ == "__main__":
    main()
