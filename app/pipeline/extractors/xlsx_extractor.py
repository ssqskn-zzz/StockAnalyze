"""Excel / CSV 结构化抽取。

支持的导入模板（真实可用通道）：
1) 工作表「基本信息」：键值对，可含 公司名称/市场/代码/行业/币种/会计准则/是否金融/
   是否周期/金额单位（元|千元|万元|百万元|亿元）。
2) 工作表「主要指标」「成本结构」「资产负债」等：第 1 列为指标名，其余列为期间列，
   列头形如 2022年报 / 2023半年报 / 2024Q1 / 2024。
3) 工作表「分部数据」：列 维度类型|维度名称|指标|期间列...
4) 工作表「销量」：列 产品/指标|期间列...（可省略，只在有实物量时提供）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.domain.metrics_catalog import METRIC_BY_CODE, match_metric
from app.pipeline.extractors.base import ExtractionResult, ExtractedValue
from app.pipeline.period_detector import detect_period, detect_period_from_header
from app.pipeline.readers import Sheet, read_csv, read_xlsx

UNIT_SCALE = {"元": 1.0, "千元": 1e3, "万元": 1e4, "百万元": 1e6, "亿元": 1e8}
UNIT_ALIASES = {"人民币元": "元", "RMB": "元", "CNY": "元", "USD": "元", "HKD": "元", "EUR": "元"}

META_KEYS = {
    "name": ("公司名称", "企业名称", "名称", "company"),
    "code": ("证券代码", "股票代码", "代码", "code"),
    "market": ("市场", "上市地", "market"),
    "industry": ("行业", "所属行业", "industry"),
    "currency": ("币种", "货币", "currency"),
    "accounting_standard": ("会计准则", "准则"),
    "is_financial": ("是否金融", "金融业"),
    "is_cyclical": ("是否周期", "周期股"),
    "amount_unit": ("金额单位", "单位"),
}

SEGMENT_METRIC_MAP = {
    "营业收入": "segment_revenue",
    "收入": "segment_revenue",
    "营业成本": "segment_cost",
    "成本": "segment_cost",
    "毛利率": "segment_gross_margin",
    "销量": "sales_volume",
}

NON_DATA_SHEETS = ("基本信息", "公司信息", "说明", "meta")


def _parse_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "").replace(" ", "")
    if text in ("", "-", "—", "不适用", "N/A", "n/a", "无"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    percent = text.endswith("%")
    text = text.rstrip("%")
    try:
        number = float(text)
    except ValueError:
        return None
    if negative:
        number = -number
    return number


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_meta_sheet(name: str) -> bool:
    return any(key in name for key in NON_DATA_SHEETS)


def _read_meta(sheets: list[Sheet]) -> tuple[dict[str, Any], str]:
    meta: dict[str, Any] = {}
    unit = "元"
    for sheet in sheets:
        if not _is_meta_sheet(sheet.name):
            continue
        for row in sheet.rows:
            if not row:
                continue
            key = _cell_text(row[0]) if len(row) > 0 else ""
            val = row[1] if len(row) > 1 else None
            if not key or val in (None, ""):
                continue
            for field, aliases in META_KEYS.items():
                if any(alias in key for alias in aliases):
                    meta.setdefault(field, val)
    if meta.get("amount_unit"):
        raw_unit = _cell_text(meta["amount_unit"])
        # 「亿元」包含「元」，必须按单位名长度倒序匹配
        for name in sorted(UNIT_SCALE, key=len, reverse=True):
            if name in raw_unit:
                unit = name
                break
    return meta, unit


def _find_header(rows: list[list[Any]], max_scan: int = 15) -> tuple[int, dict[int, tuple[str, int]]] | None:
    best: tuple[int, dict[int, tuple[str, int]]] | None = None
    for idx, row in enumerate(rows[:max_scan]):
        if not row:
            continue
        mapping: dict[int, tuple[str, int]] = {}
        for col, cell in enumerate(row):
            parsed = detect_period_from_header(cell)
            if parsed:
                mapping[col] = parsed
        if len(mapping) >= 2 and (best is None or len(mapping) > len(best[1])):
            best = (idx, mapping)
    return best


def _extract_standard(
    sheet: Sheet, scale: float, unit: str, currency: str, confidence: str
) -> tuple[list[ExtractedValue], list[str]]:
    values: list[ExtractedValue] = []
    warnings: list[str] = []
    header = _find_header(sheet.rows)
    if not header:
        return values, [f"工作表「{sheet.name}」未识别到期间列头"]
    header_idx, col_map = header
    label_col = 0
    for row in sheet.rows[header_idx + 1:]:
        if not row:
            continue
        label = _cell_text(row[label_col]) if len(row) > label_col else ""
        if not label:
            continue
        code = match_metric(label)
        if not code:
            continue
        definition = METRIC_BY_CODE[code]
        if definition.dimension_type != "none":
            continue
        for col, (period_type, fiscal_year) in col_map.items():
            if col >= len(row):
                continue
            raw = _parse_number(row[col])
            if raw is None:
                continue
            value = raw
            note = None
            if definition.unit_type == "amount":
                value = raw * scale
            elif definition.unit_type == "ratio":
                if abs(raw) > 1.5:
                    value = raw / 100.0
                    note = "导入值为百分数，已换算为小数"
            values.append(ExtractedValue(
                metric_code=code,
                period_type=period_type,
                fiscal_year=fiscal_year,
                value=value,
                unit="元" if definition.unit_type == "amount" else ("ratio" if definition.unit_type == "ratio" else definition.unit_type),
                raw_value=raw,
                raw_unit=unit if definition.unit_type == "amount" else None,
                raw_currency=currency if definition.unit_type == "amount" else None,
                table_name=sheet.name,
                row_label=label,
                column_label=str(row[col]) if not isinstance(row[col], (int, float)) else f"{fiscal_year}",
                confidence=confidence,
                caliber_note=note,
            ))
    return values, warnings


def _extract_segments(
    sheet: Sheet, scale: float, currency: str, confidence: str, is_volume: bool = False
) -> tuple[list[ExtractedValue], list[str]]:
    values: list[ExtractedValue] = []
    header = _find_header(sheet.rows)
    if not header:
        return values, [f"分部工作表「{sheet.name}」未识别到期间列头"]
    header_idx, col_map = header
    first_period_col = min(col_map)
    for row in sheet.rows[header_idx + 1:]:
        if not row or len(row) < 3:
            continue
        dim_type_raw = _cell_text(row[0])
        dim_name = _cell_text(row[1])
        metric_label = _cell_text(row[2])
        if not dim_name or not metric_label:
            continue
        code = SEGMENT_METRIC_MAP.get(metric_label)
        if not code:
            code = match_metric(metric_label)
            if code not in ("segment_revenue", "segment_cost", "segment_gross_margin", "sales_volume"):
                continue
        if is_volume or code == "sales_volume":
            dimension_type = "product"
            unit = "实物量"
        elif "区域" in dim_type_raw or "地区" in dim_type_raw:
            dimension_type = "region"
            unit = "元"
        else:
            dimension_type = "product"
            unit = "元"
        for col, (period_type, fiscal_year) in col_map.items():
            if col >= len(row):
                continue
            raw = _parse_number(row[col])
            if raw is None:
                continue
            value = raw * (scale if unit == "元" else 1.0)
            definition = METRIC_BY_CODE[code]
            note = None
            if definition.unit_type == "ratio" and abs(raw) > 1.5:
                value = raw / 100.0
                note = "导入值为百分数，已换算为小数"
            values.append(ExtractedValue(
                metric_code=code,
                period_type=period_type,
                fiscal_year=fiscal_year,
                value=value,
                unit=unit,
                dimension_type=dimension_type,
                dimension_name=dim_name,
                raw_value=raw,
                raw_currency=currency if unit == "元" else None,
                table_name=sheet.name,
                row_label=f"{dim_name}·{metric_label}",
                column_label=f"{fiscal_year}",
                confidence=confidence,
                caliber_note=note,
            ))
    return values, []


def extract_workbook(path: str | Path, *, declared_period: tuple[str, int] | None = None) -> ExtractionResult:
    path = Path(path)
    result = ExtractionResult(source_kind="xlsx" if path.suffix.lower() != ".csv" else "csv")
    sheets = read_xlsx(path) if result.source_kind == "xlsx" else read_csv(path)
    meta, unit = _read_meta(sheets)
    scale = UNIT_SCALE.get(unit, 1.0)
    currency = _cell_text(meta.get("currency") or "CNY") or "CNY"
    result.meta = {k: str(v) for k, v in meta.items()}
    result.meta.setdefault("currency", currency)
    result.meta["amount_unit"] = unit

    derived_period = None
    if declared_period:
        derived_period = declared_period
    else:
        derived_period = detect_period(path.name)
        if not derived_period:
            for sheet in sheets:
                for row in sheet.rows[:20]:
                    for cell in row:
                        found = detect_period(_cell_text(cell))
                        if found:
                            derived_period = found
                            break
                    if derived_period:
                        break
                if derived_period:
                    break

    seen: set[tuple[str, str, int, str, str]] = set()
    for sheet in sheets:
        if _is_meta_sheet(sheet.name):
            continue
        is_volume = "销量" in sheet.name
        is_segment = "分部" in sheet.name or is_volume or "产品" in sheet.name or "区域" in sheet.name
        if is_segment:
            extracted, warnings = _extract_segments(sheet, scale, currency, "high", is_volume=is_volume)
        else:
            extracted, warnings = _extract_standard(sheet, scale, unit, currency, "high")
        result.warnings.extend(warnings)
        for item in extracted:
            key = (item.metric_code, item.period_type, item.fiscal_year, item.dimension_type, item.dimension_name)
            if key in seen:
                continue
            seen.add(key)
            result.values.append(item)
            result.add_period(item.period_type, item.fiscal_year)

    if not result.values and derived_period:
        result.periods.append(derived_period)
    if not result.values:
        result.warnings.append("未从文件中抽取到受控指标，请检查导入模板（见处理记录页说明）")
    elif not result.periods:
        result.warnings.append("未能识别报告期")
    return result
