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
        _migrate_reports(conn)
        conn.commit()


def _migrate_reports(conn: sqlite3.Connection) -> None:
    """轻量迁移：报告正文外置为 Markdown 文件后，旧库需补列并把 body 改为可空。

    schema.sql 使用 CREATE TABLE IF NOT EXISTS，已有库不会自动改结构，这里做幂等补齐。
    """
    version_cols = {row["name"] for row in conn.execute("PRAGMA table_info(report_versions)")}
    if "file_path" not in version_cols:
        conn.execute("ALTER TABLE report_versions ADD COLUMN file_path TEXT")
    if "content_hash" not in version_cols:
        conn.execute("ALTER TABLE report_versions ADD COLUMN content_hash TEXT")

    body_col = next(
        (row for row in conn.execute("PRAGMA table_info(report_sections)") if row["name"] == "body"),
        None,
    )
    if body_col is None or not body_col["notnull"]:
        return
    # SQLite 不支持 ALTER COLUMN，重建表以去掉 body 的 NOT NULL 约束。
    conn.execute("ALTER TABLE report_sections RENAME TO report_sections_legacy")
    conn.execute(
        """CREATE TABLE report_sections (
               id               INTEGER PRIMARY KEY AUTOINCREMENT,
               version_id       INTEGER NOT NULL REFERENCES report_versions(id) ON DELETE CASCADE,
               part_no          INTEGER NOT NULL,
               part_code        TEXT    NOT NULL,
               title            TEXT    NOT NULL,
               body             TEXT,
               change_type      TEXT    NOT NULL DEFAULT 'new'
                                CHECK (change_type IN ('new', 'carried', 'data_refresh', 'conclusion_rewrite', 'deleted')),
               carry_over_from  INTEGER,
               cited_value_ids  TEXT,
               review_status    TEXT    NOT NULL DEFAULT 'not_required'
                                CHECK (review_status IN ('not_required', 'pending', 'still_valid', 'needs_rewrite', 'invalid')),
               review_reason    TEXT,
               reviewed_at      TEXT,
               reviewed_by      TEXT,
               UNIQUE (version_id, part_code)
           )"""
    )
    conn.execute(
        """INSERT INTO report_sections
           (id, version_id, part_no, part_code, title, body, change_type, carry_over_from,
            cited_value_ids, review_status, review_reason, reviewed_at, reviewed_by)
           SELECT id, version_id, part_no, part_code, title, body, change_type, carry_over_from,
                  cited_value_ids, review_status, review_reason, reviewed_at, reviewed_by
           FROM report_sections_legacy"""
    )
    conn.execute("DROP TABLE report_sections_legacy")


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
