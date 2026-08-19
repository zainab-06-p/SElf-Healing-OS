"""Action engine and rollback (Section 2.7).

Safety-critical component. Every recovery action:
  * lives in a hard allowlist (no arbitrary shell execution),
  * stores a pre-action state snapshot before doing anything,
  * has a paired rollback that restores the snapshot,
  * is rate-limited per target via a cooldown window,
  * is followed by a ~30s post-action monitoring window that marks the
    incident resolved (improved) or failed+rolled back (worsened / no change).

Registered actions (map detected condition -> reversible action):
    cpu    -> renice the offending process        (restore previous priority)
    io     -> ionice the offending process        (restore previous I/O class)
    memory -> restart the offending process       (rollback = cooldown rule:
             never restart the same target again within the cooldown window)
    irq    -> (no automated action; suggestion only)
"""

import os
import re
import shutil
import subprocess
import sys
import time
from functools import partial

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from step2.src import config as cfg  # noqa: E402

FAULT_INJECT_SCRIPT = os.path.join(ROOT, "step2", "scripts", "fault_inject.py")

CONDITION_ACTIONS = {
    "cpu": "renice",
    "io": "ionice",
    "memory": "restart",
    "irq": None,
}

CONDITION_MONITOR_METRIC = {
    "cpu": "sched_runq_lat_avg_us",
    "io": "blk_bytes_total",
    "memory": "mem_page_faults_total",
}


def classify_condition(driver_feature):
    """Map a top driver feature name to a detected condition."""
    base = driver_feature.split("__")[0]
    if base in ("mem_page_faults_total", "mem_oom_kills"):
        return "memory"
    if base.startswith("sched_"):
        return "cpu"
    if base.startswith("blk_"):
        return "io"
    if base.startswith("irq_"):
        return "irq"
    return None


def find_target_pid(marker):
    """Return the pid of the process the recovery action should act on.

    Fault-inject roles run as a manager that spawns a ``--child`` process which
    actually runs the workload; both argv lines contain the ``shos_*`` marker.
    The child must be preferred so actions hit the real workload: acting on the
    manager would leave the busy loop running (renice) or orphan a still-leaking
    process (restart).
    """
    fallback = None
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = f.read().split(b"\x00")
        except (OSError, FileNotFoundError):
            continue
        if marker.encode() not in argv:
            continue
        if b"--child" in argv:
            return int(pid)
        if fallback is None:
            fallback = int(pid)
    return fallback


class Action:
    def __init__(self, name, condition, marker, monitor_metric, execute, snapshot, rollback, relaunch=None):
        self.name = name
        self.condition = condition
        self.marker = marker
        self.monitor_metric = monitor_metric
        self.execute = execute
        self.snapshot = snapshot
        self.rollback = rollback
        self.relaunch = relaunch
        self.enabled = True


IONICE_CLASS_NAMES = {"none": 0, "realtime": 1, "best-effort": 2, "best_effort": 2, "idle": 3}


def _read_ionice(pid):
    if not shutil.which("ionice"):
        return None
    out = subprocess.run(["ionice", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    m = re.search(r"([a-z\-]+):\s*prio\s*(\d+)", out)
    if m:
        return {"class": IONICE_CLASS_NAMES.get(m.group(1), 0), "level": int(m.group(2))}
    if out in IONICE_CLASS_NAMES:  # "idle" with no prio
        return {"class": IONICE_CLASS_NAMES[out], "level": 0}
    return None


def _apply_ionice(pid, klass, level=None):
    cmd = ["ionice", "-c", str(klass)]
    if level is not None:
        cmd += ["-n", str(level)]
    cmd += ["-p", str(pid)]
    return subprocess.run(cmd, capture_output=True, text=True).returncode == 0


# -- action implementations ---------------------------------------------------

def _snap_renice(pid):
    try:
        return {"pid": pid, "priority_before": os.getpriority(os.PRIO_PROCESS, pid)}
    except OSError:
        return None


def _exec_renice(snapshot):
    pid = snapshot["pid"]
    new_nice = min(19, snapshot["priority_before"] + 10)
    os.setpriority(os.PRIO_PROCESS, pid, new_nice)
    return True


def _rollback_renice(snapshot):
    os.setpriority(os.PRIO_PROCESS, snapshot["pid"], snapshot["priority_before"])
    return True


def _snap_ionice(pid):
    state = _read_ionice(pid)
    if state is None:
        return None
    return {"pid": pid, "ionice_before": state}


def _exec_ionice(snapshot):
    return _apply_ionice(snapshot["pid"], "3")  # idle I/O class


def _rollback_ionice(snapshot):
    before = snapshot["ionice_before"]
    return _apply_ionice(snapshot["pid"], before["class"], before.get("level"))


def _snap_restart(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            fields = f.read().split()
        return {"pid": pid, "start_time": fields[21]}
    except (OSError, IndexError):
        return None


def _exec_restart(snapshot, marker):
    pid = snapshot["pid"]
    try:
        os.kill(pid, 15)  # SIGTERM
    except ProcessLookupError:
        pass
    if not shutil.which("python3"):
        return False
    subprocess.Popen(
        [sys.executable, FAULT_INJECT_SCRIPT, "--role", marker, "--child"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True


def _rollback_restart(snapshot):
    """Nothing to restore - the cooldown rule prevents immediate re-restarts."""
    return False


class ActionEngine:
    def __init__(self, cfg_=None):
        self.cfg = cfg_ or cfg
        self.actions = {
            "renice": Action(
                "renice", "cpu", "shos_cpuhog", "sched_runq_lat_avg_us",
                _exec_renice, _snap_renice, _rollback_renice,
            ),
            "ionice": Action(
                "ionice", "io", "shos_iostress", "blk_bytes_total",
                _exec_ionice, _snap_ionice, _rollback_ionice,
            ),
            "restart": Action(
                "restart", "memory", "shos_memleak", "mem_page_faults_total",
                partial(_exec_restart, marker="shos_memleak"),
                _snap_restart, _rollback_restart,
            ),
        }
        self.last_executed = {}   # action name -> monotonic timestamp
        self.active = None        # _Active record during a monitoring window

    # -- cooldown ------------------------------------------------------------
    def _in_cooldown(self, action):
        last = self.last_executed.get(action.name)
        return last is not None and (time.monotonic() - last) < self.cfg.COOLDOWN

    def propose(self, condition, anomaly_z=None, driver_feature=None):
        """Return (action, target_pid) if an action should fire, else (None, reason)."""
        name = CONDITION_ACTIONS.get(condition)
        if name is None:
            return None, "no automated action registered for this condition"
        action = self.actions.get(name)
        if action is None or not action.enabled:
            return None, f"action '{name}' not enabled"
        if self._in_cooldown(action):
            return None, f"action '{name}' in cooldown"
        pid = find_target_pid(action.marker)
        if pid is None:
            return None, f"target '{action.marker}' not running"
        return action, pid

    def execute(self, action, pid):
        snapshot = action.snapshot(pid)
        if snapshot is None:
            return None, "snapshot failed"
        try:
            ok = action.execute(snapshot)
        except OSError as exc:
            return None, f"execute failed: {exc}"
        if not ok:
            return None, "execute returned failure"
        self.last_executed[action.name] = time.monotonic()
        return snapshot, "executed"

    def rollback(self, action, snapshot):
        try:
            restored = action.rollback(snapshot)
        except OSError:
            restored = False
        return restored

    # -- post-action monitoring ---------------------------------------------
    def start_monitoring(self, incident_id, action, snapshot, pre_value):
        self.active = {
            "incident_id": incident_id,
            "action": action,
            "snapshot": snapshot,
            "metric": action.monitor_metric,
            "pre_value": pre_value,
            "windows_seen": 0,
            "decided": False,
        }
        return self.active

    def monitor(self, engine):
        """Check the monitoring window. Returns a conclusion dict or None."""
        if not self.active or self.active["decided"]:
            return None
        act = self.active
        vals = engine.cur_series.get(act["metric"], [])
        if not vals:
            return None
        cur = vals[-1][1]
        pre = act["pre_value"]
        act["windows_seen"] += 1
        snap = act["snapshot"]

        if cur <= self.cfg.IMPROVE_RATIO * max(pre, 1e-9):
            act["decided"] = True
            return {"status": "resolved", "incident_id": act["incident_id"], "rollback_used": 0,
                    "detail": f"{act['metric']} {cur:.0f} <= {self.cfg.IMPROVE_RATIO}x pre-action {pre:.0f}"}
        if act["windows_seen"] >= 2 and cur > 1.5 * pre:
            act["decided"] = True
            restored = self.rollback(act["action"], snap)
            return {"status": "failed", "incident_id": act["incident_id"],
                    "rollback_used": int(restored),
                    "detail": f"metric worsened: {cur:.0f} > 1.5x pre-action {pre:.0f}"}
        if act["windows_seen"] >= self.cfg.MONITOR_WINDOWS:
            act["decided"] = True
            restored = self.rollback(act["action"], snap)
            return {"status": "failed", "incident_id": act["incident_id"],
                    "rollback_used": int(restored),
                    "detail": "no improvement within monitoring window"}
        return None
