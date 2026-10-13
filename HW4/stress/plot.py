import csv
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load(path):
    with open(path) as fh:
        return list(csv.DictReader(fh))


def main():
    runs = []
    for path in sys.argv[1:]:
        n = int(re.search(r"usage_(\d+)", path).group(1))
        rows = load(path)
        col = lambda k: [float(r[k]) for r in rows]
        runs.append((n, {k: col(k) for k in rows[0]}))
    runs.sort()

    metrics = [("cpu_pct", "CPU (% of VM)"), ("server_rss_mb", "Server memory RSS (MB)"),
               ("rx_kBps", "Network in (kB/s)"), ("tx_kBps", "Network out (kB/s)")]
    avg = lambda xs: sum(xs) / len(xs)

    print(f"{'clients':>7} " + " ".join(f"{m:>22}" for m, _ in metrics))
    for n, d in runs:
        print(f"{n:>7} " + " ".join(f"{avg(d[m]):>10.1f} (peak {max(d[m]):>6.1f})" for m, _ in metrics))

    fig, axes = plt.subplots(1, len(metrics), figsize=(16, 3.8))
    xs = [n for n, _ in runs]
    for ax, (m, title) in zip(axes, metrics):
        ax.plot(xs, [avg(d[m]) for _, d in runs], marker="o", label="average")
        ax.plot(xs, [max(d[m]) for _, d in runs], marker="s", linestyle="--", label="peak")
        ax.set_title(title)
        ax.set_xlabel("concurrent clients")
        ax.set_xticks(xs)
        ax.set_ylim(bottom=0)
        ax.grid(alpha=0.3)
    axes[0].legend()
    fig.tight_layout()
    fig.savefig("performance.png", dpi=150)
    print("wrote performance.png")


if __name__ == "__main__":
    main()