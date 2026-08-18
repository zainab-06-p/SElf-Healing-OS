#!/usr/bin/env python3
"""Formal evaluation harness (Step 3.2 in the plan).

Computes Precision / Recall / F1, Time-to-Detection and False-Positive rate
for the end-to-end pipeline, using per-window ground truth.

Two modes:

  1. Live-data evaluation (preferred, run on the deployment VM as root):

         sudo bash step1/scripts/run_experiment.sh    # writes experiment.db
         python3 step3/scripts/evaluate.py --db step1/data/experiment.db

     The harness reads the `__phase__` marker rows (metric_name='__phase__',
     value 0 = normal, 1 = fault) that run_experiment.sh writes into the
     metrics table, replays the full brain, and labels every feature window as
     normal/fault from those markers - the same ground truth used by
     run_experiment.sh's own phase comparison.

  2. Synthetic evaluation (works anywhere, no root/eBPF needed):

         python3 step3/scripts/evaluate.py --synthetic [--fault cpu|memory|io]

     Generates a labelled, realistic metrics corpus (controlled fault injection
     mirroring shos_cpuhog / shos_memleak / shos_iostress on top of a normal
     baseline), then runs the identical analysis. Used here to produce the
     evidence table documented in step3/EVALUATION.md.

Metrics reported (per the plan):

  * Precision / Recall / F1 over 5s feature windows:
        TP = fault window flagged, FP = normal window flagged,
        FN = fault window missed,  TN = normal window not flagged.
  * Time-to-detection: seconds between the fault phase starting and the first
    flagged window. Negative means the trend/model signalled before the fault
    matured (an early warning).
  * False-positive rate: FP / (FP + TN) and FP-per-hour on normal windows.

Exit codes: 0 ok, 1 evaluation failed (e.g. no phase markers for --db).
"""

import argparse
import json
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from step1.src import db  # noqa: E402
from step2.src.brain import Brain  # noqa: E402
from step2.src import config as cfg  # noqa: E402

# ---------------------------------------------------------------------------
# Synthetic corpus generation
# ---------------------------------------------------------------------------
# Realistic "normal" baseline for the OS this was collected on (same magnitudes
# as the Step 2 test corpus that the 20-test suite validates against).
BASE = {
    "sched_ctx_switches": 15000, "sched_preemptions": 6000, "sched_runq_lat_avg_us": 300,
    "mem_page_faults_total": 2000, "mem_oom_kills": 0, "blk_io_ops_total": 10,
    "blk_bytes_total": 60000, "blk_io_errors": 0, "irq_handled_total": 30,
}
NOISE = {
    "sched_ctx_switches": 2000, "sched_preemptions": 1000, "sched_runq_lat_avg_us": 80,
    "mem_page_faults_total": 800, "mem_oom_kills": 0, "blk_io_ops_total": 6,
    "blk_bytes_total": 30000, "blk_io_errors": 0, "irq_handled_total": 10,
}

FAULT_EFFECTS = {
    # shos_cpuhog: runaway loop starves the run-queue
    "cpu": {
        "sched_runq_lat_avg_us": 4000,
        "sched_preemptions": 12000,
        "sched_ctx_switches": 28000,
    },
    # shos_memleak: page faults climb and stay elevated as memory is consumed
    "memory": {
        "mem_page_faults_total": 45000,
        "mem_oom_kills": 2,
    },
    # shos_iostress: O_DIRECT write loop saturates block throughput/ops
    "io": {
        "blk_io_ops_total": 400,
        "blk_bytes_total": 4800000,
        "blk_io_errors": 15,
    },
}

GRID = 5.0  # feature-window width, must match cfg.INTERVAL


def _fault_override(fault):
    """Metric name -> (elevated value, noise)."""
    effects = FAULT_EFFECTS.get(fault, {})
    return {
        m: (effects[m], 0.12 * effects[m] + 1.0)
        for m in effects
    }


def gen_synthetic_experiment(fault, n_normal=120, n_fault=20, n_recovery=10, seed=7):
    """Produce a labelled metrics corpus for one fault scenario.

    Phases: n_normal normal windows, n_fault fault windows, n_recovery normal
    recovery windows - phase boundaries recorded as __phase__ marker rows.

    n_normal must exceed cfg.BASELINE_TRAIN (100) so the brain's initial
    baseline training (the first 100 windows) is pure normal data and never
    contains fault windows - otherwise living in `normal[:BASELINE_TRAIN]`
    would silently swallow the fault into the baseline.

    Returns an absolute path to a fresh SQLite file with the metrics loaded.
    """
    rng = random.Random(seed)
    rows = []
    t0 = 1700000000.0
    fault_zero = t0 + n_normal * GRID
    fault_end = fault_zero + n_fault * GRID

    override = _fault_override(fault)

    def emit_window(idx, ts):
        in_fault = idx >= n_normal and idx < n_normal + n_fault
        for m in cfg.CORE_METRICS:
            if in_fault and m in override:
                val = override[m][0] + abs(rng.gauss(0, override[m][1]))
            else:
                val = max(0.0, BASE[m] + rng.gauss(0, NOISE[m]))
            rows.append((m, ts, round(val, 2), "system"))

    for i in range(n_normal + n_fault + n_recovery):
        emit_window(i, t0 + i * GRID)

    rows.append(("__phase__", t0, 0.0, "experiment"))           # idle start
    rows.append(("__phase__", fault_zero, 1.0, "experiment"))    # fault start
    rows.append(("__phase__", fault_end, 0.0, "experiment"))     # fault end

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.unlink(tmp.name)
    conn = db.connect(tmp.name)
    db.insert_metrics(conn, rows)
    conn.close()
    return tmp.name


# ---------------------------------------------------------------------------
# Ground truth from phase markers
# ---------------------------------------------------------------------------
def phase_intervals(conn):
    """Return list of (fault_start, fault_end) from __phase__ marker rows."""
    marks = conn.execute(
        "SELECT timestamp, value FROM metrics WHERE metric_name='__phase__' "
        "ORDER BY timestamp"
    ).fetchall()
    intervals = []
    for i, (ts, value) in enumerate(marks):
        if value >= 1.0:  # a fault phase begins here and ends at the next marker
            end = marks[i + 1][0] if i + 1 < len(marks) else float("inf")
            intervals.append((ts, end))
    return intervals


def ground_truth(conn, phase_list):
    """Build {window_start_ts: 0|1} labels from the completed feature windows."""
    out = {}
    for start_ts, end_ts in conn.execute("SELECT start_ts, end_ts FROM windows"):
        is_fault = any(start_ts < fend and end_ts > fstart for fstart, fend in phase_list)
        out[round(start_ts, 3)] = 1 if is_fault else 0
    return out


def predicted_flags(conn):
    """Set of window start timestamps that the brain raised an incident on."""
    return {round(ts, 3) for (ts,) in conn.execute("SELECT timestamp FROM incidents")}


def _remove_db(path):
    """Remove a SQLite file plus its WAL/SHM sidecars."""
    for suffix in ("", "-wal", "-shm"):
        p = path + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Replay + metrics
# ---------------------------------------------------------------------------
def run_eval(db_path):
    """Replay the brain over a fresh copy of db_path and return the metrics."""
    work_path = tempfile.mktemp(suffix=".eval.db")
    try:
        shutil.copyfile(db_path, work_path)
        conn = db.connect(work_path)
        phase_list = phase_intervals(conn)
        if not phase_list:
            return None, "no __phase__ markers found - cannot label ground truth"
        brain = Brain(work_path, quiet=True)
        brain.replay(dry_run=True)
        conn.close()

        conn = db.connect(work_path)
        gt = ground_truth(conn, phase_list)
        pred = predicted_flags(conn)

        # The first cfg.BASELINE_TRAIN windows are the brain's *training*
        # baseline, never scored - exclude them so the confusion matrix only
        # covers windows the model was actually graded on.
        excluded = set(sorted(gt.keys())[:cfg.BASELINE_TRAIN])

        tp = fp = fn = tn = 0
        ttd = []  # time-to-detection per fault phase
        for st, label in gt.items():
            if st in excluded:
                continue
            flagged = st in pred
            if label:
                tp += flagged
                fn += not flagged
            else:
                fp += flagged
                tn += not flagged

        for fstart, fend in phase_list:
            det = [st for st in gt if fstart <= st < fend and st in pred]
            if det:
                ttd.append(min(st - fstart for st in det))
            elif any(st >= fstart and st in pred for st in gt):
                ttd.append(min(st - fstart for st in gt if st >= fstart and st in pred))
            else:
                ttd.append(None)  # fault completely missed

        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        fpr = fp / (fp + tn) if (fp + tn) else 0.0
        normal_hours = (fp + tn) * cfg.INTERVAL / 3600.0
        fp_per_hour = fp / normal_hours if normal_hours else 0.0

        n_incidents = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        n_windows = len([st for st in gt if st not in excluded])
        outcome = {
            "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "fpr": round(fpr, 4),
            "false_positives_per_hour": round(fp_per_hour, 3),
            "time_to_detection_s": [None if x is None else round(x, 2) for x in ttd],
            "n_windows": int(n_windows), "n_incidents": int(n_incidents),
        }
        conn.close()
        return outcome, None
    finally:
        _remove_db(work_path)


def _fmt_time(sek):
    if sek is None:
        return "missed"
    if sek <= 0:
        return f"early ({sek:+.0f}s)"
    return f"{sek:.0f}s"


def print_table(results, faults):
    print("\n=== Step 3.2 Evaluation: Precision / Recall / F1 ===")
    print(f"{'fault':<10}{'TP':>4}{'FP':>4}{'FN':>4}{'TN':>5}  "
          f"{'Precision':>9}{'Recall':>7}{'F1':>6}  {'TTD':>14}{'FPR':>7}  FP/h")
    for fault in faults:
        r = results[fault]
        print(f"{fault:<10}{r['tp']:>4}{r['fp']:>4}{r['fn']:>4}{r['tn']:>5}  "
              f"{r['precision']:>9.3f}{r['recall']:>7.3f}{r['f1']:>6.3f}  "
              f"{_fmt_time(r['time_to_detection_s'][0] if r['time_to_detection_s'] else None):>14}"
              f"{r['fpr']:>7.3f}  {r['false_positives_per_hour']:.2f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="run evaluation on this metrics DB (needs __phase__ markers)")
    parser.add_argument("--synthetic", action="store_true",
                        help="generate a labelled synthetic fault-injection corpus and evaluate")
    parser.add_argument("--fault", choices=sorted(FAULT_EFFECTS) + ["all"], default="all")
    parser.add_argument("--out", default=os.path.join(ROOT, "step3", "results", "eval_results.json"),
                        help="path to write the results JSON")
    args = parser.parse_args()

    if not args.db and not args.synthetic:
        parser.error("provide --db PATH or --synthetic")

    results = {}

    if args.db:
        res, err = run_eval(os.path.abspath(args.db))
        if err:
            print(f"[evaluate] error: {err}", file=sys.stderr)
            return 1
        results["live"] = res
        print_table(results, ["live"])
    else:
        faults = [args.fault] if args.fault != "all" else sorted(FAULT_EFFECTS)
        for fault in faults:
            corpus = gen_synthetic_experiment(fault)
            res, err = run_eval(corpus)
            if err:
                print(f"[evaluate] {fault}: {err}", file=sys.stderr)
                results[fault] = {"error": err}
            else:
                results[fault] = res
            _remove_db(corpus)
        print_table(results, faults)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n[results written to {args.out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())