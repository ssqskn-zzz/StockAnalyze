"""通用小工具：时间、JSON、数值格式化。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def fmt_amount(value: float | None, unit: str = "亿元") -> str:
    """金额默认以亿元展示（PRD 12.1）。"""
    if value is None:
        return "—"
    if unit == "亿元":
        return f"{value / 1e8:,.2f}"
    if unit == "万元":
        return f"{value / 1e4:,.2f}"
    return f"{value:,.0f}"


def fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{digits}f}%"


def fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return a / b
