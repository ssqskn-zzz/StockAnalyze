"""估值结果与估值参数的仓储。

估值不再有版本概念：每家企业只保留一份结果（``valuations`` 表的 ``company_id`` 唯一），
页面按最新年度数据自动重算并原地更新。用户可维护的参数只有折现率与未来 10 年逐年
FCF 增长率（``valuation_params``），两者都直接参与 DCF 计算。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app import config
from app.utils import json_dumps, json_loads, now_iso

VALUATION_FIELDS = (
    "based_on_snapshot_id", "base_period_id", "base_fcf_method", "base_fcf_value",
    "fcf_detail", "params", "projection", "net_cash", "shares", "market_cap",
    "market_cap_date", "enterprise_value", "equity_value", "per_share_value",
    "premium_discount", "stability", "applicability", "applicability_note",
    "sensitivity", "input_values",
)
JSON_FIELDS = ("fcf_detail", "params", "projection", "net_cash", "sensitivity", "input_values")


def parse_json(text: str | None, default: Any = None) -> Any:
    return json_loads(text, default)


def get_valuation(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM valuations WHERE company_id=?", (company_id,)
    ).fetchone()


def upsert_valuation(conn: sqlite3.Connection, company_id: int, **fields: Any) -> int:
    data = {k: v for k, v in fields.items() if k in VALUATION_FIELDS}
    for key in JSON_FIELDS:
        if key in data and not isinstance(data[key], str):
            data[key] = json_dumps(data[key])
    now = now_iso()
    existing = get_valuation(conn, company_id)
    if existing:
        data["updated_at"] = now
        assignments = ", ".join(f"{key}=?" for key in data)
        conn.execute(
            f"UPDATE valuations SET {assignments} WHERE company_id=?",
            (*data.values(), company_id),
        )
        return int(existing["id"])
    data.update(company_id=company_id, created_at=now, updated_at=now)
    cols = ", ".join(data)
    marks = ", ".join("?" * len(data))
    cur = conn.execute(f"INSERT INTO valuations ({cols}) VALUES ({marks})", tuple(data.values()))
    return int(cur.lastrowid)


def default_growth_rates() -> list[float]:
    return [float(value) for value in config.DCF_DEFAULTS["growth_rates"]]


def get_growth_rates(conn: sqlite3.Connection, company_id: int) -> list[float] | None:
    row = conn.execute(
        "SELECT growth_rates FROM valuation_params WHERE company_id=?", (company_id,)
    ).fetchone()
    if not row:
        return None
    values = json_loads(row["growth_rates"], None)
    if not isinstance(values, list) or len(values) != config.DCF_DEFAULTS["explicit_years"]:
        return None
    try:
        return [float(v) for v in values]
    except (TypeError, ValueError):
        return None


def get_discount_rate(conn: sqlite3.Connection, company_id: int) -> float | None:
    row = conn.execute(
        "SELECT discount_rate FROM valuation_params WHERE company_id=?", (company_id,)
    ).fetchone()
    if not row or row["discount_rate"] is None:
        return None
    try:
        return float(row["discount_rate"])
    except (TypeError, ValueError):
        return None


def save_params(
    conn: sqlite3.Connection,
    company_id: int,
    *,
    growth_rates: list[float],
    discount_rate: float,
) -> None:
    years = config.DCF_DEFAULTS["explicit_years"]
    if len(growth_rates) != years:
        raise ValueError(f"未来 FCF 增长率需按 {years} 年逐年填写")
    for idx, value in enumerate(growth_rates, start=1):
        if not (config.GROWTH_MIN <= value <= config.GROWTH_MAX):
            raise ValueError(
                f"第 {idx} 年增长率需在 {config.GROWTH_MIN:.0%}–{config.GROWTH_MAX:.0%} 区间"
            )
    if not (config.R_MIN <= discount_rate <= config.R_MAX):
        raise ValueError(f"折现率需在 {config.R_MIN:.0%}–{config.R_MAX:.0%} 区间")
    conn.execute(
        """INSERT INTO valuation_params (company_id, growth_rates, discount_rate, updated_at)
           VALUES (?,?,?,?)
           ON CONFLICT(company_id) DO UPDATE SET
             growth_rates=excluded.growth_rates,
             discount_rate=excluded.discount_rate,
             updated_at=excluded.updated_at""",
        (
            company_id,
            json_dumps([float(v) for v in growth_rates]),
            float(discount_rate),
            now_iso(),
        ),
    )
