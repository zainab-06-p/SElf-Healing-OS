<p align="center">
  <h1 align="center">Self-Healing OS</h1>
  <p align="center">AI-powered Linux observability & autonomous healing dashboard</p>
  <p align="center">
    <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white" alt="Python">
    <img src="https://img.shields.io/badge/eBPF-bpftrace-orange" alt="eBPF">
    <img src="https://img.shields.io/badge/ML-Isolation%20Forest-green" alt="ML">
    <img src="https://img.shields.io/badge/UI-PyQt6-amber?logo=qt&logoColor=white" alt="PyQt6">
  </p>
</p>

---

## What It Does

Self-Healing OS is an autonomous fault-detection and recovery system for Linux. It watches the kernel in real time via **eBPF probes**, learns what "normal" looks like with **Isolation Forest** anomaly detection, explains every alarm in plain language (**Explainable AI**), and takes **reversible corrective actions** (renice / ionice / restart) — all through a modern **PyQt6 dashboard**.

**The core idea:** when your system starts degrading (CPU starvation, memory leaks, I/O bottlenecks), the system detects it, explains *why* it's happening, and fixes it automatically — with rollback if the fix doesn't work.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Linux Kernel (eBPF probes)                         │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│   │  sched.bt │  │ memory.bt│  │ block.bt │  │  irq.bt  │                  │
│   └─────┬─────┘  └─────┬────┘  └─────┬────┘  └─────┬────┘                  │
│         └───────────────┴─────────────┴─────────────┘                       │
│                              BPF maps (in-kernel aggregation)               │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │ ring buffer (every 1-5s)
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 1 — Data Pipeline                                                     │
│  ┌──────────────┐    ┌─────────────────┐    ┌──────────────────────────┐    │
│  │ bpftrace      │───▶│ userspace       │───▶│ SQLite (WAL mode)        │    │
│  │ JSON output   │    │ collector.py    │    │ metrics / windows /      │    │
│  │ (per-subsystem│    │ (auto-restart   │    │ incidents / model_       │    │
│  │  crash recovery)   │  on crash)      │    │ versions tables)         │    │
│  └──────────────┘    └─────────────────┘    └────────────┬─────────────┘    │
└───────────────────────────────────────────────────────────┼──────────────────┘
                                                           │
                                   ▼                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 2 — The Brain (AI Orchestrator)                                       │
│                                                                             │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Feature       │  │ Isolation     │  │ Trend        │  │ XAI           │  │
│  │ Engine        │─▶│ Forest        │─▶│ Forecast     │─▶│ Explainer     │  │
│  │ (108-dim per  │  │ (anomaly      │  │ Layer        │  │ (SHAP +       │  │
│  │  5s window)   │  │  scoring)     │  │ (early       │  │  native +     │  │
│  │               │  │               │  │  warning)    │  │  NL output)   │  │
│  └──────────────┘  └───────────────┘  └──────────────┘  └───────┬───────┘  │
│                                                                   │          │
│  ┌──────────────────────────────────────────────────────────────┐ │          │
│  │ Decision Gate: Normal / Suggestion / Action                   │ │          │
│  └──────────────────────────────────┬───────────────────────────┘ │          │
│                                     ▼                             │          │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────────┘          │
│  │ Action       │  │ Rollback      │  │ Auto-Retrain (every 200 windows)    │
│  │ Engine       │  │ (if action    │  │ Sliding window of last 2000 normal   │
│  │ renice/ionice│  │  fails →      │  │ windows. Validation gate before      │
│  │ /restart     │  │  undo)        │  │ promoting new model.                 │
│  └──────────────┘  └───────────────┘                                        │
└──────────────────────────────────────────────────────────────────────────────┘
                                                           │
                                                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 3 — Observability Dashboard (PyQt6 + pyqtgraph)                       │
│                                                                             │
│  ┌──────────┐ ┌──────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │
│  │ 📊 Live  │ │ 📈 Metrics   │ │ 📜 History│ │ 🧠 AI    │ │ ⚙️ System│      │
│  │ View     │ │ & Charts     │ │ & Incidents│ │ Insights │ │ Controls │      │
│  │          │ │ (23 live     │ │           │ │ & XAI    │ │          │      │
│  │ 50 rows  │ │  mini-charts │ │ 50 recent │ │          │ │ Mode     │      │
│  │ real-time│ │  per-metric) │ │ incidents │ │ Anomaly  │ │ Threshold│      │
│  │ stream   │ │              │ │           │ │ Gauge    │ │ Clear DB │      │
│  └──────────┘ └──────────────┘ └──────────┘ └──────────┘ └──────────┘      │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Features

### Data Pipeline
- **4 eBPF probes** — scheduler, memory, block I/O, interrupts
- **In-kernel aggregation** via BPF maps (cheap, low overhead)
- **Auto-recovering collector** — survives bpftrace crashes, restarts each subsystem independently
- **SQLite WAL mode** — safe, fast writes for demo-scale workloads
- **Device name decoding** — raw `dev_t` numbers converted to human-readable names

### AI Brain
- **108-dimensional feature vector** per 5-second window across 9 core metrics
- **Isolation Forest** anomaly detection with continuous scoring (not just binary)
- **Dual trigger gate** — both confidence (percentile) AND magnitude (per-feature sigma) can independently trigger
- **SHAP + native XAI** — every alarm gets a plain-language explanation (e.g., "Run-queue latency is 13.3x its baseline")
- **Historical similarity** — cosine similarity search against past incidents
- **Trend forecasting** — linear regression early-warning layer separate from Isolation Forest
- **Auto-retraining** — every 200 new windows, retrains on sliding window of last 2000 normal windows with validation gate
- **Safe actions** — renice / ionice / restart with pre-action snapshots, rate limiting, 30s monitoring window, and automatic rollback

### Dashboard
- **23 live metric charts** — Task-Manager-style scrolling graphs, one per metric
- **Color-coded by subsystem** — cyan (scheduler), violet (memory), amber (disk), rose (IRQ)
- **Proper units** — µs latency, KB/MB/GB bytes, event counts with K/M scaling
- **Real-time 1s refresh** — charts slide left as new data arrives
- **Friendly labels** — technical metric names replaced with readable labels
- **Severity-colored anomaly gauge** — green (normal) → amber (warning) → red (critical)
- **Desktop icon + autostart** — appears in Linux app launcher, starts at every login

---

## Setup

### Quick Install (Recommended)

```bash
git clone https://github.com/zainab-06-p/SElf-Healing-OS.git
cd SElf-Healing-OS
./install.sh
```

This single command:
1. Installs the Python package (creates the `shos` terminal command)
2. Creates a desktop icon in your app launcher
3. Sets up auto-start at every login
4. Adds `~/.local/bin` to your PATH

After install, launch from anywhere:
```bash
shos                          # uses default DB path
shos --db /path/to/custom.db  # custom database
```

### Manual Install

```bash
pip install .
shos
```

### Development Setup

```bash
pip install -r requirements.txt
python3 -m pytest ai_brain/tests/test_step2.py -q   # run 21 tests
```

---

## Live Demo (4 Terminals)

Run on a Linux VM with root access:

```bash
# Terminal 1 — The Senses (eBPF collector)
sudo python3 data_pipeline/src/collector.py

# Terminal 2 — The Face (dashboard)
shos

# Terminal 3 — The Brain (AI orchestrator)
sudo python3 ai_brain/scripts/run_brain.py --db data_pipeline/data/self_healing_os.db

# Terminal 4 — The Chaos (fault injection)
timeout 90s nice -n 15 python3 ai_brain/scripts/fault_inject.py --role shos_cpuhog
```

**What you'll see:**
1. CPU load spikes in the Metrics tab charts
2. AI tab turns red with plain-language root cause
3. Brain executes `renice` to fix the issue
4. Charts return to baseline
5. Green "Resolved" incident appears in History

---

## Fault Injection Scenarios

| Scenario | Command | What It Tests |
|----------|---------|---------------|
| CPU starvation | `--role shos_cpuhog` | Scheduler latency detection → renice |
| Memory leak | `--role shos_memleak` | Page fault spike → process restart |
| I/O degradation | `--role shos_iostress` | Block latency → ionice |

---

## Evaluation

```bash
# Synthetic evaluation (no root needed)
python3 dashboard/scripts/evaluate.py --synthetic

# Hardware evaluation (root required)
sudo bash data_pipeline/scripts/run_experiment.sh
python3 dashboard/scripts/evaluate.py --db data_pipeline/data/experiment.db
```

Results: `dashboard/results/eval_results.json`

---

## Project Structure

```
SElf-Healing-OS/
├── pyproject.toml                    # Package metadata + entry point
├── install.sh                        # One-shot installer
├── requirements.txt                  # Python dependencies
│
├── self_healing_os/                  # Installable package
│   ├── __init__.py
│   └── launcher.py                   # Entry point for `shos` command
│
├── data_pipeline/                    # DATA PIPELINE
│   ├── bpftrace/                     # 4 eBPF probes + diagnostics
│   │   ├── sched.bt                  # Scheduler (ctx switches, preemptions, run-queue)
│   │   ├── memory.bt                 # Memory (page faults, OOM kills)
│   │   ├── block.bt                  # Block I/O (latency, ops, bytes)
│   │   ├── irq.bt                    # Interrupts (frequency, latency)
│   │   └── diagnostics/histograms.bt
│   ├── src/
│   │   ├── collector.py              # Userspace bpftrace → SQLite collector
│   │   └── db.py                     # SQLite schema + insert helpers
│   ├── scripts/
│   │   ├── loadgen.py                # Controlled load generator
│   │   └── run_experiment.sh         # Experiment runner
│   └── data/
│       └── self_healing_os.db        # SQLite database
│
├── ai_brain/                         # AI BRAIN
│   ├── src/
│   │   ├── brain.py                  # Main orchestrator (replay + live loop)
│   │   ├── feature_engine.py         # 108-dim feature vector per 5s window
│   │   ├── model.py                  # Isolation Forest + auto-retrainer
│   │   ├── trend.py                  # Linear trend / early-warning layer
│   │   ├── explain.py                # SHAP + native XAI + NL templates
│   │   ├── action_engine.py          # renice / ionice / restart + rollback
│   │   └── config.py                 # All hyperparameters in one place
│   ├── scripts/
│   │   ├── fault_inject.py           # CPU / memory / I/O fault injectors
│   │   ├── run_brain.py              # CLI entry point
│   │   └── run_e2e_demo.py           # End-to-end demo script
│   └── tests/
│       └── test_step2.py             # 21 tests covering all exit criteria
│
└── dashboard/                        # OBSERVABILITY DASHBOARD
    ├── src/
    │   ├── app.py                    # PyQt6 main window (5 tabs)
    │   └── theme.py                  # Modern dark theme (QSS + helpers)
    ├── scripts/
    │   └── evaluate.py               # P/R/F1 + time-to-detection harness
    ├── results/
    │   └── eval_results.json         # Evaluation output
    └── EVALUATION.md                 # Methodology + results
```

---

## Uninstall

```bash
pip3 uninstall self-healing-os
rm ~/.local/share/applications/self-healing-os.desktop
rm ~/.config/autostart/self-healing-os.desktop
```

---

## License

MIT
