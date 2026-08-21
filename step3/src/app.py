import sys
import os
import json
import sqlite3
import argparse
from collections import deque
from datetime import datetime

# PyQt6 and PyQtGraph imports
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, 
    QVBoxLayout, QHBoxLayout, QLabel, QStatusBar, 
    QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QGroupBox, QTextEdit, QPushButton, QMessageBox,
    QRadioButton, QSlider, QSystemTrayIcon, QStyle, QPlainTextEdit,
    QFrame, QScrollArea, QGridLayout,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont

# Modern dark theme (pure presentation - see theme.py)
from theme import (
    STYLE_SHEET, ACCENT, OK_TEXT, OK_BAR, TEXT_DIM,
    style_chart, severity_palette, progress_bar_qss,
    subsystem_color, unit_for, humanize, rgba, friendly_name,
)

# pyqtgraph powers the live chart tab. It is intentionally optional: without
# it the rest of the dashboard still works and the missing dependency is called
# out in the UI rather than crashing the whole app.
try:
    import pyqtgraph as pg
    HAS_PYQTGRAPH = True
except ImportError:
    pg = None
    HAS_PYQTGRAPH = False

if HAS_PYQTGRAPH:
    pg.setConfigOptions(antialias=True)

# Database path
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../step1/data/self_healing_os.db'))

# ---------------------------------------------------------------------------
# MetricCard: Task-Manager-style live mini-chart for a single metric
# ---------------------------------------------------------------------------
if HAS_PYQTGRAPH:

    class MetricCard(QFrame):
        """One self-contained live chart card with a rolling 60s window."""

        WINDOW = 60  # seconds visible

        def __init__(self, name, parent=None):
            super().__init__(parent)
            self.metric_name = name
            self.unit = unit_for(name)
            self.color = subsystem_color(name)

            self.setObjectName("metricCard")
            lay = QVBoxLayout(self)
            lay.setContentsMargins(14, 10, 14, 10)
            lay.setSpacing(4)

            # title row
            top = QHBoxLayout()
            top.setSpacing(8)
            title = QLabel(friendly_name(name))
            title.setObjectName("metricTitle")
            title.setMinimumWidth(100)
            title.setToolTip(name)
            badge = QLabel(self.unit)
            badge.setStyleSheet(
                f"color: {self.color}; background-color: {rgba(self.color, 0.12)};"
                f"border:1px solid {rgba(self.color, 0.35)}; border-radius:9px;"
                f"padding:2px 8px; font-size:10px; font-weight:700;"
            )
            top.addWidget(title)
            top.addStretch()
            top.addWidget(badge)
            lay.addLayout(top)

            # big current value
            self.value_label = QLabel("—")
            self.value_label.setObjectName("metricValue")
            lay.addWidget(self.value_label)

            # plot
            self.plot = pg.PlotWidget(background=None)
            style_chart(self.plot)
            self.plot.setMouseEnabled(x=False, y=False)
            self.plot.hideButtons()
            self.plot.setFixedHeight(130)
            self.plot.getPlotItem().setMenuEnabled(False)
            self.plot.setXRange(-self.WINDOW, 0, padding=0)
            self.plot.enableAutoRange(x=False, y=True)

            c = QColor(self.color)
            fill_c = QColor(c); fill_c.setAlpha(38)
            pen = pg.mkPen(color=self.color, width=2)
            self.curve = self.plot.plot(
                [], [], pen=pen,
                fillLevel=0, brush=pg.mkBrush(fill_c),
                symbol="o", symbolSize=3,
                symbolBrush=pg.mkBrush(self.color),
                symbolPen=pg.mkPen((0, 0, 0, 0)),
            )
            lay.addWidget(self.plot)

        def update_data(self, timestamps, values):
            if not values:
                return
            latest = timestamps[-1]
            x = [t - latest for t in timestamps]
            self.curve.setData(x, values)
            self.value_label.setText(humanize(values[-1], self.unit))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Self-Healing OS — Observability & Autonomous Healing Console")
        self.resize(1280, 800)
        self.setMinimumSize(1024, 680)

        # ── header bar + tabs container ──
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        bar_lay = QHBoxLayout(top_bar)
        bar_lay.setContentsMargins(20, 10, 20, 10)
        title_lbl = QLabel("Self-Healing OS")
        title_lbl.setObjectName("appTitle")
        sub_lbl = QLabel("Observability & Autonomous Healing Console")
        sub_lbl.setObjectName("appSubtitle")
        self.live_pill = QLabel("● MONITORING")
        self.live_pill.setObjectName("livePill")
        bar_lay.addWidget(title_lbl)
        bar_lay.addWidget(sub_lbl)
        bar_lay.addStretch()
        bar_lay.addWidget(self.live_pill)
        outer.addWidget(top_bar)

        # Main Tab Widget
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        self.setCentralWidget(container)

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
        
        # Setup Auto-Refresh Timer (Refreshes every 1 second)
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self._refresh_all_data)
        self.refresh_timer.start(1000) 

        # Initial fetch immediately on startup
        self._refresh_all_data() 

    def _style_table(self, table):
        """Shared modern look for data tables (presentation only)."""
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)

    def _init_live_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        header = QLabel("Real-Time eBPF Metrics Stream")
        header.setObjectName("pageHeader")
        layout.addWidget(header)

        # ── stat cards row ──
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.stat_cards = {}
        for caption in ("Events captured", "Active sources", "Busiest source"):
            frame = QFrame()
            frame.setObjectName("statCard")
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(16, 12, 16, 12)
            fl.setSpacing(4)
            val = QLabel("—")
            val.setObjectName("statValue")
            cap = QLabel(caption)
            cap.setObjectName("statCaption")
            fl.addWidget(val)
            fl.addWidget(cap)
            stats_row.addWidget(frame)
            self.stat_cards[caption] = val
        stats_row.addStretch()
        layout.addLayout(stats_row)

        self.live_table = QTableWidget()
        self.live_table.setColumnCount(4)
        self.live_table.setHorizontalHeaderLabels(["Timestamp", "Metric Name", "Source (PID/Dev/IRQ)", "Value"])
        self._style_table(self.live_table)

        header_view = self.live_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        
        layout.addWidget(self.live_table)
        self.tab_live.setLayout(layout)

    def _init_metrics_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        header = QLabel("Time-Series Visualization")
        header.setObjectName("pageHeader")

        sub = QLabel("Live · rolling 60 s window · auto-refresh 1 s")
        sub.setObjectName("pageSubtitle")

        top_row = QHBoxLayout()
        top_row.addWidget(header)
        top_row.addStretch()
        top_row.addWidget(sub)
        layout.addLayout(top_row)

        if HAS_PYQTGRAPH:
            self._metric_cards = {}
            self._metrics_built = False

            host = QWidget()
            host.setStyleSheet("background:transparent;")
            self._metrics_grid = QGridLayout(host)
            self._metrics_grid.setSpacing(12)
            self._metrics_grid.setContentsMargins(0, 0, 0, 0)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(host)
            scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
            layout.addWidget(scroll)
        else:
            missing = QLabel(
                "pyqtgraph is not installed, so live charts are unavailable.\n\n"
                "Install it to enable real-time plotting:\n"
                "    pip install pyqtgraph\n\n"
                "The Live View, Incident History, AI Insights and Controls tabs "
                "still work normally."
            )
            missing.setWordWrap(True)
            missing.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(missing)

        self.tab_metrics.setLayout(layout)

    def _init_history_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        header = QLabel("Incident & Action Audit Log")
        header.setObjectName("pageHeader")
        layout.addWidget(header)

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(7)
        self.history_table.setHorizontalHeaderLabels([
            "ID", "Time", "Score", "Explanation (XAI)", "Action Taken", "Outcome", "Rollback?"
        ])
        self._style_table(self.history_table)
        
        header_view = self.history_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        
        layout.addWidget(self.history_table)
        self.tab_history.setLayout(layout)

    def _init_ai_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        header = QLabel("AI Decision Gate & Explainability (XAI)")
        header.setObjectName("pageHeader")
        layout.addWidget(header)

        # 1. Anomaly Gauge Box
        gauge_box = QGroupBox("Current Threat & Anomaly Level")
        gauge_layout = QVBoxLayout()

        self.status_label = QLabel("System State: NORMAL")
        self.status_label.setFont(QFont("Sans", 12, QFont.Weight.Bold))
        self.status_label.setStyleSheet(f"color: {OK_TEXT};")

        self.anomaly_bar = QProgressBar()
        self.anomaly_bar.setRange(0, 100)
        self.anomaly_bar.setValue(0)
        self.anomaly_bar.setTextVisible(True)
        self.anomaly_bar.setFormat("Anomaly Score: %v / 100")
        self.anomaly_bar.setStyleSheet(progress_bar_qss(OK_BAR))
        
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
        self.btn_manual_action.setObjectName("primaryButton")
        self.btn_manual_action.setFont(QFont("Sans", 10, QFont.Weight.Bold))
        self.btn_manual_action.setEnabled(False)
        self.btn_manual_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_manual_action.clicked.connect(self._on_manual_action_clicked)
        
        gate_layout.addWidget(self.suggestion_label)
        gate_layout.addWidget(self.btn_manual_action)
        gate_box.setLayout(gate_layout)
        layout.addWidget(gate_box)

        layout.addStretch()
        self.tab_ai.setLayout(layout)

    def _init_settings_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        header = QLabel("System Configuration & Controls")
        header.setObjectName("pageHeader")
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
        self.notifications_feed.setObjectName("notificationsFeed")
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
        self.btn_clear_history.setObjectName("dangerButton")
        self.btn_clear_history.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_history.clicked.connect(self._clear_incident_history)
        
        db_layout.addWidget(self.btn_clear_history)
        db_box.setLayout(db_layout)
        layout.addWidget(db_box)

        layout.addStretch()
        self.tab_settings.setLayout(layout)

    def _refresh_all_data(self):
        self._refresh_live_data()
        self._refresh_plot_data()
        self._refresh_history_data()
        self._refresh_ai_data()
        self._check_new_notifications()

    def _refresh_live_data(self):
        if not os.path.exists(DB_PATH):
            self.status_bar.showMessage(f"Error: SQLite DB not found at {DB_PATH}")
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

            # update stat cards
            from collections import Counter
            sources = [str(r[2]) for r in rows if r[2]]
            top_src = Counter(sources).most_common(1) if sources else [("—", 0)]
            self.stat_cards["Events captured"].setText(str(len(rows)))
            self.stat_cards["Active sources"].setText(str(len(set(sources))))
            self.stat_cards["Busiest source"].setText(top_src[0][0][:18])

            self.status_bar.showMessage(f"System Status: Monitoring Active | Last Updated: {datetime.now().strftime('%H:%M:%S')}")
        except sqlite3.Error as e:
            self.status_bar.showMessage(f"SQLite Error: {e}")

    def _refresh_plot_data(self):
        if not HAS_PYQTGRAPH or not os.path.exists(DB_PATH):
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()

            # Discover metrics & build cards on first call
            if not self._metrics_built:
                cur.execute("SELECT DISTINCT metric_name FROM metrics ORDER BY metric_name")
                all_names = [r[0] for r in cur.fetchall()]
                if all_names:
                    order = {"sched": 0, "mem": 1, "blk": 2, "irq": 3}
                    all_names.sort(key=lambda n: (order.get(n.split("_", 1)[0], 9), n))
                    cols = self._metrics_grid.columnCount()
                    for idx, name in enumerate(all_names):
                        card = MetricCard(name)
                        r, c = divmod(idx, cols)
                        self._metrics_grid.addWidget(card, r, c)
                        self._metric_cards[name] = card
                    self._metrics_built = True
                else:
                    conn.close()
                    return

            # Fetch last 65 s of all metrics in one query (anchored to latest
            # timestamp in the DB so charts work for both live and historical data)
            cur.execute("SELECT MAX(timestamp) FROM metrics")
            latest = cur.fetchone()[0]
            if latest is None:
                conn.close()
                return
            cutoff = latest - 65
            cur.execute(
                "SELECT metric_name, timestamp, value FROM metrics WHERE timestamp >= ? ORDER BY timestamp ASC",
                (cutoff,),
            )
            rows = cur.fetchall()
            conn.close()

            buckets = {}
            for m, ts, v in rows:
                buckets.setdefault(m, ([], []))
                buckets[m][0].append(ts)
                buckets[m][1].append(v)

            for name, card in self._metric_cards.items():
                ts_list, vals = buckets.get(name, ([], []))
                card.update_data(ts_list, vals)

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
                    outcome_item.setForeground(QColor("#34d399"))
                elif outcome_str.lower() == 'failed':
                    outcome_item.setForeground(QColor("#f87171"))
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
                bar_color = severity_palette(conf_pct)[1]
                self.anomaly_bar.setStyleSheet(progress_bar_qss(bar_color))
                self.xai_text.setText(str(explanation or ""))
                self.suggestion_label.setText(f"Suggested Recovery Action: {action_txt}")

                if conf_pct >= 70:
                    self.status_label.setText("System State: CRITICAL / ANOMALY DETECTED")
                    self.status_label.setStyleSheet(f"color: {severity_palette(conf_pct)[0]};")
                    self.btn_manual_action.setEnabled(True)
                elif conf_pct >= 30:
                    self.status_label.setText("System State: WARNING / ELEVATED LATENCY")
                    self.status_label.setStyleSheet(f"color: {severity_palette(conf_pct)[0]};")
                    self.btn_manual_action.setEnabled(True)
                else:
                    self._reset_ai_panel_to_normal()
            else:
                self._reset_ai_panel_to_normal()

        except sqlite3.Error:
            pass

    def _reset_ai_panel_to_normal(self):
        self.status_label.setText("System State: NORMAL")
        self.status_label.setStyleSheet(f"color: {OK_TEXT};")
        self.anomaly_bar.setValue(5)
        self.anomaly_bar.setStyleSheet(progress_bar_qss(OK_BAR))
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

def main():
    """CLI entry point.  Accepts --db to override the SQLite database path."""
    global DB_PATH

    parser = argparse.ArgumentParser(
        description="Self-Healing OS — Observability & Autonomous Healing Console",
    )
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help="Path to the SQLite database (default: step1/data/self_healing_os.db)",
    )
    args = parser.parse_args()
    DB_PATH = os.path.abspath(args.db)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(["Inter", "SF Pro Display", "Segoe UI", "Ubuntu", "Noto Sans"])
    font.setPointSize(10)
    app.setFont(font)
    app.setStyleSheet(STYLE_SHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()