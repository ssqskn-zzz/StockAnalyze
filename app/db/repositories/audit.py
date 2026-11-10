"""复核留痕：任何确认、改值、争议、发布、复核动作都写入 review_records。"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.utils import now_iso


def add(
    conn: sqlite3.Connection,
    *,
    object_type: str,
    object_id: int,
    action: str,
    reason: str = "",
    actor: str = "user",
    old_value: Any = None,
    new_value: Any = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO review_records
           (object_type, object_id, action, old_value, new_value, reason, actor, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            object_type, object_id, action,
            None if old_value is None else str(old_value),
            None if new_value is None else str(new_value),
            reason, actor, now_iso(),
        ),
    )
    return int(cur.lastrowid)


def list_for(conn: sqlite3.Connection, object_type: str, object_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM review_records WHERE object_type=? AND object_id=? ORDER BY id DESC",
        (object_type, object_id),
    ))


def list_recent(conn: sqlite3.Connection, company_id: int | None = None, limit: int = 50) -> list[sqlite3.Row]:
    if company_id:
        return list(conn.execute(
            """SELECT r.* FROM review_records r
               LEFT JOIN metric_values mv ON r.object_type='metric_value' AND mv.id=r.object_id
               LEFT JOIN report_sections rs ON r.object_type='report_section' AND rs.id=r.object_id
               LEFT JOIN report_versions rv ON rs.version_id=rv.id
               WHERE mv.company_id=? OR rv.company_id=?
               ORDER BY r.id DESC LIMIT ?""",
            (company_id, company_id, limit),
        ))
    return list(conn.execute("SELECT * FROM review_records ORDER BY id DESC LIMIT ?", (limit,)))
