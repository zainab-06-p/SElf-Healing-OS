# 🩺 Self-Healing OS — Project Progress Report

> **Last Updated:** August 18, 2026  
> **Repo:** [github.com/zainab-06-p/SElf-Healing-OS](https://github.com/zainab-06-p/SElf-Healing-OS)  
> **Overall Completion: ~88%** — Steps 1 & 2 fully implemented; Step 3 (PyQt6 dashboard + formal evaluation) built and verified. Remaining: live-VM hardware evaluation numbers + demo rehearsal polish.

---

## Architecture Overview

```
eBPF probes  ->  ring buffer  ->  feature engine  ->  Isolation Forest  ->  decision gate  ->  action engine (+ rollback)
                                         ^                                                              |
                                         +----- outcome log / retrain (every 200) <--------------------+
                                                             |
                                                  [Step 3] Desktop App
```

---

## STEP 1 — Data Pipeline (Kernel -> Storage) — STATUS: COMPLETE (100%)

> Goal: Capture real kernel-level signal via eBPF, aggregate it cheaply, and persist as a proper time series in SQLite.

### 1.1 eBPF / bpftrace Scripts — `step1/bpftrace/`

| File | Subsystem | What It Does | Status |
|------|-----------|--------------|--------|
| `sched.bt` | Scheduling | Tracks `sched_switch`/`sched_wakeup` — run-queue latency, context switches, preemptions | ✅ Done |
| `memory.bt` | Memory | Tracks `mm_page_fault`/`oom_kill_process` — page fault rates, OOM kill events | ✅ Done |
| `block.bt` | Block I/O | Tracks `block_rq_issue`/`block_rq_complete` — I/O latency and op counts per device | ✅ Done |
| `irq.bt` | Interrupts | Tracks `irq_handler_entry`/`irq_handler_exit` — interrupt frequency and latency | ✅ Done |
| `diagnostics/histograms.bt` | All | Diagnostic latency histograms for validation | ✅ Done |

In-kernel aggregation via BPF maps is implemented — data is pre-aggregated inside the kernel before being sent to userspace every 1–5 seconds.

---

### 1.2 Userspace Collector — `step1/src/collector.py`

| Feature | Status |
|---------|--------|
| Launches all 4 bpftrace subsystem scripts in parallel threads | ✅ Done |
| Parses bpftrace JSON output (printf + map events) | ✅ Done |
| Snapshot framing markers to avoid false data from bpftrace's END flush | ✅ Done |
| Decodes dev_t numbers to human-readable device names from /sys/class/block | ✅ Done |
| Writes metric rows to SQLite immediately (no in-memory buffering) | ✅ Done |
| Survives bpftrace crashes and auto-restarts each subsystem independently | ✅ Done |
| Configurable via CLI: --db, --interval, --duration, --scripts-dir, --verbose | ✅ Done |
| Graceful shutdown on SIGTERM/SIGINT with per-subsystem row/frame/restart summary | ✅ Done |
| Root-check guard (bpftrace requires root) | ✅ Done |

---

### 1.3 Storage Layer — `step1/src/db.py`

| Feature | Status |
|---------|--------|
| SQLite WAL mode + NORMAL sync (safe, fast for demo-scale writes) | ✅ Done |
| `metrics` table with time-series indexes (metric_name + timestamp, timestamp) | ✅ Done |
| `windows` table (pre-created for Step 2) | ✅ Done |
| `incidents` table (pre-created for Step 2) | ✅ Done |
| `model_versions` table (pre-created for Step 2) | ✅ Done |
| stats() health-check returning row counts + time span | ✅ Done |
| Schema swappable to TimescaleDB/InfluxDB without changing ingestion logic | ✅ Done |

---

### 1.4 Load Generator & Experiment Scripts — `step1/scripts/`

| File | Status |
|------|--------|
| `loadgen.py` — controlled CPU/memory/IO load generation for baseline collection | ✅ Done |
| `run_experiment.sh` — shell script to run idle, load, and stress-ng experiments | ✅ Done |

---

## STEP 2 — The Brain (Feature Engineering -> ML -> Action Engine) — STATUS: COMPLETE (100%)

> Goal: Turn raw stored metrics into a detected, explained, and safely-acted-upon incident — entirely through SQLite, no UI needed.

---

### 2.1 Feature Engineering — `step2/src/feature_engine.py`

| Feature | Status |
|---------|--------|
| Reads raw metrics rows and aligns them to a fixed 5s time grid (handles collector jitter) | ✅ Done |
| Per-metric, per-window: cur, mean, std, min, max | ✅ Done |
| Trend features: linear-regression slope over 3 horizons (30s, 2min, 5min) | ✅ Done |
| EWMA residual (actual minus EWMA) to detect deviation from recent behavior | ✅ Done |
| Lag features: lag1, lag2, lag3 for short-term trajectory | ✅ Done |
| **108-dimensional feature vector** per window across all 9 core metrics | ✅ Done |
| Writes completed windows into the `windows` table | ✅ Done |
| Incremental polling: only reads new rows since last poll for live mode | ✅ Done |

---

### 2.2 ML Model (Isolation Forest) — `step2/src/model.py`

| Feature | Status |
|---------|--------|
| sklearn IsolationForest with n_estimators=100, contamination=0.02 | ✅ Done |
| Continuous anomaly scoring via decision_function() (not just binary predict()) | ✅ Done |
| Calibrated z-score from training distribution — gates on "how many sigma above normal" | ✅ Done |
| Percentile confidence — what fraction of training windows this sample outscores | ✅ Done |
| Deviation magnitude — max z-score over core metrics, catches severe spikes | ✅ Done |
| Per-feature mean/std stored for explanations and similarity lookups | ✅ Done |
| Training preserves chronological order (never shuffled) | ✅ Done |

---

### 2.3 Auto-Retraining Loop — `step2/src/model.py` + `step2/src/brain.py`

| Feature | Status |
|---------|--------|
| Triggers retraining every 200 new feature windows | ✅ Done |
| Trains on sliding window of last 2000 normal windows (avoids catastrophic forgetting) | ✅ Done |
| Validation gate before promoting: flags 1–5% of held-out normal windows = accepted | ✅ Done |
| Keeps previous model active on rejection; logs rejection reason | ✅ Done |
| Every model version logged to model_versions table | ✅ Done |

---

### 2.4 Trend / Forecast Layer — `step2/src/trend.py`

| Feature | Status |
|---------|--------|
| Linear regression on last N points per metric, projected ~30s forward | ✅ Done |
| Independent early-warning signal separate from Isolation Forest score | ✅ Done |
| Residual std after detrending (prevents noisy-but-flat signals from triggering) | ✅ Done |
| Relative floor check (projected rise must be >25% of mean to count) | ✅ Done |

---

### 2.5 Explainability (XAI) — `step2/src/explain.py`

| Method | Status |
|--------|--------|
| Native Isolation Forest attribution — walks every tree, credits split feature weighted by 1/(depth+1), combined with feature z-score | ✅ Done |
| SHAP TreeExplainer (more rigorous second layer) — uses shap.TreeExplainer when available | ✅ Done |
| Automatic fallback to KernelExplainer if TreeExplainer doesn't support the installed version | ✅ Done |
| Fallback to native attribution if SHAP is not installed (graceful degradation) | ✅ Done |
| Natural-language templating — top 3 features converted to human-readable sentences | ✅ Done |
| Historical similarity lookup — cosine similarity search against z-scored vectors of past incidents | ✅ Done |

Example output: *"Anomaly detected (confidence 99%). Run-queue latency is 13.3x its baseline (4000 vs 300). This looks similar to incident #3 (cosine 0.97), which was resolved (renice)."*

---

### 2.6 Decision Gate — `step2/src/brain.py`

| Rule | Trigger | Status |
|------|---------|--------|
| **Normal** | Confidence and magnitude both below suggest threshold, no trend warnings | ✅ Done |
| **Suggestion** | Single anomaly score above suggest threshold OR trend warning fires | ✅ Done |
| **Action** | Sustained anomaly (>=3 of last 5 windows above threshold) AND high score/magnitude | ✅ Done |
| **Action via trend** | Trend warning AND score above trend-push threshold | ✅ Done |
| Dual gate: both confidence (percentile) AND magnitude (per-feature sigma) can independently trigger | ✅ Done |

---

### 2.7 Action Engine & Rollback — `step2/src/action_engine.py`

| Detected Condition | Action | Rollback | Status |
|--------------------|--------|----------|--------|
| CPU starvation (sched features) | renice — lower offending process priority by +10 nice | Restore original priority | ✅ Done |
| I/O degradation (blk features) | ionice — set offending process to idle I/O class | Restore original I/O class | ✅ Done |
| Memory leak (mem features) | restart — SIGTERM offending process, re-launch | Cooldown prevents immediate re-restart | ✅ Done |
| IRQ anomaly (irq features) | Suggestion only — no automated action yet | N/A | ⚠️ Partial |

**Safety rules (all implemented):**

| Safety Rule | Status |
|-------------|--------|
| Hard action allowlist — only pre-registered, reversible actions (no arbitrary shell exec) | ✅ Done |
| Pre-action state snapshot stored before any action runs | ✅ Done |
| Rate limiting / cooldown per action target (prevents action loops) | ✅ Done |
| 30-second post-action monitoring window: marks incident resolved or failed + triggers rollback | ✅ Done |
| All outcomes logged to incidents table (audit trail + retraining data source) | ✅ Done |

---

### 2.8 Brain Orchestrator — `step2/src/brain.py`

| Feature | Status |
|---------|--------|
| replay() — processes all existing metrics from SQLite (backfill / testing) | ✅ Done |
| bootstrap() — initial training on available normal history | ✅ Done |
| live_loop() — polls for new metrics in real time, acts and monitors live | ✅ Done |
| Deduplication: won't create duplicate incidents across restarts | ✅ Done |
| Full pipeline in one call: feature_engine -> model -> trend -> explainer -> gate -> action -> retrain | ✅ Done |

---

### 2.9 Fault Injection Scripts — `step2/scripts/`

| File | Status |
|------|--------|
| `fault_inject.py` — CPU hog (shos_cpuhog), memory leak (shos_memleak), I/O stress (shos_iostress) | ✅ Done |
| `run_brain.py` — CLI entry point to run the brain in replay or live mode | ✅ Done |
| `run_e2e_demo.py` — end-to-end demo script: starts collector, injects faults, runs brain | ✅ Done |

---

### 2.10 Tests — `step2/tests/test_step2.py` (21 tests total)

| Test Area | Tests | Status |
|-----------|-------|--------|
| Feature engine: window bucketing, 108-feature vector, zero-fill on missing subsystem | 3 tests | ✅ Done |
| Feature response to CPU fault (scheduler latency 3x above normal) | 1 test | ✅ Done |
| Isolation Forest scores (fault windows score higher than normal) | 1 test | ✅ Done |
| Retrainer: accepts a good model, rejects contamination=0.5 model | 1 test | ✅ Done |
| Retrainer: rejects a candidate that loses detection of past incidents | 1 test | ✅ Done |
| Retraining triggers and logs to model_versions table | 2 tests | ✅ Done |
| Trend layer: warns on rising signal, silent on flat signal | 1 test | ✅ Done |
| Explainer: native attribution ranks true driver, natural-language output | 1 test | ✅ Done |
| Historical similarity: second fault references first incident | 1 test | ✅ Done |
| SHAP fallback to native when SHAP not installed | 1 test | ✅ Done |
| SHAP used when available (monkeypatched) | 1 test | ✅ Done |
| Decision gate: all 8 combinations of confidence/magnitude/trend/sustained | 1 test | ✅ Done |
| Action engine: condition classification, cooldown, monitoring conclusion | 3 tests | ✅ Done |
| Full replay pipeline: incidents created, windows stored, model logged | 1 test | ✅ Done |
| Live loop: detects fault via background thread injection | 1 test | ✅ Done |
| Live action + renice on real process (find PID, execute, verify priority) | 1 test | ✅ Done |
| Privileged: full renice + rollback cycle (requires root/CAP_SYS_NICE) | 1 test | ✅ Done |

---

## STEP 3 — Desktop Application + Evaluation — STATUS: MOSTLY COMPLETE (~90%)

> Goal: Make the system demoable and produce real, defensible evaluation numbers.

### 3.1 Desktop Application (`step3/src/app.py`) — PyQt6 + pyqtgraph, 5 tabs

| Tab | Feature | Status |
|-----|---------|--------|
| **Live View** | Real-time stream of eBPF metrics refreshed every 2s | ✅ Built & tested |
| **Metrics & Visualization** | Time-series chart per metric (rolling 60s window, dropdown selector) | ✅ Built & tested |
| **History** | Incident log — score, XAI explanation, action, outcome (green=resolved / red=failed), rollback | ✅ Built & tested |
| **AI Insights & Suggestions** | Threat gauge, natural-language XAI, decision-gate state, manual-action approval button | ✅ Built & tested |
| **System & Controls** | Observer/Autonomous mode toggle, action threshold slider, clear-incident-history | ✅ Built & tested |
| **Real-time Popups** | System-tray popups + in-app notification feed for new incidents & resolved/failed outcomes | ✅ Added |

Technology: PyQt6 + pyqtgraph (reads the same SQLite DB; no extra logic).
`pyqtgraph` is optional at runtime — the dashboard degrades gracefully with a
clear message if it is missing. Dependencies pinned in `requirements.txt`.

### 3.2 Formal Evaluation (Precision / Recall / F1)

| Evaluation Task | Status |
|----------------|--------|
| Fault-injection suite with ground-truth-labelled windows | ✅ Harness `step3/scripts/evaluate.py` |
| Compute Precision, Recall, F1 | ✅ Done (see below) |
| Measure Time-to-Detection | ✅ Done (all faults caught on first window = "early") |
| Measure False-Positive Rate on normal workloads | ✅ Done |
| Document results | ✅ `step3/EVALUATION.md` + `step3/results/eval_results.json` |

Synthetic fault-injection results (replay over labelled corpus, 120 normal + 20
fault + 10 recovery windows per class):

| Fault | Precision | Recall | F1 | Time-to-Detection | FPR |
|-------|-----------|--------|-----|-------------------|-----|
| cpu (CPU starvation)    | 0.870 | 1.000 | 0.930 | early (+0s) | 0.100 |
| io (I/O degradation)    | 0.909 | 1.000 | 0.952 | early (+0s) | 0.067 |
| memory (memory-pressure) | 0.952 | 1.000 | 0.976 | early (+0s) | 0.033 |

The same harness reads live `__phase__` markers written by
`run_experiment.sh`, so hardware numbers can be produced on the deployment VM
with no code change (`python3 step3/scripts/evaluate.py --db
step1/data/experiment.db`).

### 3.3 Demo Preparation

| Demo Scenario | Status |
|--------------|--------|
| Memory leak process -> memory-pressure detection + process restart | ✅ Rehearsed in E2E; restart action unit-tested |
| CPU hog / stress-ng -> scheduler-latency detection + priority throttling | ✅ Rehearsed in E2E; renice + rollback unit-tested (incl. privileged) |
| Throttled disk load -> I/O-degradation detection + ionice | ✅ Rehearsed in E2E; ionice action unit-tested |
| IRQ anomaly -> suggestion-only (no automated module reload) | ⚠️ Suggestion-only by design |
| Retraining before/after comparison | ✅ Retraining loop verified on real DB (see fixes below) |

---

## Fixes & Improvements Logged (August 18, 2026)

1. **Retraining validation gate bug fixed.** The gate required a retrained model
   to flag 1–5% of a *known-normal* holdout, but a healthy model flags ~0% of
   normal data — so every retrain was rejected (`model_versions` v2/v3 in the
   DB both logged `flag rate 0.000 too low`). The gate now accepts a 0% normal
   flag rate and instead rejects candidates that (a) cry wolf on normal data
   (`flag rate > 5%`) or (b) fail to re-detect the system's own past incidents
   (must flag ≥30% of them). Verified on production data: the candidate is now
   accepted and still detects 66.7% of the 75 stored incidents.
2. **Decision-gate "stale alarm" bug fixed.** Confidence-driven alarms are now
   confirmed against the *current* metric state (`MAG_CONFIRM`, 2σ on a live
   `cur` feature). Previously, lag/rolling features kept the anomaly score
   elevated for ~a minute after a fault healed, so a recovered system kept
   alarming. In the synthetic evaluation this cut false positives by ~70% (e.g.
   CPU FPs 10→3, FPR 0.33→0.10) without missing a single fault.
3. **Step 3 dashboard robustness.** `pyqtgraph` is now an optional runtime
   dependency (graceful degradation + in-UI message); real-time system-tray
   popups + in-app notification feed added (plan §2.9/§3.1 popups requirement).
4. **Formal evaluation harness added** (`step3/scripts/evaluate.py`).
5. **`requirements.txt` added** and doc paths corrected (`UI_Testing.md` now
   points at `step1/src/collector.py`).

---

## Summary

| Step | Description | Completion |
|------|-------------|------------|
| Step 1 | eBPF data pipeline: kernel probes -> collector -> SQLite time-series | ✅ 100% |
| Step 2 | AI brain: feature engineering -> Isolation Forest -> XAI -> decision gate -> action engine + rollback + retraining | ✅ 100% |
| Step 3 | Desktop app (5 tabs + popups) + formal evaluation (P/R/F1, TTFD, FPR) | 🟡 ~90% |
| **Overall** | | **~88%** |

---

## What Needs to Be Done Next (Priority Order)

1. **[HIGH] Live-VM evaluation numbers** — run `sudo bash
   step1/scripts/run_experiment.sh` + `step3/scripts/evaluate.py --db
   step1/data/experiment.db` on the VMware deployment to replace the synthetic
   evidence table with hardware-accurate numbers (harness is ready, no code
   change required).
2. **[MEDIUM] Commit `step3/` + `UI_Testing.md`** — the entire Step 3 work is
   still untracked in git and should be committed.
3. **[MEDIUM] Demo rehearsal polish** — rehearse the 4-terminal demo
   (collector → UI → brain → fault injector) back-to-back and fix any rough
   edges; verify tray popups appear on the target desktop session.
4. **[LOW] IRQ automated action** — implement safe kernel-module reload to
   complete the 4th action class (currently suggestion-only).
5. **[LOW] libbpf / CO-RE port** — port bpftrace scripts to native libbpf +
   CO-RE for kernel-version portability. Not required for the hackathon build.

---

## Repository Structure

```
SElf-Healing-OS/
├── README.md
├── .gitignore
├── requirements.txt                    # runtime dependencies
├── AI_Self_Healing_OS.md                  # Full design document
├── Self_Healing_OS_Implementation_Plan.md # Detailed 3-step build plan
├── PROGRESS.md                            # This file
├── UI_Testing.md                          # Frontend + integration handoff
│
├── step1/                                 (DATA PIPELINE — Complete)
│   ├── bpftrace/
│   │   ├── sched.bt                       # Scheduler eBPF probe
│   │   ├── memory.bt                      # Memory eBPF probe
│   │   ├── block.bt                       # Block I/O eBPF probe
│   │   ├── irq.bt                         # IRQ eBPF probe
│   │   └── diagnostics/histograms.bt
│   ├── src/
│   │   ├── collector.py                   # Userspace bpftrace -> SQLite collector
│   │   └── db.py                          # SQLite schema + insert helpers
│   ├── scripts/
│   │   ├── loadgen.py                     # Controlled load generator
│   │   └── run_experiment.sh              # Experiment runner (+ __phase__ markers)
│   └── data/                              # Runtime SQLite DBs (gitignored)
│
├── step2/                                 (AI BRAIN — Complete)
│   ├── src/
│   │   ├── brain.py                       # Main orchestrator (replay + live loop)
│   │   ├── feature_engine.py              # 108-dim feature vector per 5s window
│   │   ├── model.py                       # Isolation Forest + Retrainer (+ validation gate)
│   │   ├── trend.py                       # Linear trend / early-warning layer
│   │   ├── explain.py                     # Native + SHAP XAI + NL templates + similarity
│   │   ├── action_engine.py               # renice / ionice / restart + rollback
│   │   └── config.py                      # All hyperparameters in one place
│   ├── scripts/
│   │   ├── fault_inject.py                # CPU / memory / I/O fault injectors
│   │   ├── run_brain.py                   # CLI entry point
│   │   └── run_e2e_demo.py                # End-to-end demo script
│   └── tests/
│       └── test_step2.py                  # 21 tests covering all Step 2 exit criteria
│
└── step3/                                 (OBSERVABILITY + EVALUATION — ~90%)
    ├── src/
    │   └── app.py                         # PyQt6 + pyqtgraph 5-tab dashboard + popups
    ├── scripts/
    │   └── evaluate.py                    # P/R/F1, time-to-detection, FPR harness
    ├── results/
    │   └── eval_results.json              # Generated evaluation numbers
    ├── EVALUATION.md                      # Methodology + results table
    └── assets/                            # (icon/asset dir)
```
