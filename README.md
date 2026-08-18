# Self-Healing OS

An autonomous fault-detection and self-healing operating-system monitor that
watches the Linux kernel live via **eBPF**, learns what "normal" looks like per
machine with **Isolation Forest**, explains every alarm in plain language
(**XAI**), and takes **reversible** corrective actions (renice / ionice /
restart) with automatic rollback and continuous retraining — all surfaced
through a real-time **PyQt6 dashboard**.

```
eBPF probes → SQLite time series → feature engine → Isolation Forest → decision gate → action engine (+ rollback)
                                                   ↑                                                    │
                                                   └────────── outcome log / retrain (every 200) ◄──────────┘
```

## Architecture (3 steps)

| Step | Component | Where |
|------|-----------|-------|
| 1 — Data pipeline | bpftrace probes (sched / memory / block / IRQ) → in-kernel aggregation → userspace collector → SQLite | `step1/` |
| 2 — The brain | 108-dim feature windows → Isolation Forest → SHAP/native XAI → trend layer → decision gate → action engine with rollback + auto-retrain validation gate | `step2/` |
| 3 — Observability + evaluation | PyQt6 + pyqtgraph dashboard (5 tabs + popups) and a P/R/F1 / time-to-detection / FPR harness | `step3/` |

Fault injection demos: CPU starvation (`shos_cpuhog`), memory leak
(`shos_memleak`), I/O degradation (`shos_iostress`) in `step2/scripts/fault_inject.py`.

## Quick start

```bash
pip install -r requirements.txt          # numpy, scikit-learn, PyQt6, pyqtgraph

# Step 2 tests (no root needed; 21 tests)
python3 -m pytest step2/tests/test_step2.py -q

# Formal evaluation (synthetic labelled corpus; no root/eBPF needed)
python3 step3/scripts/evaluate.py --synthetic
```

## Live demo (4 terminals, run as root on the Linux VM)

```bash
# 1. Senses — eBPF collector -> SQLite
sudo python3 step1/src/collector.py

# 2. Face — observability dashboard
python3 step3/src/app.py

# 3. Brain — AI orchestrator (needs --db)
sudo python3 step2/scripts/run_brain.py --db step1/data/self_healing_os.db

# 4. Chaos — inject a CPU-starvation fault (nice + timeout keeps the VM safe)
timeout 90s nice -n 15 python3 step2/scripts/fault_inject.py --role shos_cpuhog
```

Watch: the Metrics tab spikes, the AI tab turns red with a plain-language root
cause, the brain executes `renice`, the chart returns to baseline, and a green
"Resolved" incident appears in History.

## Evaluation

Synthetic fault-injection results (full methodology and live-VM rerun
instructions): [`step3/EVALUATION.md`](step3/EVALUATION.md). Raw numbers:
`step3/results/eval_results.json`.

For hardware-accurate numbers on the deployment VM (as root):

```bash
sudo bash step1/scripts/run_experiment.sh               # writes experiment.db
python3 step3/scripts/evaluate.py --db step1/data/experiment.db
```

## Documents

- `AI_Self_Healing_OS.md` — full design document
- `Self_Healing_OS_Implementation_Plan.md` — detailed 3-step build plan
- `PROGRESS.md` — current status, completion, and logged fixes
- `UI_Testing.md` — frontend & integration handoff (4-terminal setup)

## Status

~88% complete. Steps 1 & 2 (pipeline + brain) are fully implemented and tested;
Step 3's dashboard and evaluation harness are built and verified on a synthetic
corpus. Remaining: hardware-evaluation numbers on the live VM, demo rehearsal
polish, optional IRQ module-reload action, and optional libbpf/CO-RE port. See
`PROGRESS.md` for the detailed breakdown.