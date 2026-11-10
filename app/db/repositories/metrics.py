"""指标字典、指标值、抽取记录与复核留痕的仓储。"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from app.domain.metrics_catalog import METRICS, METRIC_BY_CODE, MetricDef
from app.utils import json_dumps, json_loads, now_iso

_STATUS_CONFIRMED = ("confirmed", "locked")


def seed_metrics(conn: sqlite3.Connection) -> int:
    """把指标字典写入数据库；已存在条目只更新展示元数据，不改 code。"""
    inserted = 0
    for order, m in enumerate(METRICS):
        row = conn.execute("SELECT id FROM metrics WHERE code=?", (m.code,)).fetchone()
        if row:
            conn.execute(
                """UPDATE metrics SET name=?, group_code=?, definition=?, unit_type=?,
                   dimension_type=?, is_valuation_required=?, sort_order=? WHERE id=?""",
                (
                    m.name, m.group_code, m.definition, m.unit_type, m.dimension_type,
                    int(m.is_valuation_required), order, row["id"],
                ),
            )
        else:
            conn.execute(
                """INSERT INTO metrics
                   (code, name, group_code, definition, unit_type, dimension_type,
                    is_valuation_required, is_enabled, version, sort_order)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    m.code, m.name, m.group_code, m.definition, m.unit_type,
                    m.dimension_type, int(m.is_valuation_required), 1, 1, order,
                ),
            )
            inserted += 1
    return inserted


def list_metrics(conn: sqlite3.Connection, group_code: str | None = None) -> list[sqlite3.Row]:
    if group_code:
        return list(conn.execute(
            "SELECT * FROM metrics WHERE group_code=? AND is_enabled=1 ORDER BY sort_order",
            (group_code,),
        ))
    return list(conn.execute("SELECT * FROM metrics WHERE is_enabled=1 ORDER BY sort_order"))


def get_metric(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM metrics WHERE code=?", (code,)).fetchone()


def metric_def(code: str) -> MetricDef | None:
    return METRIC_BY_CODE.get(code)


def current_value(
    conn: sqlite3.Connection,
    company_id: int,
    period_id: int,
    metric_id: int,
    dimension_type: str = "none",
    dimension_name: str = "合计",
) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM metric_values
           WHERE company_id=? AND period_id=? AND metric_id=? AND dimension_type=?
             AND dimension_name=? AND is_current=1""",
        (company_id, period_id, metric_id, dimension_type, dimension_name),
    ).fetchone()


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    scale = max(abs(a), abs(b), 1.0)
    return abs(a - b) <= 1e-6 * scale


def upsert_value(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    period_id: int,
    metric_id: int,
    value: float | None,
    unit: str,
    dimension_type: str = "none",
    dimension_name: str = "合计",
    status: str = "pending",
    confidence: str = "medium",
    raw_value: float | None = None,
    raw_unit: str | None = None,
    raw_currency: str | None = None,
    is_derived: bool = False,
    derived_from: Any = None,
    caliber_note: str | None = None,
    is_manual: bool = False,
) -> tuple[int, str]:
    """写入指标值，返回 (value_id, action)。

    action:
      inserted  新值
      superseded 旧值未确认，被新值替代（旧值 is_current=0，不删除）
      skipped    数值相同，无变化
      conflict   旧值已确认/锁定/人工录入且数值不同 → 不覆盖，交由 U3/改值流程处理
    """
    existing = current_value(conn, company_id, period_id, metric_id, dimension_type, dimension_name)
    ts = now_iso()
    if existing:
        if _same(existing["value"], value):
            return int(existing["id"]), "skipped"
        protected = existing["status"] in _STATUS_CONFIRMED or bool(existing["is_manual"])
        if protected:
            return int(existing["id"]), "conflict"
        conn.execute("UPDATE metric_values SET is_current=0 WHERE id=?", (existing["id"],))
        version_no = int(existing["version_no"]) + 1
        action = "superseded"
    else:
        version_no = 1
        action = "inserted"

    cur = conn.execute(
        """INSERT INTO metric_values
           (company_id, period_id, metric_id, dimension_type, dimension_name, value, unit,
            raw_value, raw_unit, raw_currency, status, version_no, is_current, is_derived,
            derived_from, caliber_note, confidence, is_manual, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            company_id, period_id, metric_id, dimension_type, dimension_name, value, unit,
            raw_value if raw_value is not None else value, raw_unit or unit, raw_currency,
            status, version_no, 1, int(is_derived),
            json_dumps(derived_from) if derived_from else None, caliber_note, confidence,
            int(is_manual), ts,
        ),
    )
    return int(cur.lastrowid), action


def add_extraction(
    conn: sqlite3.Connection,
    *,
    metric_value_id: int,
    source_file_id: int,
    extractor_version: str,
    confidence: str,
    page_no: int | None = None,
    table_name: str | None = None,
    row_label: str | None = None,
    column_label: str | None = None,
    raw_value: str | None = None,
    raw_unit: str | None = None,
    raw_currency: str | None = None,
    statement_scope: str = "CONSOLIDATED",
    audited: bool | None = None,
    dimension: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO extractions
           (metric_value_id, source_file_id, page_no, table_name, row_label, column_label,
            raw_value, raw_unit, raw_currency, statement_scope, audited, extractor_version,
            confidence, dimension, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            metric_value_id, source_file_id, page_no, table_name, row_label, column_label,
            raw_value, raw_unit, raw_currency, statement_scope,
            None if audited is None else int(audited), extractor_version, confidence,
            dimension, now_iso(),
        ),
    )
    return int(cur.lastrowid)


def get_value(conn: sqlite3.Connection, value_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM metric_values WHERE id=?", (value_id,)).fetchone()


def get_value_detail(conn: sqlite3.Connection, value_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT mv.*, m.code AS metric_code, m.name AS metric_name, m.group_code, m.unit_type,
                  p.period_type, p.fiscal_year, c.name AS company_name
           FROM metric_values mv
           JOIN metrics m ON m.id = mv.metric_id
           JOIN periods p ON p.id = mv.period_id
           JOIN companies c ON c.id = mv.company_id
           WHERE mv.id=?""",
        (value_id,),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    ext = conn.execute(
        """SELECT e.*, sf.filename, sf.channel, sf.source_url
           FROM extractions e JOIN source_files sf ON sf.id = e.source_file_id
           WHERE e.metric_value_id=? ORDER BY e.id DESC LIMIT 1""",
        (value_id,),
    ).fetchone()
    data["extraction"] = dict(ext) if ext else None
    return data


def list_values(
    conn: sqlite3.Connection,
    company_id: int,
    *,
    period_ids: Iterable[int] | None = None,
    current_only: bool = True,
    dimension_type: str | None = None,
) -> list[sqlite3.Row]:
    sql = """SELECT mv.*, m.code AS metric_code, m.name AS metric_name,
                    m.group_code, m.unit_type, m.sort_order,
                    p.period_type, p.fiscal_year, p.period_seq
             FROM metric_values mv
             JOIN metrics m ON m.id = mv.metric_id
             JOIN periods p ON p.id = mv.period_id
             WHERE mv.company_id=?"""
    params: list[Any] = [company_id]
    if current_only:
        sql += " AND mv.is_current=1"
    if period_ids:
        ids = list(period_ids)
        sql += f" AND mv.period_id IN ({','.join('?' * len(ids))})"
        params.extend(ids)
    if dimension_type:
        sql += " AND mv.dimension_type=?"
        params.append(dimension_type)
    return list(conn.execute(sql, params))


def value_map_by_period(conn: sqlite3.Connection, company_id: int) -> dict[int, dict[str, float | None]]:
    """{period_id: {metric_code: value}}，仅"合计"维度、当前有效值。"""
    rows = conn.execute(
        """SELECT mv.period_id, m.code, mv.value
           FROM metric_values mv JOIN metrics m ON m.id = mv.metric_id
           WHERE mv.company_id=? AND mv.is_current=1 AND mv.dimension_type='none'""",
        (company_id,),
    )
    out: dict[int, dict[str, float | None]] = {}
    for r in rows:
        out.setdefault(r["period_id"], {})[r["code"]] = r["value"]
    return out


def set_status(conn: sqlite3.Connection, value_id: int, status: str, actor: str = "user") -> None:
    conn.execute(
        "UPDATE metric_values SET status=?, confirmed_by=?, confirmed_at=? WHERE id=?",
        (status, actor, now_iso(), value_id),
    )


def confirm(conn: sqlite3.Connection, value_id: int, actor: str = "user") -> None:
    row = get_value(conn, value_id)
    if not row:
        raise ValueError("指标值不存在")
    if row["status"] not in ("extracted", "pending"):
        raise ValueError(f"当前状态 {row['status']} 不可确认")
    set_status(conn, value_id, "confirmed", actor)


def override(
    conn: sqlite3.Connection,
    value_id: int,
    new_value: float,
    reason: str,
    actor: str = "user",
    dimension_name: str | None = None,
) -> int:
    """人工改值：生成新版本，旧值保留且不再是当前有效值。"""
    row = get_value(conn, value_id)
    if not row:
        raise ValueError("指标值不存在")
    if not reason:
        raise ValueError("改值必须填写原因")
    conn.execute("UPDATE metric_values SET is_current=0 WHERE id=?", (value_id,))
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO metric_values
           (company_id, period_id, metric_id, dimension_type, dimension_name, value, unit,
            raw_value, raw_unit, raw_currency, status, version_no, is_current, is_derived,
            caliber_note, confidence, is_manual, confirmed_by, confirmed_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row["company_id"], row["period_id"], row["metric_id"], row["dimension_type"],
            dimension_name or row["dimension_name"], new_value, row["unit"],
            row["raw_value"], row["raw_unit"], row["raw_currency"], "confirmed",
            int(row["version_no"]) + 1, 1, 0,
            f"人工改值：{reason}", "high", 1, actor, ts, ts,
        ),
    )
    new_id = int(cur.lastrowid)
    conn.execute(
        """INSERT INTO review_records
           (object_type, object_id, action, old_value, new_value, reason, actor, created_at)
           VALUES ('metric_value', ?, 'override', ?, ?, ?, ?, ?)""",
        (new_id, str(row["value"]), str(new_value), reason, actor, ts),
    )
    return new_id


def dispute(conn: sqlite3.Connection, value_id: int, reason: str, actor: str = "user") -> None:
    row = get_value(conn, value_id)
    if not row:
        raise ValueError("指标值不存在")
    set_status(conn, value_id, "disputed", actor)
    conn.execute(
        """INSERT INTO review_records
           (object_type, object_id, action, reason, actor, created_at)
           VALUES ('metric_value', ?, 'dispute', ?, ?, ?)""",
        (value_id, reason, actor, now_iso()),
    )


def pending_values(conn: sqlite3.Connection, company_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        """SELECT mv.*, m.code AS metric_code, m.name AS metric_name, m.group_code,
                  p.period_type, p.fiscal_year, p.period_seq
           FROM metric_values mv
           JOIN metrics m ON m.id = mv.metric_id
           JOIN periods p ON p.id = mv.period_id
           WHERE mv.company_id=? AND mv.is_current=1
             AND mv.status IN ('extracted', 'pending', 'disputed')
           ORDER BY p.fiscal_year DESC, m.sort_order""",
        (company_id,),
    ))


def pending_count(conn: sqlite3.Connection, company_id: int) -> int:
    row = conn.execute(
        """SELECT COUNT(*) AS n FROM metric_values
           WHERE company_id=? AND is_current=1 AND status IN ('extracted','pending')""",
        (company_id,),
    ).fetchone()
    return int(row["n"]) if row else 0


def lock_values(conn: sqlite3.Connection, value_ids: Iterable[int]) -> None:
    ids = [int(v) for v in value_ids]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        conn.execute(
            f"UPDATE metric_values SET status='locked' WHERE id IN ({','.join('?' * len(chunk))}) "
            "AND status='confirmed'",
            chunk,
        )


def values_by_ids(conn: sqlite3.Connection, value_ids: Iterable[int]) -> list[sqlite3.Row]:
    ids = [int(v) for v in value_ids]
    if not ids:
        return []
    out: list[sqlite3.Row] = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        out.extend(conn.execute(
            f"""SELECT mv.*, m.code AS metric_code, m.name AS metric_name, p.period_type,
                       p.fiscal_year, p.period_seq
                FROM metric_values mv
                JOIN metrics m ON m.id = mv.metric_id
                JOIN periods p ON p.id = mv.period_id
                WHERE mv.id IN ({','.join('?' * len(chunk))})""",
            chunk,
        ))
    return out


def completeness(conn: sqlite3.Connection, company_id: int) -> tuple[float, int, int]:
    """估值必需指标完整度（最近一期 + 近 5 年，PRD 12.7）。"""
    periods = list_periods_annual(conn, company_id)[-5:]
    if not periods:
        return 0.0, 0, 0
    required = [m for m in METRICS if m.is_valuation_required]
    metric_ids = {
        r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM metrics WHERE is_valuation_required=1")
    }
    total = len(required) * len(periods)
    ok = 0
    for p in periods:
        for code in metric_ids:
            row = current_value(conn, company_id, p["id"], metric_ids[code])
            if row and row["status"] in _STATUS_CONFIRMED:
                ok += 1
    return (ok / total if total else 0.0), ok, total


def list_periods_annual(conn: sqlite3.Connection, company_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM periods WHERE company_id=? AND period_type='annual' AND status='valid' "
        "ORDER BY fiscal_year",
        (company_id,),
    ))


def decode_json_field(text: str | None) -> Any:
    return json_loads(text)
