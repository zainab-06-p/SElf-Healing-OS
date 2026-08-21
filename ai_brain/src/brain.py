"""Self-Healing OS "brain" (Step 2 orchestrator).

Pipelines raw `metrics` rows through the full chain:

    feature engine -> Isolation Forest -> explainability -> decision gate
                   -> action engine (+ rollback) -> retraining loop

and writes everything through SQLite (`windows`, `incidents`,
`model_versions`) so Step 3 can render it without further computation.

Modes:
    replay(db_path)              - process all existing metrics (backfill)
    live_loop(db_path, ...)      - poll for new metrics and act in real time
"""

import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from data_pipeline.src import db  # noqa: E402
from ai_brain.src import config as cfg  # noqa: E402
from ai_brain.src.action_engine import ActionEngine, classify_condition  # noqa: E402
from ai_brain.src.explain import Explainer  # noqa: E402
from ai_brain.src.feature_engine import FeatureEngine  # noqa: E402
from ai_brain.src.model import AnomalyModel, Retrainer  # noqa: E402
from ai_brain.src.trend import TrendLayer  # noqa: E402


class Brain:
    def __init__(self, db_path, cfg_=None, quiet=False):
        self.cfg = cfg_ or cfg
        self.quiet = quiet
        self.conn = db.connect(db_path)
        self.engine = FeatureEngine(self.cfg)
        self.model = AnomalyModel(self.cfg)  # model.model is None until trained
        self.retrainer = Retrainer(self.cfg)
        self.trend = TrendLayer(self.cfg)
        self.explainer = Explainer(self.cfg)
        self.actions = ActionEngine(self.cfg)
        self.flagged_ts = self._load_flagged()
        self.existing_windows = self._load_window_ts()
        self.new_since_retrain = 0
        self.recent_confs = []  # confidence of the last few scored windows
        self.recent_mets = []  # whether each recent window met the action threshold

    # -- startup state ------------------------------------------------------
    def _load_flagged(self):
        rows = self.conn.execute("SELECT timestamp FROM incidents").fetchall()
        return {r[0] for r in rows}

    def _load_window_ts(self):
        rows = self.conn.execute("SELECT start_ts FROM windows").fetchall()
        return {r[0] for r in rows}

    def _log(self, msg):
        if not self.quiet:
            print(f"[brain] {msg}", flush=True)

    # -- persistence ---------------------------------------------------------
    def _insert_window(self, start_ts, end_ts, vec):
        if start_ts in self.existing_windows:
            return
        self.conn.execute(
            "INSERT INTO windows(start_ts, end_ts, feature_vector) VALUES (?,?,?)",
            (start_ts, end_ts, json.dumps(vec)),
        )
        self.existing_windows.add(start_ts)
        self.conn.commit()

    def _log_model(self, candidate, validation):
        active = int(bool(candidate is not None and validation.get("accepted")))
        n = validation.get("n_train")
        self.conn.execute(
            "INSERT INTO model_versions(trained_at, n_samples, validation_result, active) "
            "VALUES (?,?,?,?)",
            (time.time(), n, json.dumps(validation), active),
        )
        self.conn.commit()
        if candidate is not None:
            self._log(
                f"model v{candidate.version}: "
                f"n_train={validation.get('n_train')} flag_rate={validation.get('flag_rate')} "
                f"accepted={validation.get('accepted')} ({validation.get('reason')})"
            )

    # -- training ------------------------------------------------------------
    def _normal_rows_from_table(self):
        rows = self.conn.execute(
            "SELECT start_ts, feature_vector FROM windows ORDER BY start_ts"
        ).fetchall()
        out = []
        for st, fv in rows:
            if st in self.flagged_ts:
                continue
            try:
                vec = json.loads(fv)
                x = [float(vec[f]) for f in self.cfg.FEATURE_ORDER]
            except (KeyError, TypeError, ValueError):
                continue
            out.append((st, x))
        return out[-self.cfg.RETRAIN_HISTORY:]

    def _anomalous_rows_from_table(self):
        """Feature vectors of windows that were flagged as incidents in the
        past - used to validate that a retrained candidate still detects the
        system's own known fault patterns (models that lose that ability are
        rejected, so self-healing never silently degrades)."""
        rows = self.conn.execute(
            "SELECT start_ts, feature_vector FROM windows ORDER BY start_ts"
        ).fetchall()
        out = []
        for st, fv in rows:
            if st not in self.flagged_ts:
                continue
            try:
                vec = json.loads(fv)
                x = [float(vec[f]) for f in self.cfg.FEATURE_ORDER]
            except (KeyError, TypeError, ValueError):
                continue
            out.append((st, x))
        return out[-self.cfg.RETRAIN_HISTORY:]

    def _try_initial_train(self):
        if self.model.model is not None:
            return False
        normal = self._normal_rows_from_table()
        if len(normal) < self.cfg.MIN_TRAIN:
            return False
        prefix = normal[:self.cfg.BASELINE_TRAIN]
        X = np.array([x for _, x in prefix])
        self.model.fit(X, self.cfg.FEATURE_ORDER)
        validation = {
            "n_train": len(X),
            "n_holdout": 0,
            "flag_rate": None,
            "accepted": True,
            "reason": "initial baseline training",
        }
        self._log_model(self.model, validation)
        self._trained_prefix_ts = {st for st, _ in prefix}
        self.new_since_retrain = 0
        return True

    def _maybe_retrain(self):
        normal = self._normal_rows_from_table()
        anomalous = self._anomalous_rows_from_table()
        candidate, validation = self.retrainer.retrain(
            normal, self.cfg.FEATURE_ORDER, anomalous
        )
        if candidate is not None and validation.get("accepted"):
            self.model = candidate
            self._log(f"retrained and promoted model v{candidate.version}")
        self._log_model(candidate, validation)
        self.new_since_retrain = 0

    # -- decision gate --------------------------------------------------------
    def _gate(self, conf, mag, warnings, sustained):
        if sustained and (conf >= self.cfg.ACTION_CONF or mag >= self.cfg.MAG_ACTION):
            return "action"
        if warnings and conf >= self.cfg.TREND_PUSH_ACTION_CONF:
            return "action"
        # A confidence-driven suggestion is only raised while the metric is
        # still actually deviant in the present (MAG_CONFIRM). Once the current
        # value returns to baseline the alarm clears immediately, even though
        # lag/rolling features may carry the fault's memory for ~a minute -
        # otherwise a recovered system keeps alarming (see MAG_CONFIRM).
        current_evidence = mag >= self.cfg.MAG_CONFIRM
        if warnings or (conf >= self.cfg.SUGGEST_CONF and current_evidence) \
                or mag >= self.cfg.MAG_SUGGEST:
            return "suggestion"
        return "normal"

    # -- monitoring -----------------------------------------------------------
    def _check_monitoring(self, live):
        conclusion = self.actions.monitor(self.engine)
        if conclusion is None:
            return
        inc_id = conclusion.get("incident_id")
        self.conn.execute(
            "UPDATE incidents SET outcome=?, rollback_used=? WHERE incident_id=?",
            (conclusion["status"], conclusion["rollback_used"], inc_id),
        )
        self.conn.commit()
        self._log(
            f"incident #{inc_id} -> {conclusion['status']} "
            f"(rollback={conclusion['rollback_used']}) {conclusion['detail']}"
        )

    # -- single window pipeline ----------------------------------------------
    def process_window(self, start_ts, end_ts, vec, live=False):
        x = np.array([float(vec[f]) for f in self.cfg.FEATURE_ORDER])

        if self.model.model is None:
            self._insert_window(start_ts, end_ts, vec)
            self._try_initial_train()
            self._check_monitoring(live)
            return {"status": "training"}

        if start_ts in self.flagged_ts:
            # already analysed in a previous run - do not duplicate incidents
            self._insert_window(start_ts, end_ts, vec)
            self._check_monitoring(live)
            return {"status": "replayed", "z": None}

        score = float(self.model.scores(x))
        z = float(self.model.z(x))
        conf = float(self.model.confidence(x))
        mag = float(self.model.deviation(x))
        warnings = self.trend.evaluate(self.engine.cur_series)
        met_action = conf >= self.cfg.ACTION_CONF or mag >= self.cfg.MAG_ACTION
        self.recent_confs.append(conf)
        self.recent_confs = self.recent_confs[-self.cfg.SUSTAIN_WINDOWS:]
        self.recent_mets.append(met_action)
        self.recent_mets = self.recent_mets[-self.cfg.SUSTAIN_WINDOWS:]
        sustained = sum(self.recent_mets) >= self.cfg.SUSTAIN_MIN
        decision = self._gate(conf, mag, warnings, sustained)
        self._insert_window(start_ts, end_ts, vec)
        self.new_since_retrain += 1

        if decision == "normal":
            self._check_monitoring(live)
            if self.new_since_retrain >= self.cfg.RETRAIN_EVERY:
                self._maybe_retrain()
            return {"status": "normal", "z": z}

        nl, drivers = self.explainer.explain(self.model, x, conf)
        driver_feature = drivers[0]["feature"] if drivers else None
        condition = classify_condition(driver_feature)
        similarity = self.explainer.historical_similarity(self.conn, self.model, x)
        if similarity:
            nl = nl + " " + similarity

        outcome, action_taken = self._decide_and_act(decision, condition, score, x, live, conf, mag)

        inc_id = self.conn.execute(
            "INSERT INTO incidents(timestamp, anomaly_score, explanation, "
            "action_taken, outcome, rollback_used) VALUES (?,?,?,?,?,0)",
            (start_ts, score, nl, json.dumps(action_taken), outcome),
        ).lastrowid
        self.conn.commit()
        self.flagged_ts.add(start_ts)

        self._log(
            f"window {start_ts:.0f} -> {decision} conf={conf * 100:.0f}% "
            f"mag={mag:.1f}sigma sustained={sustained} condition={condition} outcome={outcome} incident=#{inc_id}"
        )
        self._log(f"  explanation: {nl}")

        if outcome == "actioned":
            action = self.actions.actions[action_taken["action"]]
            pre_value = self.engine.cur_series.get(action.monitor_metric, [(0, 0.0)])[-1][1]
            self.actions.start_monitoring(inc_id, action, action_taken["snapshot_raw"], pre_value)

        self._check_monitoring(live)
        if self.new_since_retrain >= self.cfg.RETRAIN_EVERY:
            self._maybe_retrain()
        return {"status": decision, "z": z, "incident_id": inc_id,
                "explanation": nl, "condition": condition, "outcome": outcome}

    def _decide_and_act(self, decision, condition, score, x, live, conf=None, mag=None):
        zvec = self.model.zscore_vector(x).tolist()
        meta = {"confidence": conf, "deviation": mag, "score": score, "feature_vector_z": zvec}
        if decision != "action":
            return "suggestion", {"action": None, "condition": condition,
                                  "reason": "below action threshold", **meta}
        action, pid = self.actions.propose(condition, score)
        if action is None:
            return "suggestion", {"action": None, "condition": condition,
                                  "reason": "no actionable recovery", **meta}
        if not live:
            return "action_suggested", {"action": action.name, "condition": condition,
                                        "target_pid": pid, "reason": "replay (no live action)", **meta}
        snapshot, msg = self.actions.execute(action, pid)
        if snapshot is None:
            return "action_failed", {"action": action.name, "condition": condition,
                                     "target_pid": pid, "reason": msg, **meta}
        safe_snapshot = {k: v for k, v in snapshot.items() if k != "feature_vector_z"}
        return "actioned", {"action": action.name, "condition": condition, "target_pid": pid,
                            "snapshot": safe_snapshot, "snapshot_raw": snapshot, **meta}

    # -- entry points ---------------------------------------------------------
    def _build_train_score(self, live):
        """Build windows from all existing metrics, train on the available
        known-normal history, then score every later window."""
        windows = self.engine.poll(self.conn, force_complete=True)
        for (st, et, vec) in windows:
            self._insert_window(st, et, vec)
        self._log(f"pipeline: built {len(windows)} windows")

        self._try_initial_train()
        prefix_ts = getattr(self, "_trained_prefix_ts", set())
        for (st, et, vec) in windows:
            if st in prefix_ts:
                continue
            self.process_window(st, et, vec, live=live)
        self._try_initial_train()

    def bootstrap(self):
        self._build_train_score(live=False)

    def replay(self, dry_run=True):
        self._build_train_score(live=not dry_run)

    def live_loop(self, duration=None, poll_interval=1.0):
        since = self.engine.max_ts(self.conn)
        start = time.time()
        self._log(f"live loop started (poll {poll_interval}s, since {since:.1f})")
        while True:
            windows = self.engine.poll(self.conn, since_ts=since)
            for (st, et, vec) in windows:
                self.process_window(st, et, vec, live=True)
            since = self.engine.max_ts(self.conn)
            if duration is not None and (time.time() - start) > duration:
                break
            time.sleep(poll_interval)
