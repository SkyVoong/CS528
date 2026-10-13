"""Samples resource usage once per second and writes a CSV. Run on the web VM.

    python3 monitor.py <label> [seconds=180]      ->  usage_<label>.csv

Columns: t (s), cpu_pct (whole VM), mem_used_mb (whole VM), server_rss_mb
(the python server process), rx_kBps / tx_kBps (all non-loopback interfaces).
Standard library only (/proc).
"""
import csv
import sys
import time


def cpu_times():
    f = open("/proc/stat").readline().split()[1:]
    vals = list(map(int, f))
    # fields: user nice system idle iowait irq softirq steal guest guest_nice
    # guest/guest_nice are already included in user/nice, so only the first 8 are summed.
    vals = vals[:8]
    idle = vals[3] + vals[4]                 # idle + iowait
    steal = vals[7] if len(vals) > 7 else 0  # time the hypervisor gave to other VMs
    return sum(vals) - steal, idle


def mem_used_mb():
    info = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":")
        info[k] = int(v.split()[0])
    return (info["MemTotal"] - info["MemAvailable"]) / 1024


def net_bytes():
    rx = tx = 0
    for line in list(open("/proc/net/dev"))[2:]:
        name, data = line.split(":")
        if name.strip() == "lo":
            continue
        cols = data.split()
        rx += int(cols[0])
        tx += int(cols[8])
    return rx, tx


def server_rss_mb():
    import os
    total = 0
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmd = open(f"/proc/{pid}/cmdline").read()
            if "server.py" in cmd:
                for line in open(f"/proc/{pid}/status"):
                    if line.startswith("VmRSS"):
                        total += int(line.split()[1]) / 1024
        except OSError:
            pass
    return total


def main():
    label = sys.argv[1]
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 180
    out = f"usage_{label}.csv"
    t_prev, i_prev = cpu_times()
    rx_prev, tx_prev = net_bytes()
    start = time.time()
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "cpu_pct", "mem_used_mb", "server_rss_mb", "rx_kBps", "tx_kBps"])
        for s in range(1, seconds + 1):
            time.sleep(max(0, start + s - time.time()))
            t, i = cpu_times()
            rx, tx = net_bytes()
            dt = t - t_prev
            cpu = 100.0 * (1 - (i - i_prev) / dt) if dt else 0.0
            w.writerow([s, round(cpu, 1), round(mem_used_mb(), 1), round(server_rss_mb(), 1),
                        round((rx - rx_prev) / 1024, 1), round((tx - tx_prev) / 1024, 1)])
            fh.flush()
            t_prev, i_prev, rx_prev, tx_prev = t, i, rx, tx
    print("wrote", out)


if __name__ == "__main__":
    main()