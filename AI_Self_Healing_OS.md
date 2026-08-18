# Autonomous Fault Detection and Self-Healing Operating System

## Track 2 — AI Usage at Operating System & Kernel Level

---

# Part 1: The Idea, in Plain Words

## The problem with how most systems watch themselves today

Most monitoring tools — including most "AI-powered" ones people will build for this track — watch the **wrong layer**. They read log files, dmesg output, and application logs. The problem is that logs are written *after* something has already gone wrong, and a lot of real failures never make it into a log line at all until it's too late.

Our approach flips this: instead of reading what the system *says* happened, we watch what the **kernel is actually doing**, live, as it happens — memory allocation, process scheduling, disk I/O, interrupts — using a technology called **eBPF**. Think of it as the difference between reading a hospital's discharge paperwork versus watching the patient's vitals monitor in real time.

## What eBPF actually gives us

eBPF lets us safely attach small programs directly inside the running Linux kernel, at almost zero overhead, to observe events as they happen: every time a process is scheduled, every page fault, every disk request, every interrupt. No log file needed — we're watching the kernel's own internal bookkeeping.

## Common issues this approach can catch — and how

### Example 1: The silent memory leak
**What normally happens:** An application slowly leaks memory over hours. Nothing looks wrong until the kernel's OOM killer suddenly kills a random process (maybe the wrong one), and the user just sees "why did my app crash?" with no warning.

**How our system helps:** eBPF continuously tracks page fault rates and memory pressure per process. Our AI model isn't just checking "is memory full right now" — it's watching the **trend**. If memory pressure has been climbing steadily for the last few minutes, the system flags this *before* the OOM killer has to step in, explains which process is responsible and how fast it's growing, and can proactively restart that process or apply a memory limit — with the option to roll that action back if it turns out to be wrong.

### Example 2: A process is starving the CPU
**What normally happens:** A runaway or buggy process hogs the CPU. The system becomes sluggish. Logs might show nothing unusual because the process itself isn't crashing — it's just being greedy.

**How our system helps:** eBPF watches scheduler events directly — run-queue latency, how long processes wait for CPU time, involuntary context switches. A spike here is invisible to log-based monitoring but immediately visible at the kernel level. Our system detects the anomaly, explains "process X has held the CPU 8x longer than its historical baseline," and can lower its priority or throttle it — again, reversibly.

### Example 3: A slow, failing disk or driver
**What normally happens:** A disk or driver starts responding slowly or intermittently before it fully fails. Traditional monitoring only notices once I/O errors start appearing in logs — by which point the user has likely already experienced hangs or data loss risk.

**How our system helps:** Block I/O latency and retry counts are tracked directly through eBPF. A gradual increase in latency or retries is a leading indicator of failure. The system flags this early, explains what's degrading, and can take protective action (e.g., remounting, adjusting I/O scheduling, or alerting before a full failure).

### Example 4: An IRQ storm or misbehaving driver
**What normally happens:** A driver keeps firing interrupts abnormally, silently eating CPU and degrading everything else on the system, without a single clean log line pointing at the cause.

**How our system helps:** IRQ handler entry/exit events are tracked at the kernel level, so this pattern is directly visible as it emerges, not inferred from side effects.

## Why the AI matters here, not just the monitoring

Watching kernel data alone still leaves a human to interpret "is this normal or not," and normal looks different on every machine and every workload. Our AI model (Isolation Forest, an unsupervised anomaly detection model) learns what "normal" looks like *for this specific system*, flags genuine deviations, and — critically — **explains why** it flagged something, in plain language, rather than just throwing an alert. It doesn't just say "anomaly detected" — it says "memory page-fault rate is 3.2x the recent baseline, correlated with process `xyz`, similar to an issue seen 4 times before that was resolved by restarting the process."

## Why "self-healing" and not just "self-alerting"

Most similar projects stop at detection and notification. We go further: every time the system takes a corrective action, it **records the outcome** — did it work, did it fail, did we have to try something else — and uses that history to (a) get better at deciding *when* to act automatically versus just alerting, and (b) always have a **rollback path** ready in case an automated action makes things worse. Nothing is done blindly or irreversibly.

---

# Part 2: Technical Design and Implementation Plan

## 2.1 High-level architecture

```
eBPF probes  →  ring buffer  →  feature engine  →  Isolation Forest  →  decision gate  →  action engine (+ rollback)
                                        ↑                                                          │
                                        └──────────────── outcome log / retrain (every 200) ◄───────┘
```

All components run inside a Linux VM (VMware), with a companion desktop application acting as the observability + control surface (Task-Manager-style live view, metrics, history, AI explanations).

## 2.2 Data collection layer (eBPF)

**Toolchain:** libbpf + BPF CO-RE (Compile Once – Run Everywhere) for portability across kernel versions, or `bpftrace` for faster prototyping during early development. Python (BCC) can be used for the userspace glue if time is tight.

**Probes/tracepoints to instrument (kept deliberately narrow — do not hook "everything"):**

| Subsystem | Tracepoint / kprobe | What it captures |
|---|---|---|
| Scheduling | `sched_switch`, `sched_wakeup` | run-queue latency, involuntary context switches, per-process CPU wait time |
| Memory | `mm_page_fault`, `oom_kill_process` | page fault rate, memory pressure, OOM events |
| Block I/O | `block_rq_issue`, `block_rq_complete` | I/O latency, retry/error counts per device |
| Interrupts | `irq_handler_entry`, `irq_handler_exit` | IRQ frequency and handling latency (proxy for driver/hardware health in a VM) |

**In-kernel aggregation:** use BPF maps (hash maps / per-CPU arrays) to pre-aggregate counts and latencies *inside* the kernel before sending anything to userspace — this keeps overhead low and avoids flooding the ring buffer with raw per-event data.

**Transport:** `BPF_MAP_TYPE_RINGBUF` pushing aggregated snapshots to userspace every 1–5 seconds.

## 2.3 Storage layer

Store metrics as an actual **time series**, not flat unordered log rows, so both live inference and later analysis/replay use the same structure.

- **Engine:** SQLite for the hackathon build (simple, zero-ops, easy to demo/debug); schema designed so it could later swap to InfluxDB/TimescaleDB without changing the ingestion logic.
- **Schema (conceptual):**
  - `metrics(metric_name TEXT, timestamp REAL, value REAL, source TEXT)`
  - `windows(window_id, start_ts, end_ts, feature_vector JSON)`
  - `incidents(incident_id, timestamp, anomaly_score, explanation TEXT, action_taken TEXT, outcome TEXT, rollback_used BOOLEAN)`
  - `model_versions(version_id, trained_at, n_samples, validation_result, active BOOLEAN)`

## 2.4 Feature engineering

Per metric, per time window (e.g. 5–10 second windows):

- **Point statistics:** mean, standard deviation, min/max, current value
- **Trend features:** slope over multiple horizons (30s, 2min, 5min) — this is what lets the system talk about a *trend*, not just a single spike
- **EWMA residual:** `actual − EWMA(metric)` — cheap way to detect deviation from recent behavior rather than absolute magnitude
- **Lag features:** t-1, t-2, t-3 windows, so the model implicitly sees short-term trajectory

Final feature vector = concatenation of the above across all tracked metrics (scheduling, memory, I/O, IRQ), one row per window.

## 2.5 ML model and training methodology

### 2.5.1 Primary model: Isolation Forest

- **Why:** unsupervised, works well on multivariate numeric data, doesn't require labeled failure data (which we won't have in volume), computationally cheap enough to retrain frequently on a live system.
- **Library:** scikit-learn (`sklearn.ensemble.IsolationForest`)
- **Key hyperparameters:**
  - `n_estimators`: 100 (default is a reasonable starting point; tune via validation)
  - `contamination`: set low (0.01–0.05) since real faults should be rare in the training window
  - `max_samples`: `'auto'` initially, tune based on window size
- **Scoring:** use `decision_function()` (continuous anomaly score) rather than only `predict()` (binary), so the decision gate can threshold on confidence instead of a hard yes/no.

### 2.5.2 Bootstrapping ("baseline") training

1. Collect a baseline dataset by running the VM under a variety of **normal** workloads (idle, light browsing/dev tasks, moderate CPU/I/O load) for a few hours to a day.
2. Train the initial Isolation Forest on this baseline so "normal" is defined by the system's own real behavior, not a generic assumption.

### 2.5.3 Fault-injection dataset (for evaluation, not training)

Because Isolation Forest is unsupervised, we still need a way to measure **accuracy** (precision/recall), which requires *some* ground truth. Solve this by deliberately injecting known failure conditions and labeling those windows as "should be anomalous":

- Memory leak simulation → memory-hogging test process
- CPU starvation → `stress-ng --cpu` or a controlled fork bomb (in an isolated VM)
- I/O slowdown → `stress-ng --io` / throttled disk via cgroups
- Scheduler pressure → many competing processes at once

Use these injected runs to compute:
- **Precision / Recall / F1** — how many true faults are actually caught, and how many false alarms are raised
- **Time-to-detection** — how many seconds/windows before the fault "matures" does the system flag it (this is your evidence for the "predicts failures" claim, not just "detects" them)
- **False positive rate on normal workloads** — critical, since a system that cries wolf will never be trusted to self-heal

### 2.5.4 Auto-retraining (every 200 entries)

1. Accumulate incoming feature windows in a buffer.
2. When the buffer reaches 200 new entries, trigger retraining — but train on a **sliding window of recent history** (e.g. last 1000–2000 windows), not just the newest 200, to avoid catastrophic forgetting of what "normal" looks like.
3. **Preserve time order** when retraining — do not shuffle, since the system should adapt to genuine drift in normal behavior (e.g. workload changes over the day), not treat all history as interchangeable.
4. **Validation gate before swapping models:** run the newly trained model against a small held-out set of recent known-normal windows. If it flags a sane proportion (e.g. 1–5%) as anomalous, promote it as the active model; if it flags almost everything or almost nothing, reject it and keep the previous model, logging the reason. This prevents a noisy data batch from silently degrading detection quality.
5. Log every model version (`model_versions` table) with training size, timestamp, and validation result — this becomes part of what the desktop app's "AI views" tab can display.

### 2.5.5 Secondary model: lightweight trend/forecast layer

To strengthen the "predicts failures" claim beyond anomaly scoring alone:

- Simple linear regression or Holt's linear trend method on the last N points per metric, projected 1–2 minutes forward.
- If the projected value crosses a defined threshold, this raises an early warning **independent of** the Isolation Forest anomaly score — giving two complementary signals (a) "this is already unusual" and (b) "this is heading toward being unusual."
- Deliberately avoiding LSTM/deep time-series models for this build — they need more data and tuning than a hackathon timeline allows, and are much harder to explain to judges than "the trend line crossed the threshold."

## 2.6 Explainable AI (XAI) methods

Detection without explanation is not enough for a self-healing system that takes autonomous action — every action needs a human-readable justification.

1. **Feature-level attribution from Isolation Forest itself:** Isolation Forest isolates anomalies via shorter average path lengths across its trees. By tracking which *feature* was responsible for the early splits that isolated a given point, we can rank features by how much they contributed to the anomaly score — this is native to the model, cheap to compute, no extra library needed.
2. **SHAP (SHapley Additive exPlanations)** as a second, more rigorous option: apply `shap.TreeExplainer` (or `KernelExplainer` as a fallback, since Isolation Forest support varies by SHAP version) to the model's decision function to get per-feature contribution values for a given flagged window. This gives a more standard, defensible explanation method to cite to judges.
3. **Natural-language templating:** convert the top contributing features into a sentence, e.g.:
   *"Memory page-fault rate is 3.2x above baseline, correlated with process `xyz`; this is the primary driver of the anomaly score."*
   Templates are filled from the ranked feature attributions rather than hand-written per case.
4. **Historical similarity lookup:** store the feature vector of every past incident. When a new anomaly is flagged, do a nearest-neighbor search (simple cosine or Euclidean distance) against past incidents. If a close match exists, surface it: *"This looks similar to incident #45, which was resolved by restarting the process (successful 3 of the last 4 times)."* This is a strong, simple way to combine explainability with the self-learning history, and doubles as evidence for how confident the system should be in a given recovery action.

## 2.7 Decision gate

Before any action is taken:

- Anomaly score must exceed a confidence threshold.
- Below threshold → log and surface as a **suggestion** in the desktop app, do not act automatically.
- Above threshold → proceed to the action engine.
- Threshold is itself tunable and can be informed by the historical success rate of past actions for similar incidents (see 2.6.4).

## 2.8 Action engine and rollback

Every possible recovery action is registered with a **paired rollback procedure**, executed only after a pre-action state snapshot is stored:

| Detected condition | Automated action | Rollback |
|---|---|---|
| Memory leak trend | Restart offending process / apply cgroup memory limit | Store previous cgroup limits; remove limit / do not restart again if it recurs immediately |
| CPU starvation | Lower `nice`/priority of offending process | Store previous priority value; restore it |
| I/O degradation | Adjust I/O scheduler / remount | Store previous scheduler/mount options; revert |
| Driver/IRQ anomaly | Reload kernel module with safe parameters | Keep previous module parameters; reload with them if new config causes further errors |

**Post-action monitoring window:** after acting, watch the relevant metrics for ~30 seconds.
- Metrics improve → mark the incident as **resolved**, log success.
- Metrics worsen or a new anomaly fires → **automatic rollback**, mark as **failed**, log for later analysis.

**Safety rules (explicitly built in, since this is the part judges will stress-test hardest):**
- Rate limiting: never repeat the same failed action on the same target within a cooldown window (avoid action loops).
- Hard action allowlist: the action engine can only ever perform pre-registered, reversible actions — no arbitrary shell execution.
- Every action, success or failure, is written to the outcome log — this is the same data source used for retraining (2.5.4) and for the historical similarity lookup (2.6.4).

## 2.9 Desktop application

A Task-Manager-style companion app with the following tabs:

- **Live view:** real-time stream of the raw metrics eBPF is capturing (scheduling, memory, I/O, IRQ), refreshed on the same cadence as the ring buffer flush.
- **Metrics & visualization:** time-series charts per metric with trend lines, so the "before/after" of a self-heal event is visually obvious.
- **History:** the incident/outcome log — what was detected, what action was taken, success/failure, rollback used or not. Doubles as the audit trail and the training data source, shown transparently to build trust.
- **AI views / suggestions:** current anomaly score, top contributing features (from 2.6.1/2.6.2), the natural-language explanation, similar past incidents, and — when confidence is below the action threshold — a suggested-but-not-yet-taken action the user can approve manually.
- **Popups:** real-time notification whenever the system detects an issue or completes/fails a recovery action, with the plain-language explanation attached.

## 2.10 Demo plan (VMware constraints)

Since VMware limits real hardware-fault injection, plan the live demo around controllable, repeatable failure scenarios:
- Memory leak process → triggers memory-pressure detection and process restart/limit
- `stress-ng`/fork bomb → triggers scheduler-latency detection and priority throttling
- Throttled/simulated disk load → triggers I/O-degradation detection
- A dummy/test kernel module → simulate a misbehaving "driver" to demonstrate IRQ-anomaly detection and safe module reload

For the self-training loop, show **real** outcome logging live, but present the *retraining* step as a clear before/after comparison (validation metrics of old vs. new model) rather than attempting a fragile live retrain on-stage with too little data.
