#!/usr/bin/env python3
"""
Userspace collector for the Self-Healing OS data pipeline (Step 1).

Runs the four bpftrace subsystem scripts (scheduling, memory, block I/O, IRQ)
with JSON output, streams their per-interval snapshots, parses them into
structured metric rows, and persists them into SQLite immediately.

Architecture (matches the plan):
    eBPF probes -> in-kernel BPF-map aggregation -> interval snapshot
    -> bpftrace -f json -> this collector -> SQLite `metrics` time series

Snapshot framing: each bpftrace script brackets one interval snapshot with
    __snapshot__=1 ... __snapshot_end__=1
so that bpftrace's final END map dump (emitted on exit) is never mistaken for
a real window. Only rows arriving inside a frame are persisted, and only map
names listed in MAP_METRICS are treated as data (internal/ts maps are ignored).

Usage:
    sudo python3 collector.py [--db PATH] [--interval SEC] [--duration SEC]
                              [--scripts-dir DIR] [--verbose]
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

DEFAULT_SCRIPT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bpftrace"
)
SUBSYSTEMS = ["sched", "memory", "block", "irq"]

# bpftrace map name -> base metric name for per-entity breakdown rows.
MAP_METRICS = {
    "@sw_per_comm": "sched_switches_per_comm",
    "@pf_per_comm": "mem_page_faults_per_comm",
    "@oom_per_comm": "mem_oom_kills_per_comm",
    "@blk_per_dev": "blk_io_ops_per_dev",
    "@irq_per_line": "irq_handled_per_line",
}

START_MARKER = "__snapshot__"
END_MARKER = "__snapshot_end__"

log = logging.getLogger("collector")


def decode_dev_t(dev):
    """dev_t number -> 'major:minor' (Linux 32-bit dev encoding)."""
    major = (dev >> 20) & 0xFFF
    minor = dev & 0xFFFFF
    return f"{major}:{minor}"


def _build_dev_name_map():
    """Map 'major:minor' -> block device name from /sys/class/block."""
    mapping = {}
    base = "/sys/class/block"
    try:
        for name in os.listdir(base):
            devfile = os.path.join(base, name, "dev")
            try:
                with open(devfile) as f:
                    mapping[f.read().strip()] = name
            except OSError:
                continue
    except OSError:
        pass
    return mapping


class SubsystemSource:
    """Owns one bpftrace subprocess and turns its JSON stream into metric rows."""

    def __init__(self, name, script_path, interval):
        self.name = name
        self.script_path = script_path
        self.interval = interval
        self.proc = None
        self.conn = None           # per-thread SQLite connection (set in read_loop)
        self.rows = []            # pending rows for the current frame
        self.in_frame = False
        self.inserted = 0
        self.frames = 0
        self.restarts = 0
        self.alive = True
        self.dev_names = _build_dev_name_map()

    def _script_with_interval(self):
        with open(self.script_path) as f:
            text = f.read()
        if self.interval != 5:
            text = text.replace("interval:s:5", f"interval:s:{self.interval}")
        return text

    def start(self):
        cmd = ["bpftrace", "-f", "json", "-e", self._script_with_interval()]
        log.debug("%s: starting %s", self.name, " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.last_line_ts = time.time()

    def _handle_printf(self, data, timestamp):
        text = data.rstrip("\n")
        if not text or "=" not in text:
            return
        key, _, value = text.partition("=")
        if key == START_MARKER:
            self.in_frame = True
            return
        if key == END_MARKER:
            self.in_frame = False
            self.flush()
            return
        if not self.in_frame:
            return
        try:
            self.rows.append((key, timestamp, float(value), "system"))
        except ValueError:
            log.debug("%s: non-numeric value for %s", self.name, key)

    def _handle_map(self, data, timestamp):
        if not self.in_frame:
            return
        for map_name, entries in data.items():
            metric = MAP_METRICS.get(map_name)
            if metric is None or not isinstance(entries, dict):
                continue
            for key, value in entries.items():
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
                source = self._decode_source(metric, key)
                self.rows.append((metric, timestamp, value, source))

    def _decode_source(self, metric, key):
        if metric == "blk_io_ops_per_dev":
            try:
                majmin = decode_dev_t(int(key))
                return self.dev_names.get(majmin, majmin)
            except (ValueError, TypeError):
                return str(key)
        return str(key)

    def flush(self):
        if self.conn is None:
            return
        if self.rows:
            db.insert_metrics(self.conn, self.rows)
            self.inserted += len(self.rows)
            self.rows = []
            self.frames += 1

    def read_loop(self, db_path):
        """Blocking reader; call from a dedicated thread. Survives restarts."""
        self.conn = db.connect(db_path)
        while self.alive:
            if self.proc is None:
                time.sleep(0.2)
                continue
            line = self.proc.stdout.readline()
            if not line:
                # subprocess ended (or was replaced); flush and wait for restart
                if self.in_frame:
                    self.flush()
                    self.in_frame = False
                self.rows = []
                time.sleep(0.2)
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.warning("%s: unparsable line: %r", self.name, line[:120])
                continue
            t = msg.get("type")
            if t == "printf":
                self._handle_printf(msg.get("data", ""), time.time())
            elif t == "map":
                self._handle_map(msg.get("data", {}), time.time())
            # attached_probes / helper_error / others are ignored

    def reap(self):
        """Return True if the process is still running."""
        if self.proc is None:
            return False
        rc = self.proc.poll()
        if rc is None:
            return True
        stderr = self.proc.stderr.read()
        if stderr:
            log.error("%s: bpftrace exited rc=%s, stderr:\n%s",
                      self.name, rc, stderr[-2000:])
        else:
            log.info("%s: bpftrace exited rc=%s", self.name, rc)
        return False

    def stop(self):
        self.alive = False
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def run_source(source):
    """Maintenance thread for one subsystem source: start, restart, stop."""
    source.start()
    while source.alive:
        if source.reap():
            time.sleep(0.2)
        else:
            if source.alive:
                source.restarts += 1
                log.warning("%s: restarting bpftrace (restart #%d)",
                            source.name, source.restarts)
                time.sleep(1.0)
                source.start()


def collect(db_path, scripts_dir, interval, duration):
    sources = []
    for sub in SUBSYSTEMS:
        path = os.path.join(scripts_dir, f"{sub}.bt")
        if not os.path.exists(path):
            log.error("missing script %s", path)
            continue
        sources.append(SubsystemSource(sub, path, interval))

    if not sources:
        log.error("no subsystem scripts found under %s", scripts_dir)
        return 1

    threads = []
    for src in sources:
        reader = threading.Thread(target=src.read_loop, args=(db_path,), daemon=True)
        reader.start()
        threads.append(reader)
        maint = threading.Thread(target=run_source, args=(src,), daemon=True)
        maint.start()

    start = time.time()
    last_summary = time.time()

    _stop = {"flag": False}

    def _on_signal(signum, _frame):
        log.info("signal %s received, shutting down", signum)
        _stop["flag"] = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    try:
        while not _stop["flag"]:
            if duration and time.time() - start >= duration:
                break
            now = time.time()
            if now - last_summary >= interval:
                last_summary = now
                log.info(
                    "alive=%s rows=%s restarts=%s",
                    {s.name: s.proc.poll() is None if s.proc else False
                     for s in sources},
                    {s.name: s.inserted for s in sources},
                    {s.name: s.restarts for s in sources},
                )
            time.sleep(0.5)
    except KeyboardInterrupt:
        log.info("interrupt received, shutting down")
    finally:
        for src in sources:
            src.stop()
        for t in threads:
            t.join(timeout=6)

    total = sum(s.inserted for s in sources)
    log.info("done: %d metric rows inserted across %d subsystems",
             total, len(sources))
    for src in sources:
        log.info("  %-8s rows=%-7d frames=%-4d restarts=%d",
                 src.name, src.inserted, src.frames, src.restarts)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "self_healing_os.db"))
    parser.add_argument("--interval", type=float, default=5.0,
                        help="snapshot cadence in seconds (1-5 recommended)")
    parser.add_argument("--duration", type=float, default=None,
                        help="stop after N seconds (default: run forever)")
    parser.add_argument("--scripts-dir", default=DEFAULT_SCRIPT_DIR)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    if os.geteuid() != 0:
        log.error("collector must run as root (bpftrace needs it): "
                  "sudo python3 %s", os.path.abspath(__file__))
        return 2

    if int(args.interval) < 1 or int(args.interval) > 5:
        log.warning("interval %ss outside the 1-5s design range; continuing anyway",
                    args.interval)

    os.makedirs(os.path.dirname(args.db) or ".", exist_ok=True)
    conn = db.connect(args.db)
    log.info("SQLite ready: %s", args.db)

    rc = collect(args.db, args.scripts_dir, int(args.interval), args.duration)
    log.info("health: %s", db.stats(conn))
    conn.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
