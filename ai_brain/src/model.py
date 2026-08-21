"""Isolation Forest model wrapper (Sections 2.2 / 2.3).

Wraps sklearn's IsolationForest with:
  * continuous anomaly scoring via `-decision_function()`,
  * a z-score calibration derived from the training scores, so the decision
    gate can threshold on "how many standard deviations above normal" rather
    than a hard-coded number,
  * per-feature mean/std statistics (used for explanations and for the
    z-scored incident vectors used by historical-similarity lookup),
  * a validation gate used before promoting a retrained model. A candidate is
    promoted only if it (a) does not cry wolf on recent known-normal held-out
    windows and (b) still detects the system's own past incidents. Flagging ~0%
    of a *normal* holdout is healthy (the model calls normal data normal), so
    the gate no longer rejects low flag rates - it rejects high ones.
"""

import json
import os
import sys

import numpy as np
from sklearn.ensemble import IsolationForest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from ai_brain.src import config as cfg  # noqa: E402


class AnomalyModel:
    def __init__(self, cfg_=None):
        self.cfg = cfg_ or cfg
        self.model = None
        self.version = 0
        self.feature_order = list(self.cfg.FEATURE_ORDER)
        self.feature_means = None
        self.feature_stds = None
        self.train_score_mean = 0.0
        self.train_score_std = 1.0
        self.train_scores_sorted = None

    # -- training -----------------------------------------------------------
    def fit(self, X, feature_order=None):
        """Train on chronological samples X (rows = windows, cols = features)."""
        if feature_order is not None:
            self.feature_order = list(feature_order)
        X = np.asarray(X, dtype=float)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        if X.shape[0] < 1:
            raise ValueError("cannot fit on an empty training set")

        self.model = IsolationForest(
            n_estimators=self.cfg.N_ESTIMATORS,
            contamination=self.cfg.CONTAMINATION,
            max_samples=self.cfg.MAX_SAMPLES,
            random_state=self.cfg.RANDOM_STATE + self.version,
        )
        self.model.fit(X)  # rows stay in chronological order - never shuffled

        self.feature_means = np.mean(X, axis=0)
        self.feature_stds = np.std(X, axis=0)
        self.feature_stds[self.feature_stds == 0.0] = 1.0

        train_scores = -self.model.decision_function(X)
        self.train_score_mean = float(np.mean(train_scores))
        self.train_score_std = float(np.std(train_scores)) or 1.0
        self.train_scores_sorted = np.sort(train_scores)
        self.version += 1
        return self

    # -- scoring ------------------------------------------------------------
    def scores(self, X):
        X = np.asarray(X, dtype=float)
        single = X.ndim == 1
        if single:
            X = X.reshape(1, -1)
        out = -self.model.decision_function(X)
        return out[0] if single else out

    def z(self, X):
        s = self.scores(X)
        z = (s - self.train_score_mean) / self.train_score_std
        return float(z)

    def confidence(self, X):
        """Fraction of training windows this sample outscores (0..1)."""
        s = self.scores(X)
        s = np.atleast_1d(s)
        sorted_scores = self.train_scores_sorted
        ranks = np.searchsorted(sorted_scores, s, side="right")
        conf = ranks.astype(float) / len(sorted_scores)
        return float(conf[0]) if conf.shape == (1,) else conf

    def predict(self, X):
        X = np.asarray(X, dtype=float)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        return self.model.predict(X)

    def zscore_vector(self, x):
        """Per-feature z-scored copy of a sample (for similarity lookups)."""
        x = np.asarray(x, dtype=float)
        return np.where(self.feature_stds > 0, (x - self.feature_means) / self.feature_stds, 0.0)

    def deviation(self, x):
        """Severity magnitude: max |z| over the core per-metric `cur` features.

        Unlike the percentile confidence (calibrated against the whole training
        set), this per-feature deviation is not diluted when an anomaly lives in
        a small subset of the 108 features - a severe CPU-starvation fault in
        run-queue latency reads 80-2500 sigma while normal windows stay below
        ~8 sigma, so a fixed sigma gate separates them cleanly.
        """
        x = np.asarray(x, dtype=float)
        z = self.zscore_vector(x)
        cur_idx = [self.feature_order.index(f"{m}__cur") for m in self.cfg.CORE_METRICS]
        return float(np.abs(z[cur_idx]).max())


class Retrainer:
    """Handles the auto-retraining loop and its validation gate (2.3)."""

    def __init__(self, cfg_=None):
        self.cfg = cfg_ or cfg

    @staticmethod
    def _flag_rate(model, X):
        """Fraction of windows X the IsolationForest labels anomalous.

        Used only for the cry-wolf check (how often a *known-normal* holdout
        is flagged). For measuring detection of known incidents use
        `_would_detect` instead: in the full 108-dim space a severe fault
        concentrated in a few features can be scored *less* anomalous than
        plain normal windows (isolation masking), so raw predict() labels
        systematically under-count the incidents the brain would still catch
        via the magnitude gate.
        """
        if len(X) == 0:
            return 0.0
        return float(np.mean(model.predict(np.asarray(X, dtype=float)) == -1))

    @staticmethod
    def _would_detect(model, x):
        """True if the brain's own decision gate would flag window x.

        Mirrors `Brain._gate`'s per-window signals (percentile confidence and
        per-feature z deviation over the core `cur` metrics) so the retrain
        gate measures detection exactly the way live detection does - not via
        predict() labels, which are unreliable in the 108-dim feature space.
        """
        conf = model.confidence(x)
        mag = model.deviation(x)
        if mag >= model.cfg.MAG_SUGGEST:
            return True
        return conf >= model.cfg.SUGGEST_CONF and mag >= model.cfg.MAG_CONFIRM

    def _detect_rate(self, model, X):
        if len(X) == 0:
            return 0.0
        hits = sum(1 for x in np.atleast_2d(X) if self._would_detect(model, x))
        return hits / len(X)

    def validate(self, model, X_holdout, X_anomalous=None):
        """Return (flag_rate, accepted, reason) for a candidate model.

        Rejects candidates that flag too many known-normal held-out windows
        (cries wolf) or that fail to detect the system's own past incidents
        under the brain's own decision gate (degenerate - would stop
        self-healing). Flagging 0% of normal held-out windows is healthy and
        is therefore accepted.
        """
        if len(X_holdout) == 0:
            return 0.0, False, "no held-out windows available"
        flag_rate = self._flag_rate(model, X_holdout)
        hi = self.cfg.VALIDATE_MAX_FLAG
        if flag_rate > hi:
            return flag_rate, False, f"flag rate {flag_rate:.3f} too high (> {hi})"
        if X_anomalous is not None and len(X_anomalous) > 0:
            det_rate = self._detect_rate(model, X_anomalous)
            lo_det = self.cfg.VALIDATE_MIN_DETECT
            if det_rate < lo_det:
                return flag_rate, False, (
                    f"detects only {det_rate:.3f} of known anomalies "
                    f"(< {lo_det}) - would stop recovering past fault patterns"
                )
        return flag_rate, True, f"flag rate {flag_rate:.3f} within sane band"

    def retrain(self, normal_windows, feature_order=None, anomalous_windows=None):
        """Train a candidate on recent known-normal windows (chronological),
        validate against the newest known-normal held-out slice plus (when
        available) the system's past incident windows, and return
        (candidate, validation_dict) without mutating the active model."""
        feature_order = feature_order or list(self.cfg.FEATURE_ORDER)
        rows = sorted(normal_windows, key=lambda r: r[0])  # by start_ts
        if len(rows) < self.cfg.RETRAIN_MIN_TRAIN:
            return None, {"accepted": False, "reason": "insufficient normal history"}

        holdout = rows[-self.cfg.HOLD_OUT_N:]
        train_rows = rows[:-self.cfg.HOLD_OUT_N] if len(rows) > self.cfg.HOLD_OUT_N else rows
        if len(train_rows) < self.cfg.RETRAIN_MIN_TRAIN:
            return None, {"accepted": False, "reason": "insufficient training rows after holdout"}

        X_train = np.array([r[1] for r in train_rows])
        X_hold = np.array([r[1] for r in holdout])
        X_anom = np.array([r[1] for r in anomalous_windows]) if anomalous_windows else None
        candidate = AnomalyModel(self.cfg)
        candidate.fit(X_train, feature_order)
        flag_rate, accepted, reason = self.validate(candidate, X_hold, X_anom)
        validation = {
            "n_train": int(len(X_train)),
            "n_holdout": int(len(X_hold)),
            "n_anomalous": int(len(X_anom)) if X_anom is not None else 0,
            "flag_rate": round(flag_rate, 4),
            "accepted": accepted,
            "reason": reason,
        }
        return candidate, validation
