"""抽取结果的通用结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedValue:
    metric_code: str
    period_type: str
    fiscal_year: int
    value: float
    unit: str
    dimension_type: str = "none"
    dimension_name: str = "合计"
    raw_value: float | None = None
    raw_unit: str | None = None
    raw_currency: str | None = None
    page_no: int | None = None
    table_name: str | None = None
    row_label: str | None = None
    column_label: str | None = None
    confidence: str = "high"
    caliber_note: str | None = None


@dataclass
class ExtractionResult:
    values: list[ExtractedValue] = field(default_factory=list)
    periods: list[tuple[str, int]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    source_kind: str = "unknown"

    def add_period(self, period_type: str, fiscal_year: int) -> None:
        item = (period_type, fiscal_year)
        if item not in self.periods:
            self.periods.append(item)
