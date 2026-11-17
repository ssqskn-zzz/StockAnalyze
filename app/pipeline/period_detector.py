"""报告期识别：从文件名/正文/表头识别期间类型与财年，不猜。"""

from __future__ import annotations

import re

from app.domain.periods import PERIOD_TYPES

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}

# 顺序重要：先判断半年报/季报，再判断年报
_PATTERNS: list[tuple[str, str]] = [
    ("h1", r"(20\d{2})\s*年\s*半年度报告"),
    ("h1", r"(20\d{2})\s*年\s*中期报告"),
    ("q1", r"(20\d{2})\s*年\s*(?:第\s*)?[一1]\s*季度报告"),
    ("q3", r"(20\d{2})\s*年\s*(?:第\s*)?[三3]\s*季度报告"),
    ("annual", r"(20\d{2})\s*年\s*年度报告"),
    ("annual", r"(20\d{2})\s*年报"),
    ("annual", r"(20\d{2})\s*annual\s*report"),
    ("annual", r"(20\d{2})\s*form\s*10-k"),
    ("annual", r"10-k\s*[\(（]?\s*(20\d{2})"),
    ("h1", r"(20\d{2})\s*form\s*20-f"),
    ("annual", r"(20\d{2})\s*form\s*20-f"),
    ("q1", r"(20\d{2})\s*form\s*10-q"),
]

_COLUMN_PATTERNS: list[tuple[str, str]] = [
    ("h1", r"(20\d{2})\s*(?:半年报|中报|半年度|H1|6月)"),
    ("q1", r"(20\d{2})\s*(?:一季报|一季度|Q1|3月)"),
    ("q3", r"(20\d{2})\s*(?:三季报|三季度|Q3|9月)"),
    ("annual", r"(20\d{2})\s*(?:年报|年度|A|Y)?$"),
    ("annual", r"^FY\s*(20\d{2})$"),
]


def detect_period(text: str) -> tuple[str, int] | None:
    if not text:
        return None
    normalized = text.replace("\u3000", " ")
    for period_type, pattern in _PATTERNS:
        m = re.search(pattern, normalized, flags=re.IGNORECASE)
        if m:
            year = int(m.group(1))
            if 1990 <= year <= 2100:
                return period_type, year
    return None


def detect_period_from_header(value: object) -> tuple[str, int] | None:
    """解析列头，如 '2024年报'、'2023半年报'、'2022Q1'、'2024'。"""
    if value is None:
        return None
    text = str(value).strip().replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"20\d{2}", text):
        return "annual", int(text)
    for period_type, pattern in _COLUMN_PATTERNS:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            year = int(m.group(1))
            if 1990 <= year <= 2100:
                return period_type, year
    return None


def period_text(period_type: str, fiscal_year: int) -> str:
    return f"{fiscal_year}{PERIOD_TYPES.get(period_type, period_type)}"
