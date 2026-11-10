"""报告期类型、标签与排序。"""

from __future__ import annotations

PERIOD_TYPES = {
    "annual": "年报",
    "h1": "半年报",
    "q1": "一季报",
    "q3": "三季报",
}

# 排序权重：同年内 年报 > 半年报 > 三季报 > 一季报（按期末日期）
_SEQ = {"q1": 1, "h1": 2, "q3": 3, "annual": 4}


def period_label(period_type: str, fiscal_year: int) -> str:
    return f"{fiscal_year}{PERIOD_TYPES[period_type]}"


def period_sort_key(period_type: str, fiscal_year: int, period_seq: int = 0) -> tuple[int, int, int]:
    return (fiscal_year, _SEQ.get(period_type, 9), period_seq)


def period_end_date(period_type: str, fiscal_year: int) -> str:
    return {
        "annual": f"{fiscal_year}-12-31",
        "h1": f"{fiscal_year}-06-30",
        "q1": f"{fiscal_year}-03-31",
        "q3": f"{fiscal_year}-09-30",
    }[period_type]


def period_start_date(period_type: str, fiscal_year: int) -> str:
    return {
        "annual": f"{fiscal_year}-01-01",
        "h1": f"{fiscal_year}-01-01",
        "q1": f"{fiscal_year}-01-01",
        "q3": f"{fiscal_year}-01-01",
    }[period_type]


def is_annual(period_type: str) -> bool:
    return period_type == "annual"
