#!/usr/bin/env python3
"""End-to-end demo for Step 2 (Section 2.8 exit criteria).

Runs the full live pipeline against a seeded normal baseline:

    seed DB (real normal history) -> collector (bpftrace -> SQLite)
        -> brain (features -> Isolation Forest -> explain -> gate)
        -> fault injection -> action engine -> post-action monitoring
        -> resolve/rollback -> all logged through SQLite

Usage (run as root, or it will re-invoke itself with sudo):

    sudo python3 step2/scripts/run_e2e_demo.py [--db step2/data/demo.db]
                    [--seed step1/data/self_healing_os.db]
                    [--fault cpu|io|memory] [--fault-seconds 90]

The demo polls SQLite and prints a phase timeline, then a final incident
summary with the detected -> explained -> acted -> resolved/rolled-back chain.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

STEP1_COLLECTOR = os.path.join(ROOT, "step1", "src", "collector.py")
STEP2_RUN_BRAIN = os.path.join(ROOT, "step2", "scripts", "run_brain.py")
STEP2_FAULT = os.path.join(ROOT, "step2", "scripts", "fault_inject.py")
FAULT_MARKERS = {"cpu": "shos_cpuhog", "io": "shos_iostress", "memory": "shos_memleak"}

SUDO = "SUDO_ASKPASS" not in os.environ


def run(cmd, sudo=False, log=None):
    full = ["sudo", "-S"] + cmd if sudo and os.geteuid() != 0 else cmd
    out = open(log, "a") if log else subprocess.DEVNULL
    return subprocess.Popen(
        full,
        stdin=subprocess.PIPE if sudo and os.geteuid() != 0 else None,
        stdout=out,
        stderr=out,
    )


def q(conn_path, sql):
    import sqlite3

    try:
        c = sqlite3.connect(conn_path, timeout=10)
        rows = c.execute(sql).fetchall()
        c.close()
        return rows
    except Exception as exc:
        print(f"  [demo] db read retry ({exc})", flush=True)
        return []


def wait_for(conn_path, sql, timeout, desc, poll=1.0):
    start = time.time()
    while time.time() - start < timeout:
        rows = q(conn_path, sql)
        if rows and rows[0][0]:
            return rows
        time.sleep(poll)
    print(f"  !! timed out waiting for: {desc}")
    return []


def brain_min_train():
    sys.path.insert(0, ROOT)
    from step2.src import config as cfg

    return cfg.MIN_TRAIN


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "step2", "data", "demo.db"))
    ap.add_argument("--seed", default=os.path.join(ROOT, "step1", "data", "self_healing_os.db"))
    ap.add_argument("--fault", choices=list(FAULT_MARKERS), default="cpu")
    ap.add_argument("--nprocs", type=int, default=1,
                    help="number of concurrent fault processes (cpu: use 2 to "
                         "starve both cores; one busy loop parks on the idle CPU)")
    ap.add_argument("--fault-seconds", type=int, default=90)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--max-wait", type=float, default=300)
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("re-invoking with sudo...")
        os.execvp("sudo", ["sudo", "-S"] + sys.argv)

    # kill any strays from a previous demo that targeted this db path
    for pat in (f"collector.py --db {args.db}", f"run_brain.py --db {args.db}",
                "fault_inject.py --role "):
        subprocess.run(["pkill", "-9", "-f", pat], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    time.sleep(1)

    os.makedirs(os.path.dirname(args.db), exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(args.db + suffix)
        except FileNotFoundError:
            pass
    if args.seed and os.path.exists(args.seed):
        shutil.copy(args.seed, args.db)
    else:
        # empty DB: schema is created by the collector/brain on connect, and the
        # model trains on *current* known-normal windows (no stale baseline)
        open(args.db, "w").close()

    procs = []
    try:
        # 1. collector + brain together; the brain auto-trains on the first
        #    MIN_TRAIN current-normal windows, then starts scoring.
        print("[demo] starting collector (bpftrace -> SQLite)...")
        coll = run([sys.executable, STEP1_COLLECTOR, "--db", args.db,
                    "--interval", str(args.interval)], log="/tmp/e2e_coll.log")
        procs.append(coll)
        print("[demo] starting brain (auto-train on current normal + live loop)...")
        brain = run([sys.executable, STEP2_RUN_BRAIN, "--db", args.db], log="/tmp/e2e_brain.log")
        procs.append(brain)
        time.sleep(args.interval * 2.5)  # let a few live windows land

        # 2. wait for the model to be trained on current normal (MIN_TRAIN windows)
        print(f"[demo] capturing current normal baseline "
              f"(model trains at {brain_min_train()} windows)...")
        wait_for(
            args.db,
            "SELECT COUNT(*) FROM model_versions",
            args.max_wait, "initial model training",
        )
        nwin = q(args.db, "SELECT COUNT(*) FROM windows")[0][0]
        print(f"[demo] model trained on {nwin} current-normal windows; injecting fault...")

        # 3. inject the fault
        print(f"[demo] injecting fault: {args.fault} "
              f"({FAULT_MARKERS[args.fault]} x{args.nprocs}) for {args.fault_seconds}s...")
        for _ in range(args.nprocs):
            fault = run([sys.executable, STEP2_FAULT, "--role", FAULT_MARKERS[args.fault]],
                        log=f"/tmp/e2e_fault_{_}.log")
            procs.append(fault)

        # 4. wait for detection
        det = wait_for(
            args.db,
            "SELECT COUNT(*) FROM incidents WHERE outcome NOT IN ('suggestion','action_suggested')",
            args.max_wait, "an action-grade incident",
        )
        if det:
            inc = q(args.db, "SELECT incident_id, anomaly_score, outcome FROM incidents "
                            "WHERE outcome NOT IN ('suggestion','action_suggested') "
                            "ORDER BY incident_id LIMIT 1")[0]
            print(f"  detected: incident #{inc[0]} score={inc[1]:.4f} outcome={inc[2]}")
            print("  waiting for post-action monitoring to conclude...")
            conclusion = wait_for(
                args.db,
                "SELECT COUNT(*) FROM incidents WHERE outcome IN ('resolved','failed')",
                args.max_wait, "post-action monitoring conclusion",
            )
            if conclusion:
                final = q(args.db, "SELECT incident_id, outcome, rollback_used FROM incidents "
                                   "WHERE outcome IN ('resolved','failed') ORDER BY incident_id LIMIT 1")[0]
                print(f"  conclusion: incident #{final[0]} -> {final[1]} "
                      f"(rollback={final[2]})")
        else:
            print("  !! no action-grade incident produced")

        # let recovery windows land before tearing down
        time.sleep(args.interval * 3)
    finally:
        print("[demo] tearing down...")
        for p in reversed(procs):
            try:
                p.terminate()
            except Exception:
                pass
        time.sleep(4)
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
        # belt and braces: kill any orphaned fault children
        subprocess.run(["pkill", "-9", "-f", "fault_inject.py --role"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)

    # summary
    print("\n================ E2E SUMMARY ================")
    print("model_versions:")
    for v in q(args.db, "SELECT version_id, n_samples, active FROM model_versions"):
        print(f"  v{v[0]}: n_train={v[1]} active={v[2]}")
    print("incidents (detect -> explain -> act -> resolve/rollback):")
    for inc in q(args.db, "SELECT incident_id, ROUND(anomaly_score,4), outcome, "
                          "substr(explanation,1,140), rollback_used "
                          "FROM incidents ORDER BY incident_id LIMIT 20"):
        print(f"  #{inc[0]} score={inc[1]:>8} {inc[2]:<16} rollback={inc[4]} | {inc[3]}")
    print("windows:", q(args.db, "SELECT COUNT(*) FROM windows")[0][0])


if __name__ == "__main__":
    main()
