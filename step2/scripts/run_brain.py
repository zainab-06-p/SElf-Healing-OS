#!/usr/bin/env python3
"""Brain runner: bootstrap from existing history, then run the live loop.

    sudo python3 step2/scripts/run_brain.py --db PATH [--quiet]

Runs as root so the action engine can execute and *roll back* privileged
operations (restoring a renice'd process's priority needs CAP_SYS_NICE).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from step2.src.brain import Brain  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--duration", type=float, default=None, help="run live for N seconds")
    parser.add_argument("--poll", type=float, default=1.0)
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("[brain] warning: not running as root - rollback of privileged "
              "actions (renice restore) will fail", file=sys.stderr)

    brain = Brain(args.db, quiet=args.quiet)
    brain.bootstrap()
    print(f"[brain] bootstrap done: {len(brain.existing_windows)} windows, "
          f"model v{brain.model.version}", flush=True)
    brain.live_loop(duration=args.duration, poll_interval=args.poll)


if __name__ == "__main__":
    main()
