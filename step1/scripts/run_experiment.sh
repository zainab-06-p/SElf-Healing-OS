#!/usr/bin/env bash
#
# Step 1 integration test / experiment (v2).
#
# Runs ONE continuous collector, then applies load in the middle of the run and
# compares per-window metric values before/after the phase boundary.
#
# Phases:
#   WARMUP 10s  -> collector settles, windows discarded from the comparison
#   IDLE   20s  -> baseline
#   LOAD   25s  -> loadgen.py applies cpu/mem/io/fork pressure
#   RECOV  10s  -> watch metrics fall back (optional)
#
# Phase boundaries are recorded directly in the SQLite DB as marker rows
# (metric_name='__phase__'), so the analysis is unambiguous regardless of
# collector startup timing.
#
# Run as root:  sudo bash scripts/run_experiment.sh
#
set -u

INTERVAL=5
WARMUP=${1:-10}
IDLE=${2:-20}
LOAD=${3:-25}
RECOV=${4:-10}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="${HERE}/data/experiment.db"
COLLECTOR="${HERE}/src/collector.py"
LOADGEN="${HERE}/scripts/loadgen.py"

mkdir -p "${HERE}/data"
rm -f "$DB"

if [ "$(id -u)" != "0" ]; then
    echo "ERROR: run as root (sudo bash $0)"
    exit 1
fi

mark() {  # mark <value> ; epoch timestamp of the phase boundary
    local now
    now=$(python3 -c "import time; print(time.time())")
    sqlite3 "$DB" "INSERT INTO metrics(metric_name, timestamp, value, source)
                   VALUES('__phase__', $now, $1, 'experiment');"
}

TOTAL=$((WARMUP + IDLE + LOAD + RECOV))

echo "==> DB: $DB  (warmup=${WARMUP}s idle=${IDLE}s load=${LOAD}s recovery=${RECOV}s)"
python3 "$COLLECTOR" --db "$DB" --interval "$INTERVAL" --duration "$TOTAL" \
    >"${HERE}/data/experiment.log" 2>&1 &
COLPID=$!

# --- warmup ---
sleep "$WARMUP"

# --- idle ---
mark 0
echo "==> IDLE phase (${IDLE}s), baseline = $(date +%T)"
sleep "$IDLE"

# --- load ---
mark 1
echo "==> LOAD phase (${LOAD}s): loadgen cpu/mem/io/fork = $(date +%T)"
python3 "$LOADGEN" --cpu 2 --mem 96 --io 2 --fork 2 --duration "$LOAD" \
    >/dev/null 2>&1 &
LPID=$!
sleep "$LOAD"
wait "$LPID" 2>/dev/null

# --- recovery ---
mark 0
echo "==> RECOVERY phase (${RECOV}s) = $(date +%T)"
sleep "$RECOV"

wait "$COLPID"

echo "==> Done collecting. Comparison from SQLite:"
python3 - "$DB" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
cur = con.cursor()
total = cur.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
print(f"total metric rows: {total}  "
      f"(windows/incidents/model_versions: "
      + ", ".join(str(cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                  for t in ("windows", "incidents", "model_versions")) + ")")

# Phase boundaries from marker rows (in order: idle_start, load_start, load_end)
marks = cur.execute(
    "SELECT timestamp, value FROM metrics WHERE metric_name='__phase__' "
    "ORDER BY timestamp").fetchall()
if len(marks) >= 3:
    t_idle_start, t_load_start, t_load_end = marks[0][0], marks[1][0], marks[2][0]
else:
    print("expected >=3 phase markers, found", len(marks)); sys.exit(1)

METRICS = ("sched_runq_lat_avg_us", "sched_runq_lat_max_us",
           "mem_page_faults_total",
           "blk_io_ops_total", "blk_bytes_total", "irq_handled_total")


def phase_values(metric, t0, t1):
    """Per-window values of a metric inside a phase (robust to outliers)."""
    return [v for (v,) in cur.execute(
        "SELECT value FROM metrics WHERE metric_name=? AND source='system' "
        "AND timestamp >= ? AND timestamp < ? ORDER BY timestamp",
        (metric, t0, t1))]


def median(vals):
    if not vals:
        return None
    vals = sorted(vals)
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


print(f"\n{'metric':<24}{'IDLE med':>14}{'LOAD med':>14}{'ratio':>9}  windows(idle/load)")
for m in METRICS:
    iv, lv = phase_values(m, t_idle_start, t_load_start), \
             phase_values(m, t_load_start, t_load_end)
    im, lm = median(iv), median(lv)
    r = (lm / im) if im else float("nan")
    print(f"  {m:<22}{im or 0:>14.0f}{lm or 0:>14.0f}{r:>9.2f}  {len(iv)}/{len(lv)}")
print("(medians of per-window values -- robust to background spikes)")
print(f"\nidle window: t={t_idle_start:.1f} .. {t_load_start:.1f}")
print(f"load window: t={t_load_start:.1f} .. {t_load_end:.1f}")

# Per-window series for the busiest metrics so the response is visible
print("\n==> per-window series (busiest metrics, 'LOAD' marks the load window):")
by_median = sorted(((m, median(phase_values(m, t_idle_start, t_load_end)))
                    for m in METRICS), key=lambda x: x[1] or 0, reverse=True)
for m, _ in by_median[:3]:
    print(f"\n--- {m} ---")
    row = ["%6.0f|%s" % (v, "LOAD" if t_load_start <= ts < t_load_end else "    ")
           for ts, v in cur.execute(
               "SELECT timestamp, value FROM metrics WHERE metric_name=? "
               "AND source='system' ORDER BY timestamp", (m,))]
    for i in range(0, len(row), 4):
        print("   " + "   ".join(row[i:i+4]))
con.close()
PY
