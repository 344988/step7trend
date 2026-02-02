from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from app.drivers.s7_driver import TagSpec


class WorkspaceStorage:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._configure()
        self._create_tables()

    def _configure(self) -> None:
        with self.conn:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")

    def _create_tables(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tags (
                    name TEXT PRIMARY KEY,
                    area TEXT NOT NULL,
                    db INTEGER NOT NULL,
                    byte_index INTEGER NOT NULL,
                    bit_index INTEGER,
                    data_type TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tag_name TEXT NOT NULL,
                    ts REAL NOT NULL,
                    value REAL NOT NULL
                )
                """
            )
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_samples_tag_ts ON samples(tag_name, ts)")

    def upsert_tags(self, tags: Iterable[TagSpec]) -> None:
        rows = []
        for tag in tags:
            payload = asdict(tag)
            rows.append(
                (
                    payload["name"],
                    payload["area"],
                    payload["db"],
                    payload["byte_index"],
                    payload["bit_index"],
                    payload["data_type"],
                )
            )
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO tags (name, area, db, byte_index, bit_index, data_type)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    area=excluded.area,
                    db=excluded.db,
                    byte_index=excluded.byte_index,
                    bit_index=excluded.bit_index,
                    data_type=excluded.data_type
                """,
                rows,
            )

    def list_tags(self) -> List[str]:
        cur = self.conn.execute("SELECT name FROM tags ORDER BY name")
        return [row["name"] for row in cur.fetchall()]

    def insert_samples(self, samples: Iterable[Tuple[str, float, float]]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO samples (tag_name, ts, value) VALUES (?, ?, ?)",
                samples,
            )

    def insert_sample(self, tag_name: str, value: float, ts: Optional[float] = None) -> None:
        if ts is None:
            ts = time.time()
        self.insert_samples([(tag_name, ts, float(value))])

    def get_latest_values(self, tag_names: Optional[List[str]] = None) -> dict:
        params = []
        where = ""
        if tag_names:
            placeholders = ",".join(["?"] * len(tag_names))
            where = f"WHERE tag_name IN ({placeholders})"
            params.extend(tag_names)
        query = f"""
            SELECT tag_name, value, MAX(ts) as ts
            FROM samples
            {where}
            GROUP BY tag_name
        """
        cur = self.conn.execute(query, params)
        return {row["tag_name"]: row["value"] for row in cur.fetchall()}

    def get_series(self, tag_name: str, since_ts: float, limit: int = 500) -> List[Tuple[float, float]]:
        cur = self.conn.execute(
            """
            SELECT ts, value
            FROM samples
            WHERE tag_name = ? AND ts >= ?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (tag_name, since_ts, limit),
        )
        rows = cur.fetchall()
        return [(row["ts"], row["value"]) for row in reversed(rows)]
