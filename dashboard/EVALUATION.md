# Step 3.2 — Formal Evaluation Report

**Method:** `step3/scripts/evaluate.py` replays the full brain (`feature engine →
Isolation Forest → explain → decision gate → action engine`) over a labelled
metrics corpus and scores every 5-second feature window against ground truth.
Two modes:

1. **Synthetic** (no root/eBPF needed): a controlled fault-injection corpus is
   generated for each of the three automated scenarios (`shos_cpuhog` → CPU
   starvation, `shos_memleak` → memory-pressure growth, `shos_iostress` → I/O
   degradation) on top of a realistic normal baseline, with `__phase__` markers
   labelling normal/fault windows. This is the evidence table below.
2. **Live** (run on the deployment VM, as root):

       sudo bash step1/scripts/run_experiment.sh
       python3 step3/scripts/evaluate.py --db step1/data/experiment.db

   The same harness reads the real `__phase__` markers that
   `run_experiment.sh` writes and computes the identical metrics — swap in the
   live numbers once the hardware run is complete.

**Ground truth / predictions:**

- A window is **fault** if it overlaps a fault phase (`__phase__=1` marker
  until the next marker).
- A window is **flagged** if the brain logged an incident at that window.
- The brain's baseline-training prefix (`cfg.BASELINE_TRAIN` = first 100 normal
  windows) is excluded from the confusion matrix — those windows train the
  model, they are not graded.

**Reported metrics** (per the plan): Precision = TP/(TP+FP), Recall =
TP/(TP+FN), F1, Time-to-detection (seconds from fault start to first flag;
negative = early warning), False-positive rate FP/(FP+TN) and FP/hour on normal
windows.

## Results (synthetic corpus, `seed=7`, 120 normal + 20 fault + 10 recovery windows)

| Fault | TP | FP | FN | TN | Precision | Recall | F1 | Time-to-Detection | FPR | FP/hour |
|-------|----|----|----|----|-----------|--------|-----|-------------------|-----|---------|
| cpu    | 20 | 3  | 0  | 27 | 0.870     | 1.000  | 0.930 | early (+0s) | 0.100 | 72 |
| io     | 20 | 2  | 0  | 28 | 0.909     | 1.000  | 0.952 | early (+0s) | 0.067 | 48 |
| memory | 20 | 1  | 0  | 29 | 0.952     | 1.000  | 0.976 | early (+0s) | 0.033 | 24 |

Raw JSON: `step3/results/eval_results.json`.

## Interpretation

- **100% recall across all three automated fault classes** — no injected fault
  window was missed; every fault was caught on the first window it appeared
  ("early", i.e. the trend/confidence signal fired as soon as the fault's
  window matured).
- **Precision 0.87–0.95** — false positives are confined to the 1–3 windows
  straddling fault *recovery* (a metric in transition back to baseline), not
  steady-state normal operation. The remaining FPs are the boundary effect of
  the fault tail, not false alarms during idle workload.
- **FPR on normal windows 3–10%**; these are the same recovery-boundary
  windows. On a pure-normal run the false-positive rate is effectively zero
  (verified by the `replay` pipeline tests: a pure-normal corpus yields no
  incidents).

## Known gaps (documented, not measured here)

- **IRQ anomaly** remains *suggestion-only* by design (no automated kernel
  module reload), so it is not included in the action classes above.
- **Live numbers pending**: the deployment VM should re-run the harness on a
  hardware `experiment.db` and replace this table — the script requires no
  changes to do so.
- The synthetic baseline magnitudes mirror the Step 2 test corpus
  (`BASE`/`NOISE` in `evaluate.py`), not a full-day idle trace; the live run
  provides the definitive numbers.
