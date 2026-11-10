"""A 股定期报告（巨潮 PDF）结构化抽取。

年报：定位"合并资产负债表 / 合并利润表 / 合并现金流量表"，按表头列取本期与上期；
季报/半年报：定位"主要会计数据"表，取报告期（三季报取年初至报告期末累计）。
解析失败时由上层回退到文本行抽取。
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from app.domain.metrics_catalog import match_metric
from app.pipeline.extractors.base import ExtractedValue, ExtractionResult
from app.pipeline.period_detector import detect_period

_ORDINAL = re.compile(r"^[一二三四五六七八九十]+、")

STATEMENTS = {
    "balance": {
        "keyword": "合并资产负债表",
        "anchors": ("货币资金", "资产总计", "负债合计"),
        "codes": {
            "cash", "trading_fin_assets", "receivables", "prepayments", "inventory",
            "current_assets_total", "fixed_assets", "construction_in_progress",
            "intangible_assets", "development_expenditure", "goodwill",
            "long_term_equity_invest", "non_current_assets_total",
            "short_term_borrowings", "payables", "contract_liabilities",
            "payroll_payable", "taxes_payable", "current_portion_lt_debt",
            "current_liabilities_total", "long_term_borrowings", "bonds_payable",
            "non_current_liabilities_total", "total_equity", "share_capital",
            "total_assets", "total_liabilities",
        },
        "unit": "元",
    },
    "income": {
        "keyword": "合并利润表",
        "anchors": ("营业总收入", "净利润"),
        "codes": {
            "revenue", "operating_cost", "selling_expense", "admin_expense",
            "rnd_expense", "finance_expense", "net_profit", "net_profit_parent",
        },
        "unit": "元",
    },
    "cashflow": {
        "keyword": "合并现金流量表",
        "anchors": ("经营活动产生的现金流量净额",),
        "codes": {"ocf", "capex", "investing_cf", "financing_cf", "net_cash_increase", "fx_effect"},
        "unit": "元",
    },
}

KEY_CODES = ("revenue", "net_profit_parent", "net_profit_deducted", "ocf",
             "total_assets", "total_equity")


def _parse_number(text: object) -> float | None:
    if text is None:
        return None
    s = str(text).strip().replace(",", "").replace("，", "").replace(" ", "")
    if s in ("", "—", "-", "--", "/", "不适用"):
        return None
    negative = s.startswith("(") and s.endswith(")") or s.startswith("（") and s.endswith("）")
    s = s.strip("()（）")
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    value = float(s)
    return -value if negative else value


def _clean_label(text: object) -> str:
    s = re.sub(r"\s+", "", str(text or ""))
    s = re.sub(r"[（(][^）)]*填列[^）)]*[）)]", "", s)
    s = _ORDINAL.sub("", s)
    s = re.sub(r"^[（(]?[一二三四五六七八九十\d]+[)）.．、]", "", s)
    return s


def _cell(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _numeric_cells(row: list) -> list[tuple[int, float]]:
    values = []
    for index, cell in enumerate(row[1:], start=1):
        number = _parse_number(cell)
        if number is not None:
            values.append((index, number))
    return values


def _table_has_anchor(table: list[list], anchors: tuple[str, ...]) -> bool:
    labels = {_clean_label(row[0]) for row in table if row}
    return any(anchor in label for label in labels for anchor in anchors)


def _find_statement_start(pdf, texts: list[str], keyword: str, anchors: tuple[str, ...]) -> int | None:
    for index, text in enumerate(texts):
        if keyword not in text:
            continue
        try:
            tables = pdf.pages[index].extract_tables()
        except Exception:  # noqa: BLE001 - 个别页表格解析失败不影响后续页
            continue
        if any(table and _table_has_anchor(table, anchors) for table in tables):
            return index
    return None


def _extract_statements(pdf, texts, result: ExtractionResult, period_type: str, year: int) -> None:
    for name, spec in STATEMENTS.items():
        start = _find_statement_start(pdf, texts, spec["keyword"], spec["anchors"])
        if start is None:
            result.warnings.append(f"未定位到{spec['keyword']}")
            continue
        seen: set[tuple[str, int]] = set()
        for page_index in range(start, min(start + 3, len(pdf.pages))):
            try:
                tables = pdf.pages[page_index].extract_tables()
            except Exception:  # noqa: BLE001
                continue
            for table in tables:
                for row in table:
                    if not row:
                        continue
                    label = _clean_label(row[0])
                    if not label:
                        continue
                    code = match_metric(label)
                    if not code or code not in spec["codes"]:
                        continue
                    numbers = _numeric_cells(row)
                    if not numbers:
                        continue
                    for offset, value in enumerate([n for _, n in numbers[:2]]):
                        fiscal_year = year - offset
                        key = (code, fiscal_year)
                        if key in seen:
                            continue
                        seen.add(key)
                        result.values.append(ExtractedValue(
                            metric_code=code, period_type=period_type, fiscal_year=fiscal_year,
                            value=value, unit=spec["unit"], raw_value=value, raw_unit="元",
                            raw_currency="CNY", page_no=page_index + 1,
                            table_name=spec["keyword"], row_label=label,
                            column_label="本期" if offset == 0 else "上期",
                            confidence="high",
                            caliber_note="合并报表" + ("（本期）" if offset == 0 else "（上期）"),
                        ))
                        result.add_period(period_type, fiscal_year)

        if name == "balance":
            # 三大合计已解析完整时，报表未列示的有息负债科目按 0 处理（并在口径备注中说明）
            for fiscal_year in (year, year - 1):
                codes = {v.metric_code for v in result.values
                         if v.period_type == period_type and v.fiscal_year == fiscal_year}
                if {"total_assets", "total_liabilities", "total_equity"} <= codes:
                    for code in ("short_term_borrowings", "current_portion_lt_debt",
                                 "long_term_borrowings", "bonds_payable"):
                        if code not in codes:
                            result.values.append(ExtractedValue(
                                metric_code=code, period_type=period_type,
                                fiscal_year=fiscal_year, value=0.0, unit="元",
                                raw_value=0.0, raw_unit="元", raw_currency="CNY",
                                table_name=spec["keyword"], row_label="（未列示）",
                                column_label="本期" if fiscal_year == year else "上期",
                                confidence="medium",
                                caliber_note="报表未列示该科目，按 0 处理",
                            ))


def _ytd_column(table: list[list]) -> int | None:
    for row in table:
        for index, cell in enumerate(row):
            text = _cell(cell)
            if "年初至报告期末" in text:
                return index
    return None


def _extract_key_indicators(pdf, texts, result: ExtractionResult, period_type: str, year: int) -> None:
    seen: set[str] = set()
    limit = 40 if period_type == "annual" else 10
    for page_index, text in enumerate(texts[:limit]):
        compact = re.sub(r"\s+", "", text)
        if "主要会计数据" not in compact and "本报告期末" not in compact:
            continue
        try:
            tables = pdf.pages[page_index].extract_tables()
        except Exception:  # noqa: BLE001
            continue
        for table in tables:
            ytd_index = _ytd_column(table) if period_type == "q3" else None
            for row in table:
                if not row:
                    continue
                label = _clean_label(row[0])
                if not label:
                    continue
                code = match_metric(label)
                if not code or code not in KEY_CODES or code in seen:
                    continue
                numbers = _numeric_cells(row)
                if not numbers:
                    continue
                if ytd_index is not None:
                    picked = next((value for index, value in numbers if index == ytd_index), None)
                    if picked is None:
                        picked = numbers[0][1]
                else:
                    picked = numbers[0][1]
                seen.add(code)
                picks = [(year, picked)]
                if period_type == "annual" and code == "net_profit_deducted":
                    # 年报主要会计数据同时给出上年数，用于补齐历史扣非
                    picks = [(year - offset, value) for offset, (_, value) in enumerate(numbers[:2])]
                for fiscal_year, value in picks:
                    result.values.append(ExtractedValue(
                        metric_code=code, period_type=period_type, fiscal_year=fiscal_year,
                        value=value, unit="元", raw_value=value, raw_unit="元",
                        raw_currency="CNY", page_no=page_index + 1,
                        table_name="主要会计数据", row_label=label,
                        column_label="年初至报告期末" if ytd_index is not None else "本报告期",
                        confidence="medium",
                        caliber_note=(
                            "年初至报告期末累计" if ytd_index is not None
                            else ("报告期累计" if period_type in ("h1", "q1", "q3") else "合并报表")
                        ),
                    ))
    if not seen:
        result.warnings.append("未在主要会计数据表中识别到关键指标")


def extract_report(path: str | Path, *, declared_period: tuple[str, int] | None = None) -> ExtractionResult:
    path = Path(path)
    result = ExtractionResult(source_kind="cninfo_pdf")
    period = declared_period or detect_period(path.name)
    if period is None:
        result.warnings.append("无法从文件名识别报告期")
        return result
    period_type, year = period
    result.add_period(period_type, year)

    with pdfplumber.open(str(path)) as pdf:
        texts = [(page.extract_text() or "") for page in pdf.pages]
        if period_type == "annual":
            _extract_statements(pdf, texts, result, period_type, year)
            _extract_key_indicators(pdf, texts, result, period_type, year)
            # 年报主要会计数据只补充扣非（其余以三大报表为准）
            allowed = {"net_profit_deducted"}
            result.values = [
                v for v in result.values
                if v.table_name != "主要会计数据" or v.metric_code in allowed
            ]
        else:
            _extract_key_indicators(pdf, texts, result, period_type, year)
    result.meta = {
        "document": path.name,
        "period": f"{year}{period_type}",
        "currency": "CNY",
        "value_count": len(result.values),
    }
    if not result.values:
        result.warnings.append("结构化解析未产出指标值")
    return result
