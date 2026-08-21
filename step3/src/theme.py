"""Modern dark theme for the Self-Healing OS dashboard.

Pure presentation layer: QSS stylesheet, palette constants and small helpers.
No business logic lives here - app.py behaviour is unchanged by this module.
"""

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
BG          = "#0b1220"   # app background (deep navy-slate)
CARD        = "#111a2e"   # card / groupbox surface
CARD_ALT    = "#0d1526"   # table / editor / chart surface
BORDER      = "#1e293b"   # hairline borders
BORDER_SOFT = "#334155"   # hover / stronger borders
TEXT        = "#e2e8f0"   # primary text
TEXT_DIM    = "#94a3b8"   # secondary text
TEXT_TITLE  = "#f1f5f9"   # headings

ACCENT      = "#22d3ee"   # cyan - charts, highlights
ACCENT_2    = "#818cf8"   # indigo - secondary accents

OK_TEXT     = "#34d399"
OK_BAR      = "#10b981"
WARN_TEXT   = "#fbbf24"
WARN_BAR    = "#f59e0b"
CRIT_TEXT   = "#f87171"
CRIT_BAR    = "#ef4444"

FONT_STACK  = "'Inter', 'SF Pro Display', 'Segoe UI', 'Ubuntu', 'Noto Sans', sans-serif"
MONO_STACK  = "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'DejaVu Sans Mono', monospace"


# ---------------------------------------------------------------------------
# Tiny helpers used inside the stylesheet f-string
# ---------------------------------------------------------------------------
def rgba(hex_color, alpha):
    """Qt-stylesheet rgba() string from a #rrggbb colour."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


# ---------------------------------------------------------------------------
# Global stylesheet
# ---------------------------------------------------------------------------
STYLE_SHEET = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: {FONT_STACK};
    font-size: 13px;
}}

QToolTip {{
    background-color: {BORDER};
    color: {TEXT};
    border: 1px solid {BORDER_SOFT};
    border-radius: 6px;
    padding: 6px;
}}

/* ---------------- page headers ---------------- */
QLabel#pageHeader {{
    font-size: 17px;
    font-weight: 700;
    color: {TEXT_TITLE};
}}
QLabel#pageSubtitle {{
    font-size: 12px;
    color: {TEXT_DIM};
}}

/* ---------------- tabs ---------------- */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 12px;
    top: -1px;
    background-color: {BG};
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_DIM};
    padding: 9px 20px;
    margin-right: 6px;
    border-radius: 9px;
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {TEXT};
    background-color: rgba(148, 163, 184, 0.08);
}}
QTabBar::tab:selected {{
    color: {TEXT_TITLE};
    background-color: {CARD};
    border: 1px solid {BORDER_SOFT};
    border-bottom: 2px solid {ACCENT};
}}

/* ---------------- cards (group boxes) ---------------- */
QGroupBox {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
    margin-top: 16px;
    padding: 14px 14px 14px 14px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    top: 1px;
    padding: 0 6px;
    background-color: {BG};
    color: {ACCENT};
    font-weight: 700;
}}

/* ---------------- buttons ---------------- */
QPushButton {{
    background-color: {CARD};
    color: {TEXT};
    border: 1px solid {BORDER_SOFT};
    border-radius: 9px;
    padding: 9px 18px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: #26334d;
    border-color: #475569;
}}
QPushButton:pressed {{
    background-color: #1a2439;
}}
QPushButton:disabled {{
    background-color: {CARD};
    color: #475569;
    border-color: {BORDER};
}}
QPushButton#primaryButton {{
    background-color: #0891b2;
    border: none;
    color: #ffffff;
}}
QPushButton#primaryButton:hover {{
    background-color: #06b6d4;
}}
QPushButton#primaryButton:pressed {{
    background-color: #0e7490;
}}
QPushButton#primaryButton:disabled {{
    background-color: #164e63;
    color: #67e8f9;
}}
QPushButton#dangerButton {{
    background-color: transparent;
    border: 1px solid #7f1d1d;
    color: #fca5a5;
}}
QPushButton#dangerButton:hover {{
    background-color: rgba(239, 68, 68, 0.12);
    border-color: #b91c1c;
}}

/* ---------------- tables ---------------- */
QTableWidget {{
    background-color: {CARD_ALT};
    alternate-background-color: #101a30;
    gridline-color: transparent;
    border: 1px solid {BORDER};
    border-radius: 10px;
    selection-background-color: #155e75;
    selection-color: #ecfeff;
    padding: 4px;
}}
QTableWidget::item {{
    padding: 7px 10px;
    border-bottom: 1px solid #14203a;
}}
QTableWidget::item:selected {{
    background-color: #155e75;
}}
QHeaderView::section {{
    background-color: #0f1830;
    color: #93c5fd;
    padding: 9px 10px;
    border: none;
    border-bottom: 2px solid {BORDER};
    font-weight: 700;
}}
QTableCornerButton::section {{
    background-color: #0f1830;
    border: none;
}}

/* ---------------- inputs ---------------- */
QComboBox {{
    background-color: #16213c;
    border: 1px solid {BORDER_SOFT};
    border-radius: 9px;
    padding: 7px 14px;
    color: {TEXT};
    min-width: 200px;
    font-weight: 600;
}}
QComboBox:hover {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
QComboBox::down-arrow {{
    width: 0;
    height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {TEXT_DIM};
    margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    background-color: {CARD};
    border: 1px solid {BORDER_SOFT};
    border-radius: 9px;
    color: {TEXT};
    selection-background-color: #155e75;
    outline: none;
    padding: 4px;
}}

QRadioButton {{
    spacing: 10px;
    padding: 5px 2px;
    color: {TEXT};
}}
QRadioButton::indicator {{
    width: 17px;
    height: 17px;
    border-radius: 9px;
    border: 2px solid {BORDER_SOFT};
    background-color: {CARD_ALT};
}}
QRadioButton::indicator:hover {{
    border-color: {ACCENT};
}}
QRadioButton::indicator:checked {{
    border: 5px solid {ACCENT};
    background-color: {BG};
}}

QSlider::groove:horizontal {{
    height: 6px;
    background-color: {BORDER};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {ACCENT}, stop:1 {ACCENT_2});
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    width: 17px;
    height: 17px;
    margin: -6px 0;
    border-radius: 9px;
    background-color: {TEXT};
}}
QSlider::handle:horizontal:hover {{
    background-color: #ffffff;
}}

/* ---------------- progress bar (base look; recoloured per severity) --- */
QProgressBar {{
    background-color: {CARD_ALT};
    border: 1px solid {BORDER};
    border-radius: 10px;
    color: {TEXT};
    font-weight: 700;
    text-align: center;
    min-height: 20px;
}}
QProgressBar::chunk {{
    border-radius: 9px;
    background-color: {OK_BAR};
}}

/* ---------------- text editors ---------------- */
QTextEdit, QPlainTextEdit {{
    background-color: {CARD_ALT};
    border: 1px solid {BORDER};
    border-radius: 10px;
    color: #dbeafe;
    padding: 8px;
    selection-background-color: #155e75;
}}
QPlainTextEdit#notificationsFeed {{
    font-family: {MONO_STACK};
    font-size: 12px;
    color: #bae6fd;
}}

/* ---------------- status bar & scrollbars ---------------- */
QStatusBar {{
    background-color: {BG};
    color: {TEXT_DIM};
    border-top: 1px solid {BORDER};
    font-size: 12px;
}}
QStatusBar::item {{
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background-color: {BORDER_SOFT};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: #475569;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background-color: {BORDER_SOFT};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: #475569;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

QMessageBox {{
    background-color: {CARD};
}}
QMessageBox QLabel {{
    color: {TEXT};
    background-color: transparent;
}}

/* ---------------- top bar ---------------- */
QFrame#topBar {{
    background-color: {CARD};
    border-bottom: 1px solid {BORDER};
}}
QLabel#appTitle {{
    font-size: 16px;
    font-weight: 700;
    color: {TEXT_TITLE};
}}
QLabel#appSubtitle {{
    font-size: 11px;
    color: {TEXT_DIM};
}}
QLabel#livePill {{
    color: {OK_TEXT};
    background-color: {rgba(OK_TEXT, 0.12)};
    border: 1px solid {rgba(OK_TEXT, 0.35)};
    border-radius: 10px;
    padding: 4px 12px;
    font-size: 11px;
    font-weight: 700;
}}

/* ---------------- metric cards ---------------- */
QFrame#metricCard {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#metricCard:hover {{
    border-color: {BORDER_SOFT};
}}
QLabel#metricTitle {{
    color: {TEXT_DIM};
    font-size: 11px;
    font-weight: 700;
}}
QLabel#metricValue {{
    font-family: {MONO_STACK};
    font-size: 20px;
    font-weight: 700;
    color: {TEXT_TITLE};
}}

/* ---------------- stat cards ---------------- */
QFrame#statCard {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QLabel#statCaption {{
    color: {TEXT_DIM};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#statValue {{
    font-family: {MONO_STACK};
    font-size: 22px;
    font-weight: 700;
    color: {TEXT_TITLE};
}}
"""


def severity_palette(conf_pct):
    """Map an anomaly confidence percentage to (text_color, bar_color)."""
    if conf_pct >= 70:
        return CRIT_TEXT, CRIT_BAR
    if conf_pct >= 30:
        return WARN_TEXT, WARN_BAR
    return OK_TEXT, OK_BAR


def progress_bar_qss(bar_color):
    """Full per-state stylesheet for the anomaly gauge so the chunk colour
    tracks severity while keeping the card look consistent."""
    return f"""
    QProgressBar {{
        background-color: {CARD_ALT};
        border: 1px solid {BORDER};
        border-radius: 10px;
        color: {TEXT};
        font-weight: 700;
        text-align: center;
        min-height: 20px;
    }}
    QProgressBar::chunk {{
        border-radius: 9px;
        background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {bar_color}, stop:1 {ACCENT});
    }}
    """


def style_chart(plot_widget):
    """Apply the dark theme to a pyqtgraph PlotWidget (cosmetics only)."""
    plot_widget.setBackground(CARD_ALT)

    for key in ("left", "bottom"):
        axis = plot_widget.getAxis(key)
        axis.setPen(pg_pen_border())
        axis.setTextPen(pg_pen_dim())

    plot_widget.showGrid(x=True, y=True, alpha=0.15)


def pg_pen_border():
    import pyqtgraph as pg
    return pg.mkPen(color=BORDER_SOFT, width=1)


def pg_pen_dim():
    import pyqtgraph as pg
    return pg.mkPen(color=TEXT_DIM, width=1)


# ---------------------------------------------------------------------------
# Metric presentation helpers (units, colors, value formatting)
# ---------------------------------------------------------------------------
SUBSYSTEM_COLORS = {
    "sched": "#22d3ee",   # cyan  - scheduler
    "mem":   "#a78bfa",   # violet- memory
    "blk":   "#fbbf24",   # amber - block I/O
    "irq":   "#fb7185",   # rose  - interrupts
}


def subsystem_color(metric_name):
    """Pick the family colour for a metric from its prefix."""
    return SUBSYSTEM_COLORS.get(metric_name.split("_", 1)[0], ACCENT)


def unit_for(metric_name):
    """Human unit for a metric, derived from its name."""
    name = metric_name.lower()
    if name.endswith("_us"):
        return "µs"
    if "bytes" in name:
        return "B"
    return "events"


# Friendly display names so non-technical users can read the dashboard
METRIC_LABELS = {
    # block I/O
    "blk_bytes_total":         "Data Read / Written",
    "blk_io_errors":           "Disk Errors",
    "blk_io_lat_avg_us":       "Disk Latency (Avg)",
    "blk_io_lat_cnt":          "Disk I/O Ops",
    "blk_io_lat_max_us":       "Disk Latency (Peak)",
    "blk_io_ops_per_dev":      "Disk Ops per Device",
    "blk_io_ops_total":        "Disk I/O Ops Total",
    # interrupts
    "irq_handled_per_line":    "Interrupts per Line",
    "irq_handled_total":       "Interrupts Total",
    "irq_lat_avg_us":          "Interrupt Latency (Avg)",
    "irq_lat_cnt":             "Interrupt Count",
    "irq_lat_max_us":          "Interrupt Latency (Peak)",
    # memory
    "mem_oom_kills":           "Out-of-Memory Kills",
    "mem_page_faults_kernel":  "Kernel Page Faults",
    "mem_page_faults_per_comm":"Page Faults per Process",
    "mem_page_faults_total":   "Total Page Faults",
    "mem_page_faults_user":    "User Page Faults",
    # scheduler
    "sched_ctx_switches":      "Context Switches",
    "sched_preemptions":       "Preemptions",
    "sched_runq_lat_avg_us":   "CPU Queue Wait (Avg)",
    "sched_runq_lat_cnt":      "CPU Queue Events",
    "sched_runq_lat_max_us":   "CPU Queue Wait (Peak)",
    "sched_switches_per_comm": "Switches per Process",
}


def friendly_name(metric_name):
    """Return a human-readable label for a metric, falling back to the raw name."""
    return METRIC_LABELS.get(metric_name, metric_name)


def humanize(value, unit):
    """Format a metric value with sensible SI-style scaling."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return f"— {unit}"
    sign = "-" if v < 0 else ""
    v = abs(v)
    if unit == "B":
        for div, suf in ((1024**3, "GB"), (1024**2, "MB"), (1024, "KB")):
            if v >= div:
                return f"{sign}{v / div:.1f} {suf}"
        return f"{sign}{v:.0f} B"
    if unit == "µs":
        if v >= 1000:
            return f"{sign}{v / 1000:.1f} ms"
        return f"{sign}{v:.0f} µs"
    if v >= 1_000_000:
        return f"{sign}{v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{sign}{v / 1_000:.1f}K"
    return f"{sign}{v:.0f}"
