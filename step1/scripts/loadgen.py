#!/usr/bin/env python3
"""
Robust load generator for the Step 1 experiment.

stress-ng's CPU stressors SIGILL inside this VirtualBox VM and its vm/hdd
stressors can abort on a memory-tight box, so we generate load with plain,
portable primitives instead:

  cpu:   N busy loops (pure integer arithmetic)
  mem:   a buffer that is touched page-by-page in a loop (page faults)
  io:    direct (O_DIRECT) 1 MiB writes to a real-disk temp file (block I/O)
  fork:  rate-limited child spawn/exit churn (scheduler pressure)

Files are written under /var/tmp (a real block device), NOT /tmp which is
tmpfs, so the block-I/O signal actually hits the kernel block layer.

Usage:
  python3 loadgen.py [--cpu N] [--mem MB] [--io N] [--fork N] [--duration SEC]

Exit code 0. All workers run until --duration elapses.
"""

import argparse
import mmap
import os
import sys
import threading
import time

_sink = {}          # prevents the compiler from optimizing loops away
_stop = threading.Event()
IO_DIR = "/var/tmp"


def cpu_worker(seed):
    x = seed
    while not _stop.is_set():
        for i in range(200000):
            x = (x * 1103515245 + 12345) & 0xFFFFFFFF
    _sink["cpu"] = x


def mem_worker(size_mb):
    buf = bytearray(size_mb * 1024 * 1024)
    n = len(buf)
    i = 0
    while not _stop.is_set():
        buf[i % n] = 1
        i += 4096
    _sink["mem"] = buf


def io_worker(path):
    buf = mmap.mmap(-1, 1024 * 1024)          # page-aligned -> O_DIRECT safe
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_DIRECT, 0o600)
    try:
        while not _stop.is_set():
            os.lseek(fd, 0, os.SEEK_SET)
            for _ in range(16):               # 16 MiB per pass
                os.write(fd, buf)
            os.fsync(fd)
    except OSError as exc:                    # e.g. O_DIRECT unsupported
        print(f"io_worker({path}) error: {exc}", file=sys.stderr)
    finally:
        os.close(fd)


def fork_worker():
    while not _stop.is_set():
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        time.sleep(0.02)                      # self-limit ~50 forks/s/worker


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cpu", type=int, default=2)
    ap.add_argument("--mem", type=int, default=96, help="MiB to fault in a loop")
    ap.add_argument("--io", type=int, default=1, help="number of O_DIRECT writers")
    ap.add_argument("--fork", type=int, default=0, help="fork rate (0 = off)")
    ap.add_argument("--duration", type=float, default=30)
    args = ap.parse_args()

    workers = []
    for i in range(args.cpu):
        t = threading.Thread(target=cpu_worker, args=(i,), daemon=True)
        t.start(); workers.append(t)
    if args.mem > 0:
        t = threading.Thread(target=mem_worker, args=(args.mem,), daemon=True)
        t.start(); workers.append(t)
    for i in range(args.io):
        path = f"{IO_DIR}/shos_loadgen_{os.getpid()}_{i}.bin"
        t = threading.Thread(target=io_worker, args=(path,), daemon=True)
        t.start(); workers.append(t)
    for i in range(args.fork):
        t = threading.Thread(target=fork_worker, daemon=True)
        t.start(); workers.append(t)

    deadline = time.time() + args.duration
    try:
        while time.time() < deadline:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        _stop.set()
        time.sleep(0.3)
        for p in [f"{IO_DIR}/shos_loadgen_{os.getpid()}_{i}.bin"
                  for i in range(args.io)]:
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
