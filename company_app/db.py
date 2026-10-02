"""SQLite storage for the fake internal system."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "acme.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor TEXT NOT NULL,
    invoice_number TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    notes TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

SEED = [
    ("Globex Corporation", "GLX-2026-0815", 18250.00, "INR", "2026-09-01", "open", "seed"),
    ("Initech Pvt Ltd", "INI-4471", 7400.00, "INR", "2026-09-20", "open", "seed"),
    ("Umbrella Supplies", "UMB-0099", 3120.50, "INR", "2026-11-15", "paid", "seed"),
]


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path | None = None, reset: bool = False) -> None:
    p = path or DB_PATH
    if reset and p.exists():
        p.unlink()
    conn = connect(p)
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO invoices (vendor, invoice_number, amount, currency, due_date, status,"
            " created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            SEED,
        )
    conn.commit()
    conn.close()
