"""Step 2 configuration: feature engineering, model, gate, and action knobs.

All thresholds are centralised here so the brain (and the tests) can tune
behaviour without touching pipeline code.
"""

# ---------------------------------------------------------------------------
# Core metrics tracked by Step 1 collectors (one aggregate per 5s snapshot).
# Order here defines the fixed feature order used for the model matrix.
# ---------------------------------------------------------------------------
CORE_METRICS = [
    "sched_ctx_switches",
    "sched_preemptions",
    "sched_runq_lat_avg_us",
    "mem_page_faults_total",
    "mem_oom_kills",
    "blk_io_ops_total",
    "blk_bytes_total",
    "blk_io_errors",
    "irq_handled_total",
]

# Subsystem group -> metric-name prefix (used to decide window completeness
# when the four collectors do not land on the same wall-clock second).
SUBSYSTEM_PREFIXES = {
    "sched": "sched_",
    "mem": "mem_",
    "blk": "blk_",
    "irq": "irq_",
}
MIN_SUBSYSTEMS_PER_WINDOW = 3

# ---------------------------------------------------------------------------
# Feature engineering (Section 2.1)
# ---------------------------------------------------------------------------
INTERVAL = 5.0          # collector cadence in seconds (window width on the grid)
LOOKBACK = 12           # rolling-window length for point statistics (windows)
HORIZONS = [6, 24, 60]  # slope horizons in windows (30s / 2min / 5min)
LAGS = [1, 2, 3]        # t-k windows
EWMA_ALPHA = 0.2        # EWMA smoothing factor for the residual feature
MIN_HISTORY = 8         # minimum points required before slopes are meaningful

# Fixed per-metric feature layout (12 features per metric, 9 metrics = 108).
FEATURE_TYPES = (
    ["cur", "mean", "std", "min", "max"]
    + [f"slope_{h}" for h in HORIZONS]
    + ["ewma_res", "lag1", "lag2", "lag3"]
)
FEATURE_ORDER = [f"{m}__{ft}" for m in CORE_METRICS for ft in FEATURE_TYPES]

# ---------------------------------------------------------------------------
# Isolation Forest (Section 2.2)
# ---------------------------------------------------------------------------
N_ESTIMATORS = 100
CONTAMINATION = 0.02        # doc: 0.01-0.05, real faults are rare
MAX_SAMPLES = "auto"        # capped at min(256, n) by sklearn
RANDOM_STATE = 42
MIN_TRAIN = 30              # minimum windows needed before bootstrapping a model
BASELINE_TRAIN = 100        # windows used for the initial baseline model (prefix)
MIN_TRAIN_LIVE = 50         # windows needed before live scoring begins

# ---------------------------------------------------------------------------
# Auto-retraining (Section 2.3)
# ---------------------------------------------------------------------------
RETRAIN_EVERY = 200         # trigger retraining after this many new windows
RETRAIN_HISTORY = 1000      # sliding training window size (windows)
HOLD_OUT_N = 50             # recent known-normal windows reserved for validation
# A healthy model trained on recent *normal* windows flags ~0% of a normal
# holdout (calling it normal is correct, not degenerate), so flag-rate 0 must
# be accepted. The gate only rejects a candidate that cries wolf on normal
# data (flag rate above VALIDATE_MAX_FLAG) or that no longer detects the
# system's own past incidents (detection rate below VALIDATE_MIN_DETECT).
VALIDATE_MIN_FLAG = 0.0     # floor on held-out normal-window flag rate (0 is healthy)
VALIDATE_MAX_FLAG = 0.05    # ceiling on held-out normal-window flag rate
VALIDATE_MIN_DETECT = 0.3   # candidate must still flag >= 30% of past incidents
RETRAIN_MIN_TRAIN = 30      # minimum usable training rows before a retrain

# ---------------------------------------------------------------------------
# Decision gate (Section 2.6) - percentile confidence of the anomaly score
# ---------------------------------------------------------------------------
# Isolation Forest decision-function values are tightly clustered, so a
# z-score against the training std compresses everything. Instead we measure
# where a window sits in the *training* score distribution: the fraction of
# training windows it outscores. A real fault sits above ~all of them.
#
# Actions are only taken on *sustained* anomalies: high confidence across
# consecutive windows (a single spike stays a suggestion), and/or when the
# independent trend-forecast layer agrees.
SUGGEST_CONF = 0.95        # outscores >= 95% of training -> log as suggestion
ACTION_CONF = 0.97         # outscores >= 97% of training -> action, if sustained
SUSTAIN_WINDOWS = 3        # look-back for the persistence check
SUSTAIN_MIN = 2            # how many of the last windows must reach ACTION_CONF
TREND_PUSH_ACTION_CONF = 0.95  # trend warning + conf >= this -> action-grade

# Magnitude gate (Section 2.3). The IF percentile is calibrated against the
# training set, so a severe fault living in a small subset of features can
# saturate below the percentile gates. A per-feature z-deviation over the
# core `cur` metrics (the same z used for explanations) separates such faults
# cleanly: measured on the real baseline, normal windows (including transient
# page-fault/context-switch spikes) stay below ~8 sigma, while a CPU-starvation
# fault sits at 80-2500 sigma. The gate sits between the two with margin on
# both sides; it also tolerates slow baseline drift without false positives.
MAG_SUGGEST = 12.0         # any core metric >= 12 sigma above its baseline
MAG_ACTION = 20.0          # sustained >= 20 sigma -> action-grade
# A confidence-driven suggestion must also be *currently* deviant (>= this many
# sigma on a live `cur` metric). Without this, lag/rolling features keep the
# percentile score elevated for ~a minute after a fault heals, so the alarm
# would linger on an already-recovered system instead of clearing.
MAG_CONFIRM = 2.0


# ---------------------------------------------------------------------------
# Trend / forecast layer (Section 2.4)
# ---------------------------------------------------------------------------
TREND_N = 12                # points used for the linear fit
TREND_PROJECT = 6           # windows projected ahead (~30s)
TREND_Z = 5.0               # projected > mean + Z*std triggers an early warning
TREND_MIN_POINTS = 8

# ---------------------------------------------------------------------------
# Action engine + rollback (Section 2.7)
# ---------------------------------------------------------------------------
COOLDOWN = 120              # seconds to wait before repeating an action
MONITOR_WINDOWS = 6         # ~30s of 5s windows to judge an action's effect
IMPROVE_RATIO = 0.5         # metric at/below this fraction of pre-action -> resolved
ACTION_LOG_LEVEL = "info"

# Human-readable labels for explanations (Section 2.5.3).
FEATURE_LABELS = {
    "sched_ctx_switches": "context-switch rate",
    "sched_preemptions": "preemption rate",
    "sched_runq_lat_avg_us": "run-queue latency",
    "mem_page_faults_total": "memory page-fault rate",
    "mem_oom_kills": "OOM-kill count",
    "blk_io_ops_total": "block I/O operation rate",
    "blk_bytes_total": "block I/O throughput",
    "blk_io_errors": "block I/O error count",
    "irq_handled_total": "IRQ handled rate",
}
FEATURE_SUFFIX_LABELS = {
    "cur": " (current)",
    "mean": " (rolling mean)",
    "std": " (rolling std)",
    "min": " (rolling min)",
    "max": " (rolling max)",
    "ewma_res": " (EWMA residual)",
}
