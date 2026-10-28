import sys

from setup_schema import connect


def table(cur, sql, title):
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    print(f"\n{title}")
    widths = [max(len(str(c)), *(len(str(r[i])) for r in rows)) if rows else len(str(c))
              for i, c in enumerate(cols)]
    print("  ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
    if not rows:
        print("(no rows)")


def show(cur, n):
    for t in ("requests", "failed_requests"):
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"{t}: {cur.fetchone()[0]} rows")
    table(cur, f"SELECT * FROM requests ORDER BY request_id DESC LIMIT {n}", f"requests (last {n})")
    table(cur, f"SELECT * FROM failed_requests ORDER BY failure_id DESC LIMIT {n}",
          f"failed_requests (last {n})")


def stats(cur):
    cur.execute("SELECT COUNT(*) FROM requests")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM failed_requests")
    failed = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM requests WHERE is_banned")
    banned = cur.fetchone()[0]
    print(f"Total requests:            {total}")
    print(f"Successful (200):          {total - failed}")
    print(f"Unsuccessful (non-200):    {failed}")
    print(f"From banned countries:     {banned}")
    table(cur, "SELECT error_code, COUNT(*) AS requests FROM failed_requests "
               "GROUP BY error_code ORDER BY requests DESC", "Unsuccessful by error code")
    table(cur, "SELECT gender, COUNT(*) AS requests FROM requests "
               "GROUP BY gender ORDER BY requests DESC", "Requests by gender")
    table(cur, "SELECT country, COUNT(*) AS requests FROM requests "
               "GROUP BY country ORDER BY requests DESC LIMIT 5", "Top 5 countries")
    table(cur, "SELECT age, COUNT(*) AS requests FROM requests "
               "GROUP BY age ORDER BY requests DESC", "Requests by age group (most first)")
    table(cur, "SELECT income, COUNT(*) AS requests FROM requests "
               "GROUP BY income ORDER BY requests DESC", "Requests by income group (most first)")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    connector, conn = connect()
    try:
        with conn.cursor() as cur:
            if cmd == "show":
                show(cur, int(sys.argv[2]) if len(sys.argv) > 2 else 5)
            elif cmd == "stats":
                stats(cur)
            elif cmd == "reset":
                cur.execute("TRUNCATE TABLE requests")
                cur.execute("TRUNCATE TABLE failed_requests")
                print("both tables emptied")
            else:
                sys.exit(__doc__)
    finally:
        conn.close()
        connector.close()


if __name__ == "__main__":
    main()
