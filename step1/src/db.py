#!/usr/bin/env python3
"""
Storage layer for the Self-Healing OS data pipeline (Step 1).

Creates and owns the SQLite schema from the implementation plan:

    metrics(metric_name TEXT, timestamp REAL, value REAL, source TEXT)
    windows(window_id INTEGER PRIMARY KEY, start_ts REAL, end_ts REAL,
            feature_vector JSON)                         -- used in Step 2
    incidents(incident_id INTEGER PRIMARY KEY, timestamp REAL, anomaly_score REAL,
              explanation TEXT, action_taken TEXT, outcome TEXT, rollback_used BOOLEAN)
                                                         -- used in Step 2
    model_versions(version_id INTEGER PRIMARY KEY, trained_at REAL, n_samples INTEGER,
                   validation_result TEXT, active BOOLEAN)
                                                         -- used in Step 2

All four tables are created now so Step 2 can start writing immediately
without schema migration. Indexes are added on the metrics time-series
dimensions to keep windowed reads cheap.
"""

import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    metric_name TEXT,
    timestamp   REAL,
    value       REAL,
    source      TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_name_ts
    ON metrics(metric_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_ts
    ON metrics(timestamp);

CREATE TABLE IF NOT EXISTS windows (
    window_id      INTEGER PRIMARY KEY,
    start_ts       REAL,
    end_ts         REAL,
    feature_vector TEXT
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id    INTEGER PRIMARY KEY,
    timestamp      REAL,
    anomaly_score  REAL,
    explanation    TEXT,
    action_taken   TEXT,
    outcome        TEXT,
    rollback_used  BOOLEAN
);

CREATE TABLE IF NOT EXISTS model_versions (
    version_id        INTEGER PRIMARY KEY,
    trained_at         REAL,
    n_samples          INTEGER,
    validation_result  TEXT,
    active             BOOLEAN
);
"""


def connect(db_path):
    """Open a SQLite connection and ensure the schema exists."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def insert_metrics(conn, rows):
    """rows: iterable of (metric_name, timestamp, value, source)."""
    conn.executemany(
        "INSERT INTO metrics(metric_name, timestamp, value, source) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def insert_metric(conn, name, timestamp, value, source):
    conn.execute(
        "INSERT INTO metrics(metric_name, timestamp, value, source) "
        "VALUES (?, ?, ?, ?)",
        (name, timestamp, float(value), source),
    )
    conn.commit()


def now_ts():
    return time.time()


def stats(conn):
    """Row counts per table for a quick health check."""
    out = {}
    for table in ("metrics", "windows", "incidents", "model_versions"):
        out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    out["metrics_window_span"] = conn.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM metrics"
    ).fetchone()
    return out
