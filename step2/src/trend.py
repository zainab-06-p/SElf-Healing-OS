"""Lightweight trend/forecast layer (Section 2.4).

Independent early-warning signal: fit a simple linear regression on the last
N points of each metric's current-value series and project ~30s ahead. If the
projected value crosses mean + Z*std, raise a warning that is independent of
the Isolation Forest anomaly score.
"""

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from step2.src import config as cfg  # noqa: E402


class TrendLayer:
    def __init__(self, cfg_=None):
        self.cfg = cfg_ or cfg

    def evaluate(self, cur_series):
        """cur_series: {metric: [(ts, cur_value), ...]}. Returns warnings dict."""
        warnings = {}
        for metric, pts in cur_series.items():
            recent = [v for _, v in pts[-self.cfg.TREND_N:]]
            if len(recent) < self.cfg.TREND_MIN_POINTS:
                continue
            x = np.arange(len(recent), dtype=float)
            slope, intercept = np.polyfit(x, recent, 1)
            projected = intercept + slope * (len(recent) + self.cfg.TREND_PROJECT - 1)
            # residual std after detrending: measures the metric's *noise*, so a
            # sustained rise is not confused with a noisy-but-flat signal
            fitted = intercept + slope * x
            residual_std = float(np.std(recent - fitted))
            mean = float(np.mean(recent))
            threshold = mean + self.cfg.TREND_Z * residual_std
            # relative floor: a sustained meaningful increase, not noise
            if projected > threshold and (projected - mean) > 0.25 * max(mean, 1e-9):
                warnings[metric] = {
                    "projected": float(projected),
                    "mean": mean,
                    "threshold": threshold,
                    "slope": float(slope),
                }
        return warnings
