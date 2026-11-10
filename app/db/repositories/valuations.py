"""估值模型、估值版本与折现率配置的仓储。"""

from __future__ import annotations

import sqlite3
from typing import Any

from app import config
from app.utils import json_dumps, json_loads, now_iso


def get_or_create_model(
    conn: sqlite3.Connection, company_id: int, method: str = "A", scheme: str = "中性"
) -> int:
    row = conn.execute(
        "SELECT id FROM valuation_models WHERE company_id=? AND method=? AND scheme=?",
        (company_id, method, scheme),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO valuation_models (company_id, method, scheme, enabled, created_at) VALUES (?,?,?,1,?)",
        (company_id, method, scheme, now_iso()),
    )
    return int(cur.lastrowid)


def next_version_no(conn: sqlite3.Connection, model_id: int) -> int:
    row = conn.execute(
        "SELECT MAX(version_no) AS v FROM valuation_versions WHERE model_id=?", (model_id,)
    ).fetchone()
    return int(row["v"] or 0) + 1


def create_version(conn: sqlite3.Connection, **fields: Any) -> int:
    fields.setdefault("created_at", now_iso())
    fields.setdefault("status", "draft")
    allowed = [
        "model_id", "company_id", "version_no", "scheme", "method", "based_on_snapshot_id",
        "base_period_id", "base_fcf_method", "base_fcf_value", "fcf_detail", "params",
        "projection", "net_cash", "shares", "market_cap", "market_cap_date", "enterprise_value",
        "equity_value", "per_share_value", "premium_discount", "stability", "applicability",
        "applicability_note", "sensitivity", "input_values", "status", "status_reason", "created_at",
    ]
    data = {k: v for k, v in fields.items() if k in allowed}
    for key in ("fcf_detail", "params", "projection", "net_cash", "sensitivity", "input_values"):
        if key in data and not isinstance(data[key], str):
            data[key] = json_dumps(data[key])
    cols = ", ".join(data)
    marks = ", ".join("?" * len(data))
    cur = conn.execute(f"INSERT INTO valuation_versions ({cols}) VALUES ({marks})", tuple(data.values()))
    return int(cur.lastrowid)


def get_version(conn: sqlite3.Connection, version_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM valuation_versions WHERE id=?", (version_id,)).fetchone()


def list_versions(conn: sqlite3.Connection, company_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM valuation_versions WHERE company_id=? ORDER BY version_no DESC", (company_id,)
    ))


def latest_version(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM valuation_versions WHERE company_id=? ORDER BY version_no DESC LIMIT 1",
        (company_id,),
    ).fetchone()


def latest_usable(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM valuation_versions WHERE company_id=? AND status IN ('confirmed','pending_review') "
        "ORDER BY version_no DESC LIMIT 1",
        (company_id,),
    ).fetchone()


def set_status(conn: sqlite3.Connection, version_id: int, status: str, reason: str | None = None) -> None:
    if status == "confirmed":
        conn.execute(
            "UPDATE valuation_versions SET status=?, status_reason=?, confirmed_at=?, confirmed_by='user' "
            "WHERE id=?",
            (status, reason, now_iso(), version_id),
        )
    else:
        conn.execute(
            "UPDATE valuation_versions SET status=?, status_reason=? WHERE id=?",
            (status, reason, version_id),
        )


def mark_pending_review(conn: sqlite3.Connection, company_id: int, reason: str) -> int:
    cur = conn.execute(
        "UPDATE valuation_versions SET status='pending_review', status_reason=? "
        "WHERE company_id=? AND status='confirmed'",
        (reason, company_id),
    )
    return cur.rowcount


def parse_json(text: str | None, default: Any = None) -> Any:
    return json_loads(text, default)


# ---------------- 折现率配置（PRD 7.5.1） ----------------

def list_configs(conn: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT d.*, c.name AS company_name FROM discount_rate_configs d "
        "LEFT JOIN companies c ON c.id=d.company_id ORDER BY d.config_version DESC LIMIT ?",
        (limit,),
    ))


def next_config_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(config_version) AS v FROM discount_rate_configs").fetchone()
    return int(row["v"] or 0) + 1


def create_config(
    conn: sqlite3.Connection,
    *,
    scope_level: str,
    rate: float,
    industry_tag: str | None = None,
    company_id: int | None = None,
    reason: str = "",
    actor: str = "user",
) -> int:
    if not (config.R_MIN <= rate <= config.R_MAX):
        raise ValueError(f"折现率需在 {config.R_MIN:.0%}–{config.R_MAX:.0%} 区间")
    if scope_level == "industry" and not industry_tag:
        raise ValueError("行业档位必须指定行业标签")
    if scope_level == "company" and not company_id:
        raise ValueError("企业覆盖必须指定企业")
    version = next_config_version(conn)
    cur = conn.execute(
        """INSERT INTO discount_rate_configs
           (scope_level, industry_tag, company_id, rate, effective_from, config_version,
            changed_by, reason, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (scope_level, industry_tag, company_id, rate, now_iso(), version, actor, reason, now_iso()),
    )
    return int(cur.lastrowid)


def resolve_rate(conn: sqlite3.Connection, company: sqlite3.Row) -> dict[str, Any]:
    """按 企业覆盖 > 行业档位 > 全局默认 解析折现率。

    返回 {rate, level, config_version, config_id, industry_tag}。
    行业档位按企业第一个行业标签匹配。
    """
    company_row = conn.execute(
        "SELECT * FROM discount_rate_configs WHERE scope_level='company' AND company_id=? "
        "ORDER BY config_version DESC LIMIT 1",
        (company["id"],),
    ).fetchone()
    if company_row:
        return _rate_result(company_row, "company")

    tags = json_loads(company["industry_tags"], []) or ([company["industry"]] if company["industry"] else [])
    for tag in tags:
        if not tag:
            continue
        industry_row = conn.execute(
            "SELECT * FROM discount_rate_configs WHERE scope_level='industry' AND industry_tag=? "
            "ORDER BY config_version DESC LIMIT 1",
            (tag,),
        ).fetchone()
        if industry_row:
            return _rate_result(industry_row, "industry")

    global_row = conn.execute(
        "SELECT * FROM discount_rate_configs WHERE scope_level='global' "
        "ORDER BY config_version DESC LIMIT 1"
    ).fetchone()
    if global_row:
        return _rate_result(global_row, "global")
    return {
        "rate": config.DCF_DEFAULTS["r_default"],
        "level": "builtin_default",
        "config_version": 0,
        "config_id": None,
        "industry_tag": None,
    }


def _rate_result(row: sqlite3.Row, level: str) -> dict[str, Any]:
    return {
        "rate": float(row["rate"]),
        "level": level,
        "config_version": int(row["config_version"]),
        "config_id": int(row["id"]),
        "industry_tag": row["industry_tag"],
    }


def ensure_default_config(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM discount_rate_configs").fetchone()
    if row and row["n"]:
        return 0
    create_config(
        conn, scope_level="global", rate=config.DCF_DEFAULTS["r_default"],
        reason="系统初始默认值（PRD 7.5.1）",
    )
    return 1
