"""SQLite 连接与初始化。

P0 使用标准库 sqlite3，所有 SQL 收敛在仓储层（app/db/repositories），
便于后续迁移到 PostgreSQL（schema.sql 未使用 SQLite 专有类型）。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app import config

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str | Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()


@contextmanager
def session(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
