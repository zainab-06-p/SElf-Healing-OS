"""Explainability (Section 2.5).

Implements:
  2.5.1  Native Isolation Forest feature attribution - walk every tree for the
         flagged point and credit the split feature at each node, weighted by
         1/(depth+1) so early, isolating splits dominate.
  2.5.2  SHAP attribution (optional second layer) - uses shap.TreeExplainer on
         the trained forest when shap is importable, falling back to
         KernelExplainer, then to the native attribution if shap is unavailable
         or unsupported by the installed version.
  2.5.3  Natural-language templating built from the ranked attributions.
  2.5.4  Historical similarity - cosine similarity against the z-scored feature
         vectors stored with every past incident.
"""

import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from step2.src import config as cfg  # noqa: E402


def _label_for(feature):
    base = feature.split("__")[0]
    suffix = feature.split("__")[1] if "__" in feature else ""
    label = cfg.FEATURE_LABELS.get(base, base)
    if suffix in cfg.FEATURE_SUFFIX_LABELS:
        label += cfg.FEATURE_SUFFIX_LABELS[suffix]
    elif suffix.startswith("slope_"):
        label += f" (trend over last {suffix[6:]} windows)"
    elif suffix.startswith("lag"):
        label += f" (lag-{suffix[3:]})"
    return label


class Explainer:
    def __init__(self, cfg_=None):
        self.cfg = cfg_ or cfg

    def native_attribution(self, model, x):
        """Rank features by their contribution to isolating `x` in the forest.

        For every tree we walk the path of `x`, crediting the split feature at
        each node with weight 1/(depth+1) so early, isolating splits dominate.
        The raw contribution is then combined with the feature's deviation from
        the training baseline (|z|), so a feature must be both *isolating* and
        *deviant* to rank - this suppresses noise features that happen to host
        shallow splits.

        Returns a list of (feature_name, weight, value) sorted descending.
        """
        x = np.asarray(x, dtype=float).reshape(-1)
        contrib = np.zeros(len(x))
        for estimator in model.model.estimators_:
            tree = estimator.tree_
            node = 0
            depth = 0
            while tree.children_left[node] != tree.children_right[node]:
                feat = tree.feature[node]
                contrib[feat] += 1.0 / (depth + 1.0)
                if x[feat] <= tree.threshold[node]:
                    node = tree.children_left[node]
                else:
                    node = tree.children_right[node]
                depth += 1
        contrib /= len(model.model.estimators_)

        z = np.where(model.feature_stds > 0, (x - model.feature_means) / model.feature_stds, 0.0)
        weight = contrib * (1.0 + np.abs(z))
        order = sorted(range(len(x)), key=lambda i: -weight[i])
        return [
            (model.feature_order[i], float(weight[i]), float(x[i]))
            for i in order
            if weight[i] > 1e-9
        ]

    def shap_attribution(self, model, x):
        """SHAP attribution (2.5.2): per-feature contribution via shap.

        Best-effort by design - this is the "more rigorous second layer" of the
        plan, optional when the native attribution already works. Returns a list
        of (feature_name, weight, value) ranked descending, or None when shap is
        not importable / cannot explain this model, in which case the caller
        falls back to `native_attribution`.
        """
        try:
            import shap
        except ImportError:
            return None
        x = np.asarray(x, dtype=float).reshape(1, -1)
        sv = None
        try:
            explainer = shap.TreeExplainer(model.model)
            sv = explainer.shap_values(x)
        except Exception:
            try:
                # KernelExplainer is a generic fallback (needs a background
                # sample); sample around the training means for a rough baseline.
                bg = np.random.RandomState(0).normal(
                    model.feature_means, model.feature_stds, size=(32, x.shape[1])
                )
                explainer = shap.KernelExplainer(model.model.decision_function, bg)
                sv = explainer.shap_values(x)
            except Exception:
                return None
        sv = np.asarray(sv, dtype=float).reshape(-1)  # (1, n) or (n,)
        if sv.shape[0] != len(model.feature_order):
            return None
        xflat = np.asarray(x, dtype=float).reshape(-1)
        z = np.where(model.feature_stds > 0,
                     (xflat - model.feature_means) / model.feature_stds, 0.0)
        # combine absolute SHAP magnitude with deviation so a feature must be
        # both impactful and deviant to rank (same shape as native_attribution)
        weight = np.abs(sv) * (1.0 + np.abs(z))
        order = sorted(range(len(xflat)), key=lambda i: -weight[i])
        return [
            (model.feature_order[i], float(weight[i]), float(xflat[i]))
            for i in order
            if weight[i] > 1e-9
        ]

    def _describe_feature(self, model, feature, value):
        label = _label_for(feature)
        idx = model.feature_order.index(feature)
        baseline = float(model.feature_means[idx])
        std = float(model.feature_stds[idx])
        scale = max(abs(value), abs(baseline), 1e-9)
        def _fmt(v):
            return "0" if v == 0 else f"{v:.0f}"
        if abs(value - baseline) < 0.01 * scale:
            return f"{label} is near its baseline ({_fmt(value)} vs {_fmt(baseline)})"
        # "Xx its baseline" is only meaningful when both sides are clearly
        # positive (a count/rate/duration). Trend/slope and residual features
        # are signed, and near-zero baselines produce nonsense ratios such as
        # "36x its baseline (-0 vs -0)", so use a directional deviation there.
        if baseline > 0.0 and value > 0.0:
            ratio = value / baseline
            return f"{label} is {ratio:.1f}x its baseline ({_fmt(value)} vs {_fmt(baseline)})"
        if std > 1e-9:
            z = (value - baseline) / std
            direction = "above" if z >= 0 else "below"
            return (
                f"{label} is {abs(z):.1f} std {direction} its baseline "
                f"(now {_fmt(value)}, baseline {_fmt(baseline)})"
            )
        kind = "rose to" if value > baseline else "fell to"
        return f"{label} {kind} {_fmt(value)} from a baseline of {_fmt(baseline)}"

    def explain(self, model, x, confidence):
        """Return (natural_language, top_features) for a flagged window.

        Prefers SHAP attribution (2.5.2) when shap is available and can explain
        the model; otherwise falls back to the native forest attribution (2.5.1).
        """
        ranked = self.shap_attribution(model, x)
        if not ranked:
            ranked = self.native_attribution(model, x)
        ranked = ranked[:3]
        parts = []
        drivers = []
        for feature, contrib, value in ranked:
            sentence = self._describe_feature(model, feature, value)
            parts.append(sentence)
            drivers.append({"feature": feature, "contribution": contrib, "value": value})
        top = ranked[0][0] if ranked else "unknown"
        primary = _label_for(top)
        pct = f"{confidence * 100:.0f}%"
        if not parts:
            return (
                f"Anomaly detected (confidence {pct}); no decisive feature attribution available.",
                [],
            )
        nl = (
            f"Anomaly detected (confidence {pct}). "
            + "; ".join(parts)
            + f". {primary} is the primary driver of the anomaly score."
        )
        return nl, drivers

    @staticmethod
    def _cosine(a, b):
        a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na == 0.0 or nb == 0.0:
            return 0.0
        return float(np.dot(a, b) / (na * nb))

    def historical_similarity(self, conn, model, x, threshold=0.90, top=1):
        """Search past incidents for a close feature-vector match.

        Returns a string summary or None.
        """
        xz = model.zscore_vector(x)
        rows = conn.execute(
            "SELECT incident_id, timestamp, action_taken, outcome FROM incidents "
            "WHERE action_taken LIKE '%feature_vector_z%'"
        ).fetchall()
        best = None
        for inc_id, ts, action_taken, outcome in rows:
            try:
                payload = json.loads(action_taken)
            except (TypeError, ValueError):
                continue
            zv = payload.get("feature_vector_z")
            if zv is None:
                continue
            sim = self._cosine(xz, zv)
            if sim >= threshold and (best is None or sim > best[0]):
                best = (sim, inc_id, outcome, payload.get("action"))
        if best is None:
            return None
        sim, inc_id, outcome, action = best
        outcome_txt = outcome or "unknown outcome"
        action_txt = f" ({action})" if action else ""
        return (
            f"This looks similar to incident #{inc_id} (cosine {sim:.2f}), "
            f"which was {outcome_txt}{action_txt}."
        )
