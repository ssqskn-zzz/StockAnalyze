"""数据快照仓储：S{n} 是报告与估值的版本引用锚点。"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.utils import json_dumps, json_loads, now_iso

_VALID_STATUS = ("confirmed", "locked")


def list_snapshots(conn: sqlite3.Connection, company_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM snapshots WHERE company_id=? ORDER BY id DESC", (company_id,)
    ))


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()


def get_snapshot_by_code(conn: sqlite3.Connection, company_id: int, code: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM snapshots WHERE company_id=? AND code=?", (company_id, code)
    ).fetchone()


def latest_snapshot(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM snapshots WHERE company_id=? ORDER BY id DESC LIMIT 1", (company_id,)
    ).fetchone()


def create_snapshot(
    conn: sqlite3.Connection,
    company_id: int,
    *,
    reason: str,
    note: str = "",
    created_by: str = "assistant",
) -> int:
    """冻结"当前已确认的指标值"为 S{n}。只增不改。"""
    rows = list(conn.execute(
        """SELECT id, period_id FROM metric_values
           WHERE company_id=? AND is_current=1 AND status IN (?, ?)""",
        (company_id, *_VALID_STATUS),
    ))
    if not rows:
        raise ValueError("没有已确认的指标值，无法生成数据快照")
    last = conn.execute(
        "SELECT code FROM snapshots WHERE company_id=? ORDER BY id DESC LIMIT 1", (company_id,)
    ).fetchone()
    next_no = 1
    if last:
        try:
            next_no = int(str(last["code"]).lstrip("S")) + 1
        except ValueError:
            next_no = 1
    code = f"S{next_no}"
    value_ids = [int(r["id"]) for r in rows]
    period_ids = sorted({int(r["period_id"]) for r in rows})
    cur = conn.execute(
        """INSERT INTO snapshots
           (company_id, code, reason, note, period_ids, value_ids, value_count, created_at, created_by)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            company_id, code, reason, note, json_dumps(period_ids), json_dumps(value_ids),
            len(value_ids), now_iso(), created_by,
        ),
    )
    return int(cur.lastrowid)


def snapshot_value_ids(snapshot: sqlite3.Row) -> list[int]:
    return [int(v) for v in (json_loads(snapshot["value_ids"], []) or [])]


def snapshot_period_ids(snapshot: sqlite3.Row) -> list[int]:
    return [int(v) for v in (json_loads(snapshot["period_ids"], []) or [])]


def snapshot_coverage(conn: sqlite3.Connection, snapshot: sqlite3.Row) -> list[dict[str, Any]]:
    ids = snapshot_period_ids(snapshot)
    if not ids:
        return []
    rows = conn.execute(
        f"""SELECT period_type, fiscal_year, period_seq FROM periods
            WHERE id IN ({','.join('?' * len(ids))}) ORDER BY fiscal_year, period_seq""",
        ids,
    )
    return [dict(r) for r in rows]
