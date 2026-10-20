"""Transactional state + event history. A run lock serializes side effects."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

from .platforms import file_lock


class Store:
    def __init__(self, project: Path):
        self.root = project.expanduser().resolve() / ".foreman"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "state.sqlite3", timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, document TEXT NOT NULL, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL, timestamp REAL NOT NULL,
            kind TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS campaigns (
            id TEXT PRIMARY KEY, document TEXT NOT NULL, updated REAL NOT NULL
        );
        """)

    def close(self):
        self.db.close()

    def save(self, state: dict, kind: str, **payload):
        state["updated_at"] = time.time()
        with self.db:
            self.db.execute(
                "INSERT INTO runs VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "document=excluded.document, updated=excluded.updated",
                (state["id"], json.dumps(state, ensure_ascii=False, allow_nan=False), state["updated_at"]),
            )
            self.db.execute(
                "INSERT INTO events(run_id,timestamp,kind,payload) VALUES (?,?,?,?)",
                (state["id"], time.time(), kind, json.dumps(payload, ensure_ascii=False)),
            )

    def get(self, run_id: str) -> dict:
        row = self.db.execute("SELECT document FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown run: {run_id}")
        return json.loads(row[0])

    def events(self, run_id: str) -> list[dict]:
        return [dict(sequence=r[0], timestamp=r[1], kind=r[2], payload=json.loads(r[3]))
                for r in self.db.execute(
                    "SELECT sequence,timestamp,kind,payload FROM events WHERE run_id=? ORDER BY sequence",
                    (run_id,))]

    def runs(self) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute("SELECT document FROM runs ORDER BY updated DESC")]

    def save_campaign(self, state: dict, kind: str, **payload):
        state["updated_at"] = time.time()
        with self.db:
            self.db.execute(
                "INSERT INTO campaigns VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "document=excluded.document, updated=excluded.updated",
                (state["id"], json.dumps(state, ensure_ascii=False, allow_nan=False), state["updated_at"]),
            )
            self.db.execute("INSERT INTO events(run_id,timestamp,kind,payload) VALUES (?,?,?,?)",
                            ("campaign:" + state["id"], time.time(), kind, json.dumps(payload, ensure_ascii=False)))

    def get_campaign(self, campaign_id: str) -> dict:
        row = self.db.execute("SELECT document FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown campaign: {campaign_id}")
        return json.loads(row[0])

    def campaigns(self) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute("SELECT document FROM campaigns ORDER BY updated DESC")]


@contextmanager
def run_lock(run_dir: Path):
    try:
        with file_lock(run_dir / "manager.lock"):
            yield
    except BlockingIOError:
        raise ValueError("Another manager owns this run; use status or wait for it to exit.") from None
