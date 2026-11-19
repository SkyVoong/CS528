import argparse
import http.client
import random
import time
from collections import Counter
from datetime import datetime

COUNTRIES = [
    "United States", "Canada", "Mexico", "Brazil", "United Kingdom", "France", "Germany",
    "Italy", "Spain", "India", "China", "Japan", "South Korea", "Australia", "Nigeria",
    "Kenya", "Egypt", "Vietnam", "Philippines", "Chile", "Iran", "Cuba", "North Korea",
]
GENDERS = ["Male", "Female"]
AGES = ["0-16", "17-25", "26-35", "36-45", "46-55", "56-65", "66-75", "76+"]
INCOMES = ["0-10k", "10k-20k", "20k-40k", "40k-60k", "60k-100k", "100k-150k", "150k-250k", "250k+"]


def now():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def make_request(rng, max_index):
    age = rng.randrange(len(AGES))
    headers = {
        "X-country": rng.choice(COUNTRIES),
        "X-client-IP": ".".join(str(rng.randrange(256)) for _ in range(4)),
        "X-gender": rng.choice(GENDERS),
        "X-age": AGES[age],
        "X-income": INCOMES[(age + rng.choice((-1, 0, 1))) % len(INCOMES)],
        "X-time": datetime.now().strftime("%Y-%m-%d ") + f"{rng.randrange(24):02d}:00:00",
        "Connection": "close",
    }
    return f"/files/{rng.randint(0, max_index)}.html", headers


def send(host, port, path, headers, timeout):
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        resp.read()
        return resp.status, resp.getheader("X-Zone", "none")
    finally:
        conn.close()


def summarize(zones, statuses, errors, error_times):
    total = sum(zones.values()) + errors
    print(f"\n===== summary: {total} requests")
    served = sum(zones.values())
    for zone, n in sorted(zones.items()):
        print(f"  {zone}: {n} ({n / served:.1%} of served requests)")
    print(f"  errors: {errors}")
    print(f"  status codes: {dict(sorted(statuses.items()))}")
    if error_times:
        print(f"  first error: {error_times[0]}   last error: {error_times[-1]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("-p", "--port", type=int, default=80)
    ap.add_argument("-n", "--num-requests", type=int, default=0)
    ap.add_argument("-i", "--max-index", type=int, default=11999)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("-t", "--timeout", type=float, default=2.0)
    ap.add_argument("-r", "--seed", type=int, default=None)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    zones, statuses = Counter(), Counter()
    errors, error_times = 0, []
    start = time.monotonic()
    i = 0
    try:
        while args.num_requests == 0 or i < args.num_requests:
            path, headers = make_request(rng, args.max_index)
            t0 = time.monotonic()
            stamp = now()
            try:
                status, zone = send(args.host, args.port, path, headers, args.timeout)
                ms = (time.monotonic() - t0) * 1000
                zones[zone] += 1
                statuses[status] += 1
                print(f"{stamp}  {status}  zone={zone:<15} {ms:6.0f} ms  {path}", flush=True)
            except Exception as exc:
                errors += 1
                error_times.append(stamp)
                print(f"{stamp}  ERROR  {type(exc).__name__}: {exc}", flush=True)
            i += 1
            time.sleep(max(0.0, start + i * args.interval - time.monotonic()))
    except KeyboardInterrupt:
        pass
    summarize(zones, statuses, errors, error_times)


if __name__ == "__main__":
    main()
