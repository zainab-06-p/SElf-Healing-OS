"""Feature engineering (Section 2.1).

Reads raw 5s `metrics` rows from SQLite and produces one feature vector per
aligned time window. The four collectors emit their snapshots a few hundred
milliseconds apart, so snapshots are bucketed onto a fixed time grid
(round(timestamp / INTERVAL) * INTERVAL). A window is considered complete once
a later grid bucket appears (or the poll is forced), and only when at least
MIN_SUBSYSTEMS_PER_WINDOW subsystems reported into it.

Per metric, per window the vector contains:
    cur, mean, std, min, max         (rolling point statistics)
    slope_{h} for h in HORIZONS      (linear-regression trend)
    ewma_res                         (actual - EWMA over history)
    lag1, lag2, lag3                 (short-term trajectory)
"""

import os
import sys
from collections import defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from step1.src import db  # noqa: E402
from step2.src import config as cfg  # noqa: E402


def _subsystem_of(metric_name):
    for group, prefix in cfg.SUBSYSTEM_PREFIXES.items():
        if metric_name.startswith(prefix):
            return group
    return None


def _ewma(values, alpha):
    """Exponentially weighted moving average over the full series."""
    if not values:
        return 0.0
    e = values[0]
    for v in values[1:]:
        e = alpha * v + (1.0 - alpha) * e
    return e


def _feature_values_for(metric, vals, cfg_):
    """Compute the fixed feature block for one metric given its history."""
    n = len(vals)
    cur = vals[-1] if n else 0.0
    out = {f"{metric}__cur": float(cur)}

    window = vals[-cfg_.LOOKBACK:] if n else []
    if window:
        out[f"{metric}__mean"] = float(np.mean(window))
        out[f"{metric}__std"] = float(np.std(window))
        out[f"{metric}__min"] = float(np.min(window))
        out[f"{metric}__max"] = float(np.max(window))
    else:
        out[f"{metric}__mean"] = 0.0
        out[f"{metric}__std"] = 0.0
        out[f"{metric}__min"] = 0.0
        out[f"{metric}__max"] = 0.0

    for h in cfg_.HORIZONS:
        chunk = vals[-h:] if n else []
        if len(chunk) >= 4:
            slope = float(np.polyfit(np.arange(len(chunk)), chunk, 1)[0])
        else:
            slope = 0.0
        out[f"{metric}__slope_{h}"] = slope

    out[f"{metric}__ewma_res"] = float(cur - _ewma(vals, cfg_.EWMA_ALPHA)) if n else 0.0

    for k in cfg_.LAGS:
        out[f"{metric}__lag{k}"] = float(vals[-1 - k]) if n > k else 0.0
    return out


class FeatureEngine:
    """Incrementally turns new `metrics` rows into completed feature windows."""

    def __init__(self, cfg_=None):
        self.cfg = cfg_ or cfg
        self._buckets = defaultdict(dict)      # grid_ts -> {metric: value}
        self._snapshot_groups = defaultdict(set)  # grid_ts -> {subsystem, ...}
        self._processed = set()                # grid_ts already emitted
        self.series = defaultdict(list)        # metric -> [cur values in order]
        self.cur_series = defaultdict(list)    # metric -> [(ts, cur)]
        self.windows = []                      # (start_ts, end_ts, vector)

    def _grid(self, ts):
        return round(ts / self.cfg.INTERVAL) * self.cfg.INTERVAL

    def ingest_rows(self, rows):
        """rows: iterable of (metric_name, timestamp, value) for source=system."""
        for name, ts, value in rows:
            if name not in self.cfg.CORE_METRICS:
                continue
            grid = self._grid(ts)
            self._buckets[grid][name] = float(value)
            sub = _subsystem_of(name)
            if sub:
                self._snapshot_groups[grid].add(sub)

    def _buckets_sorted(self):
        return sorted(k for k in self._buckets if k not in self._processed)

    def complete_grids(self, include_last=False):
        """Grids that can be turned into windows right now."""
        grids = self._buckets_sorted()
        if not grids:
            return []
        max_grid = grids[-1]
        done = []
        for g in grids:
            if g == max_grid and not include_last:
                continue  # waiting for the next cadence tick to be sure
            if len(self._snapshot_groups[g]) < self.cfg.MIN_SUBSYSTEMS_PER_WINDOW:
                continue
            done.append(g)
        return done

    def build_window(self, grid):
        """Build the feature vector for grid, advancing the metric series."""
        bucket = self._buckets[grid]
        vector = {}
        for metric in self.cfg.CORE_METRICS:
            cur = bucket.get(metric, 0.0)
            hist = list(self.series[metric])
            vals = hist + [cur]
            vector.update(_feature_values_for(metric, vals, self.cfg))
            self.series[metric].append(cur)
            self.cur_series[metric].append((grid, cur))
        self._processed.add(grid)
        return vector

    def poll(self, conn, since_ts=None, force_complete=False):
        """Read new system metrics and return completed windows.

        Returns a list of (start_ts, end_ts, vector_dict).
        """
        if since_ts is not None:
            rows = conn.execute(
                "SELECT metric_name, timestamp, value FROM metrics "
                "WHERE source='system' AND timestamp > ? ORDER BY timestamp",
                (since_ts,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT metric_name, timestamp, value FROM metrics "
                "WHERE source='system' ORDER BY timestamp"
            ).fetchall()
        self.ingest_rows(rows)

        out = []
        for g in self.complete_grids(include_last=force_complete):
            vec = self.build_window(g)
            self.windows.append((g, g + self.cfg.INTERVAL, vec))
            out.append((g, g + self.cfg.INTERVAL, vec))
        return out

    def max_ts(self, conn):
        row = conn.execute(
            "SELECT MAX(timestamp) FROM metrics WHERE source='system'"
        ).fetchone()
        return row[0] if row and row[0] is not None else 0.0
