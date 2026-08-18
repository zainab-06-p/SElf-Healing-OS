# Self-Healing OS: Frontend & Integration Handoff

This document details the components built and tested during **Step 3 (Observability Frontend)** and **Step 4 (End-to-End Integration)**. The backend (eBPF collector, AI models, and action scripts) is fully integrated with a real-time, multi-threaded PyQt6 dashboard.

---

## 🖥️ Step 3: The Observability Dashboard (`step3/src/app.py`)

The user interface was built using `PyQt6` and `pyqtgraph`. It reads live data directly from the central SQLite database (`step1/data/self_healing_os.db`) without blocking the main event loop, using a 2-second auto-refresh timer. 

The dashboard consists of 5 fully functional tabs:

### 1. 📊 Live View
*   **Purpose:** Displays the raw, real-time metric stream captured by the eBPF collector.
*   **Features:** A `QTableWidget` that fetches and displays the 50 most recent entries from the `metrics` table, including timestamps, metric names, sources (PID/Dev), and values.

### 2. 📈 Metrics & Charts
*   **Purpose:** Provides real-time, time-series visualization to visually prove the system is self-healing (e.g., showing a CPU spike returning to baseline).
*   **Features:** 
    *   A dynamic dropdown menu that auto-populates based on distinct metrics found in the database.
    *   A `pyqtgraph` canvas optimized for live, high-speed data plotting.
    *   Displays a rolling 60-second window of the selected metric.

### 3. 📜 Incident History
*   **Purpose:** Acts as the system's audit log and proves to judges/users that the autonomous actions were successful.
*   **Features:** 
    *   Reads directly from the `incidents` table.
    *   Displays the Anomaly Score, Explainable AI (XAI) explanation, and Action Taken.
    *   **Color-coded outcomes:** Outcomes marked "resolved" appear in green, while "failed" appear in red.

### 4. 🧠 AI Insights & Suggestions (XAI)
*   **Purpose:** Translates the AI's numerical outputs into human-readable insights.
*   **Features:**
    *   **Threat Gauge:** A dynamic progress bar indicating the current anomaly score out of 100.
    *   **XAI Panel:** A text box displaying the natural language feature attribution (e.g., "Run-queue latency 13.3x above baseline").
    *   **Decision Gate:** Dynamic state labels that change to ORANGE (Warning) or RED (Critical) based on score thresholds.
    *   **Manual Trigger:** A button that activates when an anomaly is detected, allowing a human operator to execute the AI's suggested action.

### 5. ⚙️ System & Controls
*   **Purpose:** A control panel for managing the system's state during demos.
*   **Features:**
    *   **Mode Toggles:** Radio buttons to switch between "Observer Mode" and "Autonomous Mode".
    *   **Confidence Threshold:** A slider to adjust the minimum AI confidence required before taking action.
    *   **DB Management:** A functional "Clear Incident History" button that safely wipes the `incidents` table and instantly refreshes the UI (crucial for resetting between demo runs).

---

## 🧪 Step 4: End-to-End Integration & Rehearsal

We successfully verified the entire data pipeline—from the Linux kernel up to the GUI—using a 4-terminal setup. 

To run the live demonstration, execute the following commands in four separate terminals:

### Terminal 1: The Senses (eBPF Collector)
Starts the data collection from the kernel and populates the SQLite database.
```bash
sudo python3 step1/src/collector.py
```

### Terminal 2: The Face (GUI Dashboard)
Launches the observability UI.

```bash
python3 step3/src/app.py
```
### Terminal 3: The Brain (AI Orchestrator)
Starts the scikit-learn Isolation Forest anomaly detection and Action Engine. Needs the --db argument.

```bash
python3 step2/scripts/run_brain.py --db ../../step1/data/self_healing_os.db
```

### Terminal 4: The Chaos (Safe Fault Injection)
Triggers a synthetic load (e.g., CPU starvation) to force the AI to react.
Note: We wrap this in timeout and nice to prevent the VM from hanging during live demos.

```bash
timeout 30s nice -n 15 python3 step2/scripts/fault_inject.py --role shos_cpuhog
```

### ✅ Expected Behavior During Demo
1. Terminal 4 initiates a CPU load.

2. The UI Metrics Tab line chart spikes significantly.

3. The UI AI Tab gauge turns RED and prints the root cause.

4. Terminal 3 (Brain) detects the spike, generates an anomaly score, and executes renice.

5. The UI Metrics Tab line chart plummets back to baseline.

6. The UI History Tab populates a new green "Resolved" incident row.

### 📦 Dependencies Added
Ensure the following are installed on the deployment machine/VM:

- PyQt6 (for the dashboard UI)
- pyqtgraph (for time-series charting)
- scikit-learn (for the Isolation Forest AI model)
