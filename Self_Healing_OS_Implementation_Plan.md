# Autonomous Fault Detection and Self-Healing OS — Full Technical Implementation Plan

This document expands the 3-step build plan into complete, detailed technical instructions. It covers every component from the original design: eBPF data collection, storage schema, feature engineering, ML training/retraining, explainability, the decision gate, the action/rollback engine, the desktop app, and the evaluation/demo methodology.

---

## Overview of the 3 Build Steps

1. **Step 1 — Data Pipeline:** kernel (eBPF) → ring buffer → userspace collector → SQLite time-series storage.
2. **Step 2 — The Brain:** feature engineering → Isolation Forest anomaly detection → explainability → decision gate → action engine with rollback → retraining loop.
3. **Step 3 — Desktop App + Evaluation:** Task-Manager-style UI over the same data, plus fault-injection testing to produce real precision/recall/F1 numbers, plus demo rehearsal.

Each step should be fully working end-to-end before starting the next. Do not parallelize prematurely — a broken pipeline invalidates everything built on top of it.

---

# STEP 1 — Data Pipeline (Kernel → Storage)

**Goal of this step:** prove real kernel-level signal can be captured, aggregated cheaply, and persisted as a proper time series — before any ML is involved.

## 1.1 Environment setup

- Provision a Linux VM under VMware. Use a recent Ubuntu LTS release with a kernel that has **BTF (BPF Type Format)** support enabled, since this is required for CO-RE (Compile Once – Run Everywhere) portability later.
- Install prerequisites:
  - `bpftrace` (for fast prototyping)
  - `bcc-tools` and `python3-bpfcc` (Python/BCC bindings, useful as userspace glue)
  - `clang`, `llvm`, `libbpf-dev`, `linux-headers-$(uname -r)` (needed once you move to libbpf + CO-RE)
  - `sqlite3` and Python's `sqlite3` module (standard library, no install needed)
- Verify BTF availability: check for `/sys/kernel/btf/vmlinux`. If absent, you'll need `pahole`-generated BTF or a kernel build that includes it.

## 1.2 Prototype with bpftrace first

Do **not** start with raw libbpf + CO-RE C code — it's slow to iterate on. Start with `bpftrace` scripts for each of the four subsystems, since bpftrace lets you test a tracepoint in seconds instead of a full compile/load/attach cycle.

### Probes/tracepoints to instrument (deliberately narrow — do not hook "everything")

| Subsystem | Tracepoint / kprobe | What it captures |
|---|---|---|
| Scheduling | `sched_switch`, `sched_wakeup` | run-queue latency, involuntary context switches, per-process CPU wait time |
| Memory | `mm_page_fault`, `oom_kill_process` | page fault rate, memory pressure, OOM events |
| Block I/O | `block_rq_issue`, `block_rq_complete` | I/O latency, retry/error counts per device |
| Interrupts | `irq_handler_entry`, `irq_handler_exit` | IRQ frequency and handling latency (proxy for driver/hardware health in a VM) |

For each subsystem, write a small bpftrace script that:
- Attaches to the relevant tracepoint(s).
- Extracts the fields you need (PID, timestamp, duration, device, etc.).
- Aggregates using bpftrace's built-in map types (`@`, `count()`, `hist()`, `avg()`) rather than printing every raw event.

Example shape (illustrative, not exhaustive) for scheduling latency: track timestamp on `sched_wakeup`, compute delta on the matching `sched_switch`, bucket by PID.

### In-kernel aggregation

This is critical for performance and for keeping the ring buffer from flooding:

- Use **BPF maps** — hash maps for per-process/per-device counters, per-CPU arrays for high-frequency counters (e.g., interrupts) to avoid lock contention across cores.
- Pre-aggregate (counts, running sums for averages, min/max) **inside the kernel**, so userspace receives compact snapshots, not a firehose of raw per-event records.
- Flush aggregated snapshots from kernel to userspace via `BPF_MAP_TYPE_RINGBUF`, on a fixed cadence of **1–5 seconds**.

### Milestone check for this sub-step

Run the VM idle, then run `stress-ng --cpu` or a memory-hogging test process, and confirm the aggregated bpftrace output visibly changes (higher page fault rate, higher scheduler latency, etc.). Do not proceed further until this is confirmed — every downstream component depends on this signal being real and correctly aggregated.

## 1.3 Userspace collector

- Write a Python collector (using BCC's Python bindings, or bpftrace's JSON output mode piped into a Python process) that:
  - Reads aggregated snapshots from the ring buffer/bpftrace output every 1–5 seconds.
  - Parses them into structured records.
  - Writes them into SQLite immediately, so nothing is held only in memory.

## 1.4 Storage layer (SQLite)

Store metrics as an actual **time series**, not flat/unordered rows, so both live inference (Step 2) and later analysis/replay use the same structure.

- **Engine:** SQLite for the hackathon build — zero-ops, trivial to inspect/debug, good enough for demo-scale write volume.
- **Design principle:** structure the schema so it could later be swapped to InfluxDB/TimescaleDB **without changing the ingestion logic** — i.e., keep ingestion as simple inserts against a stable schema shape, not SQLite-specific tricks.

### Schema (conceptual)

```sql
-- Raw/aggregated metric points
CREATE TABLE metrics (
    metric_name TEXT,
    timestamp   REAL,
    value       REAL,
    source      TEXT   -- e.g. process name/PID, device name, IRQ line
);

-- Feature windows (populated in Step 2, but table created now)
CREATE TABLE windows (
    window_id      INTEGER PRIMARY KEY,
    start_ts       REAL,
    end_ts         REAL,
    feature_vector JSON
);

-- Incidents (populated in Step 2, but table created now)
CREATE TABLE incidents (
    incident_id    INTEGER PRIMARY KEY,
    timestamp      REAL,
    anomaly_score  REAL,
    explanation    TEXT,
    action_taken   TEXT,
    outcome        TEXT,
    rollback_used  BOOLEAN
);

-- Model version history (populated in Step 2, but table created now)
CREATE TABLE model_versions (
    version_id        INTEGER PRIMARY KEY,
    trained_at         REAL,
    n_samples          INTEGER,
    validation_result  TEXT,
    active             BOOLEAN
);
```

Create all four tables now, even though `windows`, `incidents`, and `model_versions` stay empty until Step 2 — this avoids schema-migration churn later and lets Step 2 start writing immediately.

## 1.5 Step 1 exit criteria (do not proceed to Step 2 until all are true)

- [ ] bpftrace scripts run cleanly for all four subsystems (scheduling, memory, block I/O, IRQ).
- [ ] In-kernel aggregation confirmed working (counters/histograms populate correctly, not raw per-event spam).
- [ ] Ring buffer flush cadence (1–5s) confirmed stable under load.
- [ ] Userspace collector correctly parses and inserts into SQLite `metrics` table.
- [ ] Manual load test (idle vs. `stress-ng`) shows visibly different, correct values in SQLite.
- [ ] All four tables exist in the SQLite schema.

## 1.6 Later hardening (can be deferred to end of project)

- Port the validated bpftrace probes to **libbpf + BPF CO-RE** for portability across kernel versions — this is a "nice to have" and can be the last thing finished. A bpftrace-only build is a defensible hackathon submission on its own.
- If time is extremely tight, Python/BCC glue is an acceptable substitute for hand-written libbpf loader code.

---

# STEP 2 — The Brain (Feature Engineering → ML → Explainability → Decision Gate → Action Engine)

**Goal of this step:** turn raw stored metrics into a detected, explained, and safely-acted-upon incident, entirely through the SQLite tables — no UI required yet.

## 2.1 Feature engineering

Compute features **per metric, per time window** (5–10 second windows):

- **Point statistics:** mean, standard deviation, min, max, current value.
- **Trend features:** slope over multiple horizons — 30s, 2min, 5min — so the system can talk about a *trend*, not just react to a single spike.
- **EWMA residual:** `actual − EWMA(metric)` — a cheap way to detect deviation from recent behavior rather than absolute magnitude.
- **Lag features:** t-1, t-2, t-3 windows, so the model implicitly sees short-term trajectory without needing a recurrent architecture.

**Final feature vector** = concatenation of all the above across all tracked metrics (scheduling, memory, I/O, IRQ) → one row per window, written into the `windows` table (`feature_vector` as JSON) alongside `start_ts`/`end_ts`.

## 2.2 Primary ML model: Isolation Forest

- **Why this model:** unsupervised (no labeled failure data needed in volume), works well on multivariate numeric data, computationally cheap enough to retrain frequently on a live system.
- **Library:** `sklearn.ensemble.IsolationForest`.
- **Key hyperparameters:**
  - `n_estimators`: start at 100, tune via validation.
  - `contamination`: set low, **0.01–0.05**, since real faults should be rare within any training window.
  - `max_samples`: start at `'auto'`, tune based on window size.
- **Scoring:** use `decision_function()` — a **continuous** anomaly score — rather than only `predict()` (binary), so the decision gate (2.5) can threshold on confidence instead of a hard yes/no.

### 2.2.1 Bootstrapping ("baseline") training

1. Collect a baseline dataset by running the VM under a variety of **normal** workloads (idle, light browsing/dev tasks, moderate CPU/I/O load) for a few hours to a day.
2. Train the initial Isolation Forest on this baseline, so "normal" is defined by the system's own real behavior — not a generic, hand-picked assumption.

### 2.2.2 Fault-injection dataset (for evaluation, not training)

Because Isolation Forest is unsupervised, you still need *some* ground truth to measure accuracy. Deliberately inject known failure conditions and label those windows as "should be anomalous":

- **Memory leak simulation** → a memory-hogging test process.
- **CPU starvation** → `stress-ng --cpu` or a controlled fork bomb (isolated VM only).
- **I/O slowdown** → `stress-ng --io`, or a throttled disk via cgroups.
- **Scheduler pressure** → many competing processes launched simultaneously.

Use these injected runs later (Step 3) to compute precision/recall/F1, time-to-detection, and false-positive rate — see Step 3 for details. Keep this dataset separate from training data.

## 2.3 Auto-retraining (every 200 new entries)

1. Accumulate incoming feature windows in a buffer.
2. When the buffer reaches **200 new entries**, trigger retraining — but train on a **sliding window of recent history** (e.g., last 1000–2000 windows), not just the newest 200, to avoid catastrophic forgetting of what "normal" looks like.
3. **Preserve time order** when retraining — do not shuffle. The system should adapt to genuine drift in normal behavior (e.g., workload changes over the course of a day), not treat all history as interchangeable.
4. **Validation gate before swapping models:** run the newly trained model against a small held-out set of recent known-normal windows.
   - If it flags a sane proportion (**1–5%**) as anomalous → promote it as the active model.
   - If it flags almost everything or almost nothing → reject it, keep the previous model active, and log the rejection reason. This prevents one noisy data batch from silently degrading detection quality.
5. Log every model version into `model_versions`: training size, timestamp, validation result, active flag. This feeds the desktop app's "AI views" tab (Step 3).

## 2.4 Secondary model: lightweight trend/forecast layer

To strengthen the "predicts failures" claim beyond anomaly scoring alone:

- Use **simple linear regression** or **Holt's linear trend method** on the last N points per metric, projected 1–2 minutes forward.
- If the projected value crosses a defined threshold, raise an early warning **independent of** the Isolation Forest anomaly score.
- This gives two complementary signals: (a) "this is *already* unusual" (Isolation Forest) and (b) "this is *heading toward* being unusual" (trend forecast).
- **Deliberately avoid LSTM/deep time-series models** — they need more data and tuning than a hackathon timeline allows, and are much harder to justify/explain to judges than "the trend line crossed the threshold."

## 2.5 Explainable AI (XAI) — implement in this order of increasing effort

Detection without explanation isn't enough for a system that takes autonomous action — every action needs a human-readable justification. Build these in order so you always have a working fallback if time runs out:

1. **Feature-level attribution native to Isolation Forest.** Isolation Forest isolates anomalies via shorter average path lengths across its trees. Track which *feature* was responsible for the early splits that isolated a given point, and rank features by contribution to the anomaly score. This is native to the model, cheap to compute, and needs no extra library.
2. **SHAP (SHapley Additive exPlanations)** as a more rigorous second layer. Apply `shap.TreeExplainer` (fall back to `KernelExplainer` if `TreeExplainer` support for Isolation Forest is unavailable in your installed SHAP version) to the model's decision function, producing per-feature contribution values for a given flagged window. This is a standard, defensible method to cite to judges.
3. **Natural-language templating.** Convert the top contributing features (from step 1 or 2 above) into a sentence, filled from ranked feature attributions rather than hand-written per case. Example shape:
   > "Memory page-fault rate is 3.2x above baseline, correlated with process `xyz`; this is the primary driver of the anomaly score."
4. **Historical similarity lookup.** Store the feature vector of every past incident. When a new anomaly is flagged, run a nearest-neighbor search (cosine or Euclidean distance) against past incidents. If a close match exists, surface it, e.g.:
   > "This looks similar to incident #45, which was resolved by restarting the process (successful 3 of the last 4 times)."
   This combines explainability with the self-learning history, and doubles as evidence for how confident the system should be in a given recovery action.

## 2.6 Decision gate

Before any action is taken:

- The anomaly score must exceed a confidence threshold.
- **Below threshold** → log the finding and surface it as a **suggestion** in the desktop app; do **not** act automatically.
- **Above threshold** → proceed to the action engine.
- The threshold itself is tunable, and can be informed by the historical success rate of past actions for similar incidents (via the nearest-neighbor lookup in 2.5.4).

## 2.7 Action engine and rollback

Every possible recovery action is registered with a **paired rollback procedure**, and is executed only after a **pre-action state snapshot** is stored.

| Detected condition | Automated action | Rollback |
|---|---|---|
| Memory leak trend | Restart offending process / apply cgroup memory limit | Store previous cgroup limits; remove limit / do not restart again if it recurs immediately |
| CPU starvation | Lower `nice`/priority of offending process | Store previous priority value; restore it |
| I/O degradation | Adjust I/O scheduler / remount | Store previous scheduler/mount options; revert |
| Driver/IRQ anomaly | Reload kernel module with safe parameters | Keep previous module parameters; reload with them if new config causes further errors |

### Post-action monitoring window

After acting, watch the relevant metrics for **~30 seconds**:

- Metrics improve → mark the incident as **resolved**, log success.
- Metrics worsen, or a new anomaly fires → **automatic rollback**, mark as **failed**, log for later analysis.

### Safety rules (build these in explicitly — judges will stress-test this hardest)

- **Rate limiting:** never repeat the same failed action on the same target within a cooldown window, to avoid action loops.
- **Hard action allowlist:** the action engine can only ever perform pre-registered, reversible actions — **no arbitrary shell execution**.
- **Complete outcome logging:** every action, success or failure, is written to the outcome log (`incidents` table). This is the same data source used for retraining (2.3) and for the historical similarity lookup (2.5.4).

## 2.8 Step 2 exit criteria (do not proceed to Step 3 until all are true)

- [ ] Feature engine produces correct rolling-window feature vectors from live `metrics` data.
- [ ] Baseline Isolation Forest trained on normal-workload data.
- [ ] `decision_function()` scores confirmed sane on both normal and injected-fault data.
- [ ] Decision gate correctly separates "suggestion only" vs. "trigger action" cases.
- [ ] Action engine executes at least one full action + rollback cycle correctly, with pre-action snapshot stored.
- [ ] Post-action monitoring window correctly marks incidents resolved/failed.
- [ ] At least the native Isolation Forest feature-attribution explanation is working end-to-end (SHAP and historical similarity can follow if time allows).
- [ ] Retraining loop triggers at 200 entries, respects time order, and the validation gate correctly rejects a deliberately bad model.
- [ ] Running a fault-injection script (memory leak, `stress-ng --cpu`, throttled I/O) results in: detect → explain → act → resolve/rollback → log, fully through SQLite, with no UI needed.

---

# STEP 3 — Desktop Application + Evaluation + Demo

**Goal of this step:** make the system demoable, and produce real, defensible evaluation numbers rather than only claims.

## 3.1 Desktop application

Build a Task-Manager-style companion app (Electron, or a Python/Qt app if you prefer staying in one language). Since this reads from the same SQLite database built in Steps 1–2, it is largely a UI layer over existing data, not new logic. Implement five tabs:

1. **Live view:** real-time stream of the raw metrics eBPF is capturing (scheduling, memory, I/O, IRQ), refreshed on the same cadence as the ring buffer flush (1–5s).
2. **Metrics & visualization:** time-series charts per metric with trend lines, so the before/after of a self-heal event is visually obvious.
3. **History:** the incident/outcome log — what was detected, what action was taken, success/failure, whether rollback was used. This doubles as the audit trail and the retraining data source, and should be shown transparently to build trust with judges/users.
4. **AI views / suggestions:** current anomaly score, top contributing features (from 2.5.1/2.5.2), the natural-language explanation, similar past incidents (from 2.5.4), and — when confidence is below the action threshold — a suggested-but-not-yet-taken action the user can approve manually.
5. **Popups:** real-time notifications whenever the system detects an issue or completes/fails a recovery action, with the plain-language explanation attached.

## 3.2 Evaluation methodology

Run the fault-injection suite from 2.2.2 for real, and compute:

- **Precision / Recall / F1** — how many true faults are actually caught, and how many false alarms are raised.
- **Time-to-detection** — how many seconds/windows before a fault "matures" the system flags it. This is the evidence for the "predicts failures" claim, not just "detects" them.
- **False positive rate on normal workloads** — critical, since a system that cries wolf will never be trusted to self-heal.

Document these numbers explicitly (e.g., in a results table) — this becomes your evidence slide for judges, not just a verbal claim.

## 3.3 Demo plan (VMware constraints)

Since VMware limits real hardware-fault injection, build the live demo around controllable, repeatable failure scenarios:

- **Memory leak process** → triggers memory-pressure detection and process restart/limit.
- **`stress-ng`/fork bomb** → triggers scheduler-latency detection and priority throttling.
- **Throttled/simulated disk load** → triggers I/O-degradation detection.
- **Dummy/test kernel module** → simulates a misbehaving "driver" to demonstrate IRQ-anomaly detection and safe module reload.

For the self-training loop: show **real** outcome logging live, but present the *retraining* step itself as a clear before/after comparison (validation metrics of old vs. new model) rather than attempting a fragile live retrain on-stage with too little data.

## 3.4 Step 3 exit criteria

- [ ] All five desktop app tabs functional and reading live from SQLite.
- [ ] Precision/Recall/F1, time-to-detection, and false-positive-rate numbers computed and documented from the fault-injection suite.
- [ ] Each of the four demo scenarios (memory leak, CPU starvation, I/O degradation, IRQ anomaly) runs reliably and repeatably.
- [ ] Retraining before/after comparison prepared as a static artifact for the demo, not a live on-stage retrain.
- [ ] Full run-through rehearsed at least once end-to-end without manual intervention beyond starting the injection scripts.

---

# Scope Protection Note

If time is genuinely tight, the step order above is also the cut line. A working Step 1 + Step 2 shown through terminal/Jupyter output instead of a full desktop app is still a strong, honest demo. A polished UI sitting on top of a fake or barely-working detection loop is the weaker failure mode — protect Steps 1 and 2 first, and treat the desktop app and formal evaluation as the parts to trim if the clock runs out.
