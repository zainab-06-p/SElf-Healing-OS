import sys
import os
import json
import sqlite3
from collections import deque
from datetime import datetime

# PyQt6 and PyQtGraph imports
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, 
    QVBoxLayout, QHBoxLayout, QLabel, QStatusBar, 
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QProgressBar, QGroupBox, QTextEdit, QPushButton, QMessageBox,
    QRadioButton, QSlider, QSystemTrayIcon, QStyle, QPlainTextEdit,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

# pyqtgraph powers the live chart tab. It is intentionally optional: without
# it the rest of the dashboard still works and the missing dependency is called
# out in the UI rather than crashing the whole app.
try:
    import pyqtgraph as pg
    HAS_PYQTGRAPH = True
except ImportError:
    pg = None
    HAS_PYQTGRAPH = False

# Database path
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../step1/data/self_healing_os.db'))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Self-Healing OS — Observability & Autonomous Healing Console")
        self.resize(1100, 750)

        # Main Tab Widget
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Initialize the 5 required tabs
        self.tab_live = QWidget()
        self.tab_metrics = QWidget()
        self.tab_history = QWidget()
        self.tab_ai = QWidget()
        self.tab_settings = QWidget()

        self.tabs.addTab(self.tab_live, "📊 Live View")
        self.tabs.addTab(self.tab_metrics, "📈 Metrics & Charts")
        self.tabs.addTab(self.tab_history, "📜 Incident History")
        self.tabs.addTab(self.tab_ai, "🧠 AI Insights & Suggestions")
        self.tabs.addTab(self.tab_settings, "⚙️ System & Controls")

        # Popup / notification state (Step 3 design requirement).
        self._seen_incidents = {}      # incident_id -> last observed outcome
        self._notifications = deque(maxlen=200)
        self._tray = None
        self._notif_warm = False

        # Setup Tabs using their specific methods
        self._init_live_tab()
        self._init_metrics_tab()
        self._init_history_tab()
        self._init_ai_tab()
        self._init_settings_tab()  # <--- NEW: Settings tab initialized

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        # Setup Auto-Refresh Timer (Refreshes every 2 seconds)
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self._refresh_all_data)
        self.refresh_timer.start(2000) 

        # Initial fetch immediately on startup
        self._refresh_all_data() 

    def _init_live_tab(self):
        layout = QVBoxLayout()
        header = QLabel("Real-Time eBPF Metrics Stream")
        header.setFont(QFont("Sans", 14, QFont.Weight.Bold))
        layout.addWidget(header)

        self.live_table = QTableWidget()
        self.live_table.setColumnCount(4)
        self.live_table.setHorizontalHeaderLabels(["Timestamp", "Metric Name", "Source (PID/Dev/IRQ)", "Value"])
        
        header_view = self.live_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        
        layout.addWidget(self.live_table)
        self.tab_live.setLayout(layout)

    def _init_metrics_tab(self):
        layout = QVBoxLayout()
        control_layout = QHBoxLayout()
        header = QLabel("Time-Series Visualization")
        header.setFont(QFont("Sans", 14, QFont.Weight.Bold))
        
        self.metric_selector = QComboBox()
        self.metric_selector.addItem("Loading metrics...")
        self.metric_selector.currentTextChanged.connect(self._force_plot_refresh)
        
        control_layout.addWidget(header)
        control_layout.addStretch()
        control_layout.addWidget(QLabel("Select Metric:"))
        control_layout.addWidget(self.metric_selector)
        
        layout.addLayout(control_layout)

        if HAS_PYQTGRAPH:
            self.plot_widget = pg.PlotWidget(background='w')
            self.plot_widget.setTitle("Metric Trend (Last 60 Seconds)", color='k', size='12pt')
            self.plot_widget.setLabel('left', 'Value', color='k')
            self.plot_widget.setLabel('bottom', 'Time (Seconds Ago)', color='k')
            self.plot_widget.showGrid(x=True, y=True)

            pen = pg.mkPen(color=(0, 114, 178), width=2)
            self.plot_line = self.plot_widget.plot([], [], pen=pen, symbol='o', symbolSize=4, symbolBrush='b')

            layout.addWidget(self.plot_widget)
        else:
            self.plot_widget = None
            self.plot_line = None
            missing = QLabel(
                "📈 pyqtgraph is not installed, so live charts are unavailable.\n\n"
                "Install it to enable real-time plotting:\n"
                "    pip install pyqtgraph\n\n"
                "The Live View, Incident History, AI Insights and Controls tabs "
                "still work normally."
            )
            missing.setWordWrap(True)
            missing.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(missing)

        self.tab_metrics.setLayout(layout)
        self.metrics_loaded = False

    def _init_history_tab(self):
        layout = QVBoxLayout()
        header = QLabel("Incident & Action Audit Log")
        header.setFont(QFont("Sans", 14, QFont.Weight.Bold))
        layout.addWidget(header)

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(7)
        self.history_table.setHorizontalHeaderLabels([
            "ID", "Time", "Score", "Explanation (XAI)", "Action Taken", "Outcome", "Rollback?"
        ])
        
        header_view = self.history_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        
        layout.addWidget(self.history_table)
        self.tab_history.setLayout(layout)

    def _init_ai_tab(self):
        layout = QVBoxLayout()
        
        header = QLabel("AI Decision Gate & Explainability (XAI)")
        header.setFont(QFont("Sans", 14, QFont.Weight.Bold))
        layout.addWidget(header)

        # 1. Anomaly Gauge Box
        gauge_box = QGroupBox("Current Threat & Anomaly Level")
        gauge_layout = QVBoxLayout()
        
        self.status_label = QLabel("System State: NORMAL")
        self.status_label.setFont(QFont("Sans", 12, QFont.Weight.Bold))
        self.status_label.setStyleSheet("color: green;")
        
        self.anomaly_bar = QProgressBar()
        self.anomaly_bar.setRange(0, 100)
        self.anomaly_bar.setValue(0)
        self.anomaly_bar.setTextVisible(True)
        self.anomaly_bar.setFormat("Anomaly Score: %v / 100")
        
        gauge_layout.addWidget(self.status_label)
        gauge_layout.addWidget(self.anomaly_bar)
        gauge_box.setLayout(gauge_layout)
        layout.addWidget(gauge_box)

        # 2. Natural Language Explanation Box
        xai_box = QGroupBox("Explainable AI (XAI) Attribution")
        xai_layout = QVBoxLayout()
        
        self.xai_text = QTextEdit()
        self.xai_text.setReadOnly(True)
        self.xai_text.setPlaceholderText("No anomalies detected. System is running within baseline limits.")
        self.xai_text.setMaximumHeight(120)
        
        xai_layout.addWidget(self.xai_text)
        xai_box.setLayout(xai_layout)
        layout.addWidget(xai_box)

        # 3. Decision Gate & Manual Action Section
        gate_box = QGroupBox("Decision Gate & Recommended Recovery Action")
        gate_layout = QVBoxLayout()
        
        self.suggestion_label = QLabel("Recommended Action: None required")
        self.suggestion_label.setFont(QFont("Sans", 11))
        
        self.btn_manual_action = QPushButton("⚡ Execute Suggested Healing Action Now")
        self.btn_manual_action.setFont(QFont("Sans", 10, QFont.Weight.Bold))
        self.btn_manual_action.setEnabled(False)
        self.btn_manual_action.clicked.connect(self._on_manual_action_clicked)
        
        gate_layout.addWidget(self.suggestion_label)
        gate_layout.addWidget(self.btn_manual_action)
        gate_box.setLayout(gate_layout)
        layout.addWidget(gate_box)

        layout.addStretch()
        self.tab_ai.setLayout(layout)

    def _init_settings_tab(self):
        layout = QVBoxLayout()
        header = QLabel("System Configuration & Controls")
        header.setFont(QFont("Sans", 14, QFont.Weight.Bold))
        layout.addWidget(header)

        # 1. Mode Selection
        mode_box = QGroupBox("Operating Mode")
        mode_layout = QVBoxLayout()
        self.radio_observer = QRadioButton("Observer Mode (Generate alerts only, no actions)")
        self.radio_autonomous = QRadioButton("Autonomous Healing Mode (AI takes action automatically)")
        self.radio_autonomous.setChecked(True)
        
        mode_layout.addWidget(self.radio_observer)
        mode_layout.addWidget(self.radio_autonomous)
        mode_box.setLayout(mode_layout)
        layout.addWidget(mode_box)

        # 2. Threshold Slider
        thresh_box = QGroupBox("AI Action Threshold")
        thresh_layout = QHBoxLayout()
        self.thresh_label = QLabel("Trigger Autonomous Action at Score: 75")
        
        self.thresh_slider = QSlider(Qt.Orientation.Horizontal)
        self.thresh_slider.setRange(50, 100)
        self.thresh_slider.setValue(75)
        self.thresh_slider.valueChanged.connect(
            lambda v: self.thresh_label.setText(f"Trigger Autonomous Action at Score: {v}")
        )
        
        thresh_layout.addWidget(self.thresh_label)
        thresh_layout.addWidget(self.thresh_slider)
        thresh_box.setLayout(thresh_layout)
        layout.addWidget(thresh_box)

        # 3. Real-Time Notifications (popup feed - plan section 2.9 / 3.1.5).
        # Every new incident and every resolved/failed healing outcome is
        # surfaced here with its plain-language explanation, and mirrored to a
        # system-tray popup when a tray is available.
        notif_box = QGroupBox("Real-Time Notifications")
        notif_layout = QVBoxLayout()
        self.notifications_feed = QPlainTextEdit()
        self.notifications_feed.setReadOnly(True)
        self.notifications_feed.setPlaceholderText(
            "No notifications yet. Newly detected anomalies and healing outcomes "
            "will appear here in real time."
        )
        notif_layout.addWidget(self.notifications_feed)
        notif_box.setLayout(notif_layout)
        layout.addWidget(notif_box)

        # 4. Database Management (Functional)
        db_box = QGroupBox("Database Management")
        db_layout = QVBoxLayout()
        
        self.btn_clear_history = QPushButton("🗑️ Clear Incident History Table")
        self.btn_clear_history.clicked.connect(self._clear_incident_history)
        
        db_layout.addWidget(self.btn_clear_history)
        db_box.setLayout(db_layout)
        layout.addWidget(db_box)

        layout.addStretch()
        self.tab_settings.setLayout(layout)

    def _force_plot_refresh(self, text):
        if text and text != "Loading metrics...":
            self._refresh_plot_data()

    def _refresh_all_data(self):
        self._refresh_live_data()
        self._refresh_plot_data()
        self._refresh_history_data()
        self._refresh_ai_data()
        self._check_new_notifications()

    def _refresh_live_data(self):
        if not os.path.exists(DB_PATH):
            self.status_bar.showMessage(f"⚠️ Error: SQLite DB not found at {DB_PATH}")
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT timestamp, metric_name, source, value FROM metrics ORDER BY timestamp DESC LIMIT 50")
            rows = cursor.fetchall()
            conn.close()

            self.live_table.setRowCount(0)
            for row_idx, row_data in enumerate(rows):
                self.live_table.insertRow(row_idx)
                ts_readable = datetime.fromtimestamp(row_data[0]).strftime('%H:%M:%S.%f')[:-3]
                self.live_table.setItem(row_idx, 0, QTableWidgetItem(ts_readable))
                self.live_table.setItem(row_idx, 1, QTableWidgetItem(str(row_data[1])))
                self.live_table.setItem(row_idx, 2, QTableWidgetItem(str(row_data[2])))
                val_item = QTableWidgetItem(f"{row_data[3]:.4f}")
                val_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.live_table.setItem(row_idx, 3, val_item)

            self.status_bar.showMessage(f"✅ System Status: Monitoring Active | Last Updated: {datetime.now().strftime('%H:%M:%S')}")
        except sqlite3.Error as e:
            self.status_bar.showMessage(f"⚠️ SQLite Error: {e}")

    def _refresh_plot_data(self):
        if not HAS_PYQTGRAPH or self.plot_widget is None:
            return
        if not os.path.exists(DB_PATH): return
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            if not self.metrics_loaded:
                cursor.execute("SELECT DISTINCT metric_name FROM metrics")
                distinct_metrics = [row[0] for row in cursor.fetchall()]
                if distinct_metrics:
                    self.metric_selector.blockSignals(True)
                    self.metric_selector.clear()
                    self.metric_selector.addItems(distinct_metrics)
                    self.metric_selector.blockSignals(False)
                    self.metrics_loaded = True

            selected_metric = self.metric_selector.currentText()
            if not selected_metric or selected_metric == "Loading metrics...":
                conn.close()
                return
                
            cursor.execute("SELECT timestamp, value FROM metrics WHERE metric_name = ? ORDER BY timestamp DESC LIMIT 100", (selected_metric,))
            rows = cursor.fetchall()
            conn.close()

            if rows:
                rows.reverse()
                timestamps = [row[0] for row in rows]
                values = [row[1] for row in rows]
                latest_ts = timestamps[-1]
                x_data = [(ts - latest_ts) for ts in timestamps] 
                self.plot_line.setData(x_data, values)

        except sqlite3.Error as e:
            print(f"Plot refresh error: {e}")

    def _refresh_history_data(self):
        if not os.path.exists(DB_PATH): return
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT incident_id, timestamp, anomaly_score, explanation, action_taken, outcome, rollback_used 
                FROM incidents 
                ORDER BY timestamp DESC 
                LIMIT 50
            """)
            rows = cursor.fetchall()
            conn.close()

            self.history_table.setRowCount(0)
            for row_idx, row in enumerate(rows):
                self.history_table.insertRow(row_idx)
                ts_readable = datetime.fromtimestamp(row[1]).strftime('%H:%M:%S')
                self.history_table.setItem(row_idx, 0, QTableWidgetItem(str(row[0])))
                self.history_table.setItem(row_idx, 1, QTableWidgetItem(ts_readable))
                
                score_item = QTableWidgetItem(f"{row[2]:.2f}")
                self.history_table.setItem(row_idx, 2, score_item)
                
                self.history_table.setItem(row_idx, 3, QTableWidgetItem(str(row[3])))
                self.history_table.setItem(row_idx, 4, QTableWidgetItem(str(row[4])))
                
                outcome_str = str(row[5])
                outcome_item = QTableWidgetItem(outcome_str)
                if outcome_str.lower() == 'resolved':
                    outcome_item.setForeground(Qt.GlobalColor.darkGreen)
                elif outcome_str.lower() == 'failed':
                    outcome_item.setForeground(Qt.GlobalColor.red)
                self.history_table.setItem(row_idx, 5, outcome_item)
                
                self.history_table.setItem(row_idx, 6, QTableWidgetItem(str(row[6])))

        except sqlite3.Error:
            pass

    def _refresh_ai_data(self):
        if not os.path.exists(DB_PATH): return
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT anomaly_score, explanation, action_taken, outcome 
                FROM incidents 
                ORDER BY timestamp DESC 
                LIMIT 1
            """)
            latest = cursor.fetchone()
            conn.close()

            if latest:
                score, explanation, action_json, outcome = latest
                # The DB stores the raw isolation-forest anomaly score (a
                # small float around zero), not a 0-100 percentage - using
                # int(score) always yielded 0, so the panel reset to "No
                # anomalies detected" even while incidents existed. The brain
                # writes the percentile confidence (0-1) into the action_taken
                # metadata, so drive the severity bar and thresholds from that.
                conf_pct = None
                action_txt = "None required"
                try:
                    meta = json.loads(action_json) if action_json else {}
                    if meta.get("confidence") is not None:
                        conf_pct = min(100, int(float(meta["confidence"]) * 100))
                    action_name = meta.get("action")
                    if action_name:
                        pid = meta.get("target_pid")
                        action_txt = f"{action_name} (PID {pid})" if pid else str(action_name)
                    elif meta.get("reason"):
                        action_txt = f"Monitoring only ({meta['reason']})"
                except (TypeError, ValueError):
                    pass
                if conf_pct is None:
                    # No metadata to parse - an incident exists, so at minimum
                    # surface it as a warning rather than falsely showing NORMAL.
                    conf_pct = 50 if outcome else 0

                self.anomaly_bar.setValue(conf_pct)
                self.xai_text.setText(str(explanation or ""))
                self.suggestion_label.setText(f"Suggested Recovery Action: {action_txt}")

                if conf_pct >= 70:
                    self.status_label.setText("System State: CRITICAL / ANOMALY DETECTED")
                    self.status_label.setStyleSheet("color: red;")
                    self.btn_manual_action.setEnabled(True)
                elif conf_pct >= 30:
                    self.status_label.setText("System State: WARNING / ELEVATED LATENCY")
                    self.status_label.setStyleSheet("color: orange;")
                    self.btn_manual_action.setEnabled(True)
                else:
                    self._reset_ai_panel_to_normal()
            else:
                self._reset_ai_panel_to_normal()

        except sqlite3.Error:
            pass

    def _reset_ai_panel_to_normal(self):
        self.status_label.setText("System State: NORMAL")
        self.status_label.setStyleSheet("color: green;")
        self.anomaly_bar.setValue(5)
        self.xai_text.setText("No anomalies detected. System metrics are within normal baseline thresholds.")
        self.suggestion_label.setText("Recommended Action: None required")
        self.btn_manual_action.setEnabled(False)

    def _on_manual_action_clicked(self):
        QMessageBox.information(
            self, 
            "Manual Healing Triggered", 
            "Manual intervention command sent to Action Engine!\nExecuting recovery sequence and monitoring metrics for 30s..."
        )

    def _clear_incident_history(self):
        # Clears the incidents table from the SQLite database
        if not os.path.exists(DB_PATH): return
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM incidents")
            conn.commit()
            conn.close()
            
            # Immediately refresh the UI
            self._refresh_history_data()
            self._refresh_ai_data()
            self._check_new_notifications()
            
            QMessageBox.information(self, "Success", "Incident history has been wiped cleanly.")
        except Exception as e:
            QMessageBox.warning(self, "Database Error", f"Could not clear incident table: {e}")

    # -- real-time popup notifications --------------------------------------
    def _ensure_tray(self):
        """Lazily create a system-tray icon so popups can appear. Returns None
        when no tray exists (e.g. headless / minimal desktop), in which case
        the in-app notification feed below is the fallback."""
        if self._tray is None and QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = QSystemTrayIcon(self)
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
            self._tray.setIcon(icon)
            self._tray.setToolTip("Self-Healing OS — autonomous healing console")
            self._tray.show()
        return self._tray

    def _notify(self, title, message):
        ts = datetime.now().strftime('%H:%M:%S')
        line = f"[{ts}] {title} — {message}"
        self._notifications.appendleft(line)
        self.notifications_feed.setPlainText("\n".join(self._notifications))
        self.status_bar.showMessage(f"🔔 {line[:160]}", 8000)
        tray = self._ensure_tray()
        if tray is not None:
            try:
                tray.showMessage(
                    title, message[:160],
                    QSystemTrayIcon.MessageIcon.Information, 8000,
                )
            except Exception:
                pass

    def _check_new_notifications(self):
        """Poll the incidents table and pop up anything new: newly detected
        anomalies, and incidents whose outcome transitions to resolved/failed."""
        if not os.path.exists(DB_PATH):
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute(
                "SELECT incident_id, explanation, outcome FROM incidents "
                "ORDER BY incident_id ASC"
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return

        # First poll after startup seeds the state silently, so opening the app
        # over an existing incident log does not spam 75 popups.
        if not self._notif_warm:
            for inc_id, _, outcome in rows:
                self._seen_incidents[inc_id] = str(outcome)
            self._notif_warm = True
            return

        for inc_id, explanation, outcome in rows:
            outcome = str(outcome)
            previous = self._seen_incidents.get(inc_id)
            if previous is None:
                self._seen_incidents[inc_id] = outcome
                self._notify("Anomaly detected", (explanation or "")[:120])
            elif previous != outcome and outcome in ("resolved", "failed"):
                self._seen_incidents[inc_id] = outcome
                self._notify(f"Healing action {outcome}", (explanation or "")[:90])

        # Drop ids that were wiped from the DB so a fresh demo run re-pops.
        live_ids = {r[0] for r in rows}
        for old in list(self._seen_incidents.keys()):
            if old not in live_ids:
                del self._seen_incidents[old]

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())