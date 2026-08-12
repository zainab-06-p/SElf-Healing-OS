#!/usr/bin/env python3
"""Fault injection for the Self-Healing OS demo (Section 2.2.2 / 2.8).

Provides three labelled, observable faulty processes whose cmdline contains a
stable marker so the action engine can find them via /proc:

    shos_memleak   - unbounded memory growth (restart target)
    shos_cpuhog    - CPU starvation busy loop (renice target)
    shos_iostress  - O_DIRECT write loop to real disk (ionice target)

Usage:
    python3 fault_inject.py --role shos_cpuhog [--child]
      (without --child: spawns the role as a child and waits for SIGTERM)
      (with    --child: runs the loop in this process)

Signals: SIGTERM/SIGINT terminate cleanly. In manager mode the child is
forwarded the signal.
"""

import argparse
import os
import signal
import subprocess
import sys
import time

CHUNK = 64 * 1024 * 1024  # 64 MiB
IO_FILE = "/var/tmp/shos_iostress.bin"
IO_SIZE = 256 * 1024 * 1024  # 256 MiB scratch file
LEAK_CAP = int(os.environ.get("SHOS_LEAK_CAP_MB", "512")) * 1024 * 1024


def _marker_of(role):
    return f"shos_{role}"


def run_memleak():
    blocks = []
    while True:
        try:
            block = bytearray(CHUNK)
            blocks.append(block)
            if sum(len(b) for b in blocks) > LEAK_CAP:
                blocks = blocks[1:]  # hold the cap, still "leaking" fresh pages
            time.sleep(0.05)
        except MemoryError:
            blocks = []
            time.sleep(0.2)


def run_cpuhog():
    x = 0.0
    while True:
        x = x * 1.0000001 + 0.5
        _ = (x % 1.0) * 0.7


def run_iostress():
    import mmap

    # Per-PID scratch file so a stale file from a previous (root) run never
    # blocks a later demo, and so concurrent iostress processes don't collide.
    scratch = f"{IO_FILE}.{os.getpid()}"
    fd = None
    try:
        try:
            fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_DIRECT, 0o666)
        except OSError:
            # O_DIRECT unavailable (permission / filesystem): fall back to a
            # buffered writer so the fault still generates real disk I/O.
            fd = os.open(scratch, os.O_WRONLY | os.O_CREAT, 0o666)
        buf = mmap.mmap(-1, 1024 * 1024)  # page-aligned -> O_DIRECT safe
        pos = 0
        while True:
            written = 0
            while written < CHUNK:
                n = os.write(fd, buf)
                written += n
            pos += written
            if pos >= IO_SIZE:
                os.fsync(fd)
                pos = 0
                os.lseek(fd, 0, os.SEEK_SET)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"iostress: I/O failed: {exc}\n")
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(scratch)
        except OSError:
            pass


ROLES = {
    "shos_memleak": run_memleak,
    "shos_cpuhog": run_cpuhog,
    "shos_iostress": run_iostress,
}


def _child_argv(role):
    return [sys.executable, os.path.abspath(__file__), "--role", role, "--child"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=sorted(ROLES), required=True)
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args()

    marker = _marker_of(args.role)

    def _term(signum, _frame):
        sys.exit(0)

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    if not args.child:
        # manager mode: spawn the real loop as a child with the marker in argv
        proc = subprocess.Popen(_child_argv(args.role), stdout=subprocess.DEVNULL)

        def _forward_term(signum, _frame):
            try:
                proc.terminate()
            except Exception:
                pass
            sys.exit(0)

        signal.signal(signal.SIGTERM, _forward_term)
        signal.signal(signal.SIGINT, _forward_term)
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait()
        return

    sys.argv[0] = marker  # cosmetic: make ps show the marker
    try:
        ROLES[args.role]()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
