"""All mutable state lives in a single SQLite database."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path("state/groupmate.db")


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        with conn:
            yield conn
    finally:
        conn.close()


def get(key, default=None):
    with connect() as db:
        row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(key, value):
    with connect() as db:
        db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, json.dumps(value, ensure_ascii=False)))


def load_last_uid(key):
    return get(f"uid:{key}", 0)


def save_last_uid(uid, key):
    put(f"uid:{key}", uid)
