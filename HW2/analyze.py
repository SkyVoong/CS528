import argparse
import os
import sys
import time

from graph import Graph, parse_links, summarize, pagerank, top_k, best_closeness



def load_from_dir(path):
    pages = {}
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if os.path.isfile(full):
            with open(full, encoding="utf-8", errors="replace") as f:
                pages[name] = parse_links(f.read())
    return pages


def load_from_gcs(bucket_name, prefix):
    import requests
    from concurrent.futures import ThreadPoolExecutor

    session = requests.Session()

    names = []
    page_token = None
    while True:
        params = {"prefix": prefix, "maxResults": 1000}
        if page_token:
            params["pageToken"] = page_token
        r = session.get(f"https://storage.googleapis.com/storage/v1/b/{bucket_name}/o",
                         params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        for item in data.get("items", []):
            if not item["name"].endswith("/"):
                names.append(item["name"])
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    def fetch(name):
        r = session.get(f"https://storage.googleapis.com/{bucket_name}/{name}", timeout=30)
        r.raise_for_status()
        return name, r.text

    pages = {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        for name, text in ex.map(fetch, names):
            pages[name.split("/")[-1]] = parse_links(text)
    return pages


def fmt(stats):
    q = ", ".join(f"{x:.2f}" for x in stats["quintiles"])
    return (f"avg={stats['avg']:.2f} median={stats['median']} "
            f"max={stats['max']} min={stats['min']} quintiles(20/40/60/80%)=[{q}]")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--bucket", help="GCS bucket name (no gs:// prefix)")
    src.add_argument("--local-dir", help="read pages from a local directory")
    ap.add_argument("--prefix", default="", help="folder inside the bucket, e.g. 'files/'")
    args = ap.parse_args()

    t0 = time.perf_counter()
    pages = load_from_gcs(args.bucket, args.prefix) if args.bucket else load_from_dir(args.local_dir)
    if not pages:
        sys.exit("No files found.")
    t1 = time.perf_counter()

    g = Graph.from_pages(pages)
    out_deg, in_deg = g.out_degrees(), g.in_degrees()
    print(f"Pages: {g.n}   Edges: {sum(out_deg)}")
    print("Outgoing links:", fmt(summarize(out_deg)))
    print("Incoming links:", fmt(summarize(in_deg)))
    t2 = time.perf_counter()

    pr, iters = pagerank(g)
    print(f"\nPageRank converged in {iters} iterations (sum of PR = {sum(pr):.6f})")
    print("Top 5 pages by PageRank:")
    for rank, i in enumerate(top_k(pr, 5), 1):
        print(f"  {rank}. {g.names[i]:<15} PR={pr[i]:.8f}")
    t3 = time.perf_counter()

    best, score, _ = best_closeness(g)
    print(f"\nBest closeness centrality: {g.names[best]}  closeness={score:.6f}")
    t4 = time.perf_counter()

    print(f"\nTiming (s): read={t1-t0:.2f} build+stats={t2-t1:.2f} "
          f"pagerank={t3-t2:.2f} closeness={t4-t3:.2f} total={t4-t0:.2f}")


if __name__ == "__main__":
    main()