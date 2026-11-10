"""PDF 抽取（文本层行抽取）。

定位：真实可运行的兜底通道。PDF 表格版式差异大，抽取结果一律标为低置信度，
必须经人工逐项确认后才能进入快照；扫描件（无文本层）不抽取，提示需 OCR。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.domain.metrics_catalog import METRICS, normalize_label
from app.pipeline.extractors.base import ExtractionResult, ExtractedValue
from app.pipeline.period_detector import detect_period
from app.pipeline.readers import read_pdf

_UNIT_SCALE = {"千元": 1e3, "万元": 1e4, "百万元": 1e6, "亿元": 1e8}
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_UNIT_PATTERN = re.compile(r"单位[：:]\s*(人民币)?\s*(千元|万元|百万元|亿元)")


def _parse_numbers(text: str) -> list[float]:
    out: list[float] = []
    for token in _NUMBER.findall(text.replace("，", ",")):
        try:
            out.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return out


def _detect_scale(text: str) -> tuple[float, str]:
    match = _UNIT_PATTERN.search(text)
    if match:
        unit = match.group(2)
        return _UNIT_SCALE.get(unit, 1.0), unit
    return 1.0, "元"


def extract_pdf(path: str | Path, *, declared_period: tuple[str, int] | None = None) -> ExtractionResult:
    path = Path(path)
    result = ExtractionResult(source_kind="pdf")
    pages = read_pdf(path)
    if not any(p.strip() for p in pages):
        result.warnings.append("PDF 无文本层（疑似扫描件），需 OCR 后转为 Excel/CSV 导入")
        return result

    first_page = pages[0] if pages else ""
    period = declared_period or detect_period(path.name) or detect_period(first_page)
    if not period:
        result.warnings.append("未能从文件名或首页识别报告期，需人工指定")
        return result
    period_type, fiscal_year = period
    result.add_period(period_type, fiscal_year)

    scale, unit = _detect_scale(first_page) if first_page else (1.0, "元")
    result.meta["amount_unit"] = unit

    seen: set[tuple[str, str, int]] = set()
    for page_no, text in enumerate(pages, start=1):
        if not text:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if len(line) < 4:
                continue
            head = normalize_label(line[:18])
            matched_code = None
            match_end = 0
            for metric in METRICS:
                if metric.dimension_type != "none":
                    continue
                candidates = (metric.name, *metric.aliases)
                for alias in candidates:
                    norm_alias = normalize_label(alias)
                    if not norm_alias:
                        continue
                    pos = head.find(norm_alias)
                    if pos != -1:
                        matched_code = metric.code
                        # 在原始行中找到该别名的结束位置
                        match_end = line.find(alias) + len(alias)
                        break
                if matched_code:
                    break
            if not matched_code or matched_code in ("segment_revenue", "segment_cost", "segment_gross_margin", "sales_volume"):
                continue
            key = (matched_code, period_type, fiscal_year)
            if key in seen:
                continue
            numbers = _parse_numbers(line[match_end:] if match_end < len(line) else "")
            if not numbers:
                continue
            seen.add(key)
            metric = next(m for m in METRICS if m.code == matched_code)
            value = numbers[0]
            note = "PDF 文本行抽取，需人工核对列（本期/上期）"
            if metric.unit_type == "amount":
                value *= scale
                stored_unit = "元"
            elif metric.unit_type == "ratio":
                if abs(value) > 1.5:
                    value /= 100.0
                stored_unit = "ratio"
            else:
                stored_unit = metric.unit_type
            result.values.append(ExtractedValue(
                metric_code=matched_code,
                period_type=period_type,
                fiscal_year=fiscal_year,
                value=value,
                unit=stored_unit,
                raw_value=numbers[0],
                raw_unit=unit,
                page_no=page_no,
                table_name="PDF 文本行",
                row_label=line[:60],
                column_label="文本首个数字（列位需人工核对）",
                confidence="low",
                caliber_note=note,
            ))
    if not result.values:
        result.warnings.append("PDF 中未匹配到受控指标，建议改用 Excel/CSV 模板导入")
    return result
