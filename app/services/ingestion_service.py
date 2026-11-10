"""材料投递与抽取入库（助手侧流程）。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app import config
from app.db.repositories import companies as company_repo
from app.db.repositories import metrics as metric_repo
from app.pipeline.extractors import extract_file
from app.pipeline.period_detector import period_text
from app.pipeline.readers import detect_content_type, sha256_file
from app.pipeline.validators import Finding, validate_period
from app.utils import json_dumps, json_loads

CONTENT_TYPE_MAP = {
    "annual": "annual_report",
    "h1": "interim_report",
    "q1": "quarterly_report",
    "q3": "quarterly_report",
}


def _store_file(path: Path, sha: str) -> Path:
    config.FILES_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(ch for ch in path.name if ch not in '\\/:*?"<>|')
    target = config.FILES_DIR / f"{sha[:16]}_{safe_name}"
    if not target.exists():
        shutil.copy2(path, target)
    return target


def _period_of(extraction, declared: tuple[str, int] | None) -> tuple[str, int] | None:
    if declared:
        return declared
    if extraction.periods:
        return extraction.periods[0]
    return None


def ingest_files(
    conn,
    company_id: int,
    file_paths: list[str | Path],
    *,
    declared_periods: dict[str, tuple[str, int]] | None = None,
    trigger: str = "upload",
    channel: str = "upload",
    file_meta: dict[str, dict[str, Any]] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """投递一批文件：存文件 → 识别期间 → 抽取 → 校验 → 写待确认指标值。

    返回批次回执（同时写入 ingestion_batches）。
    """
    company = company_repo.get_company(conn, company_id)
    if not company:
        raise ValueError("企业不存在")
    batch_id = company_repo.create_batch(conn, company_id, trigger_type=trigger, note=note)
    metric_repo.seed_metrics(conn)

    report: dict[str, Any] = {
        "batch_id": batch_id,
        "company": company["name"],
        "files": [],
        "periods": [],
        "inserted": 0,
        "skipped": 0,
        "conflicts": [],
        "duplicates": [],
        "warnings": [],
        "findings": [],
    }
    processed = 0
    failed = 0

    for raw_path in file_paths:
        path = Path(raw_path)
        if not path.exists():
            report["warnings"].append(f"文件不存在：{path}")
            failed += 1
            continue
        declared = (declared_periods or {}).get(path.name)
        sha = sha256_file(path)
        existing = company_repo.find_source_file_by_hash(conn, sha)
        if existing:
            report["duplicates"].append({
                "file": path.name,
                "existing_id": existing["id"],
                "message": "内容哈希已存在，未重复入库；如需重跑抽取请先删除旧文件记录",
            })
            continue

        stored = _store_file(path, sha)
        extraction = extract_file(path, declared_period=declared)
        period = _period_of(extraction, declared)
        meta = (file_meta or {}).get(path.name, {})
        file_id = company_repo.create_source_file(
            conn,
            company_id=company_id,
            batch_id=batch_id,
            filename=path.name,
            sha256=sha,
            stored_path=str(stored),
            size_bytes=path.stat().st_size,
            page_count=None,
            channel=meta.get("channel", channel),
            source_url=meta.get("source_url"),
            announcement_no=meta.get("announcement_no"),
            disclosure_date=meta.get("disclosure_date"),
            content_type=CONTENT_TYPE_MAP.get(period[0]) if period else None,
            is_scanned=False,
        )
        file_entry = {
            "file": path.name,
            "file_id": file_id,
            "values": len(extraction.values),
            "warnings": extraction.warnings,
            "meta": extraction.meta,
        }
        report["files"].append(file_entry)
        report["warnings"].extend(f"{path.name}：{w}" for w in extraction.warnings)

        if not period:
            failed += 1
            file_entry["error"] = "未能识别报告期"
            continue

        period_id, _ = company_repo.get_or_create_period(
            conn, company_id, period[0], period[1],
            audited=True if period[0] == "annual" else None,
            authority_rank=2,
        )
        label = period_text(*period)
        if label not in report["periods"]:
            report["periods"].append(label)

        company_currency = company["currency"]
        extracted_map: dict[str, float | None] = {}
        for item in extraction.values:
            if item.period_type != period[0] or item.fiscal_year != period[1]:
                # 同文件中的其他期间列也入库（用于年报上期列 / 增量补齐）
                target_period_id, _ = company_repo.get_or_create_period(
                    conn, company_id, item.period_type, item.fiscal_year,
                    audited=True if item.period_type == "annual" else None,
                )
                target_label = period_text(item.period_type, item.fiscal_year)
                if target_label not in report["periods"]:
                    report["periods"].append(target_label)
            else:
                target_period_id = period_id

            metric = metric_repo.get_metric(conn, item.metric_code)
            if not metric:
                continue
            value_id, action = metric_repo.upsert_value(
                conn,
                company_id=company_id,
                period_id=target_period_id,
                metric_id=metric["id"],
                value=item.value,
                unit=item.unit,
                dimension_type=item.dimension_type,
                dimension_name=item.dimension_name,
                status="pending",
                confidence=item.confidence,
                raw_value=item.raw_value,
                raw_unit=item.raw_unit,
                raw_currency=item.raw_currency or (
                    company_currency if item.unit == "元" else None
                ),
                caliber_note=item.caliber_note,
            )
            if action == "conflict":
                report["conflicts"].append({
                    "metric": item.metric_code,
                    "period": period_text(item.period_type, item.fiscal_year),
                    "file": path.name,
                    "message": "库内已有已确认/人工值且数值不同，未覆盖；请走改值或重述流程",
                })
                continue
            if action == "skipped":
                report["skipped"] += 1
            else:
                report["inserted"] += 1
                metric_repo.add_extraction(
                    conn,
                    metric_value_id=value_id,
                    source_file_id=file_id,
                    extractor_version=config.EXTRACTOR_VERSION,
                    confidence=item.confidence,
                    page_no=item.page_no,
                    table_name=item.table_name,
                    row_label=item.row_label,
                    column_label=item.column_label,
                    raw_value=str(item.raw_value) if item.raw_value is not None else None,
                    raw_unit=item.raw_unit,
                    raw_currency=item.raw_currency,
                    audited=True if period[0] == "annual" else None,
                    dimension=item.dimension_name if item.dimension_type != "none" else None,
                )
            if item.dimension_type == "none" and item.period_type == period[0] and item.fiscal_year == period[1]:
                extracted_map[item.metric_code] = item.value

        if extracted_map:
            findings = validate_period(extracted_map)
            report["findings"].extend({**f.as_dict(), "period": label} for f in findings)
            if any(f.status == "fail" for f in findings):
                report["warnings"].append(f"{label} 勾稽校验未通过，已全部留在待确认清单")
        processed += 1

    if failed == 0 and (processed or report["duplicates"]):
        status = "completed"
    elif processed:
        status = "partial"
    else:
        status = "failed"
    summary = (
        f"处理 {processed} 份文件，新增指标值 {report['inserted']} 项，"
        f"跳过重复 {report['skipped']} 项，冲突 {len(report['conflicts'])} 项，"
        f"涉及报告期：{', '.join(report['periods']) or '无'}"
    )
    company_repo.finish_batch(
        conn, batch_id, status=status, summary=summary,
        failures=report["warnings"] + [
            {"conflict": c} for c in report["conflicts"]
        ],
    )
    report["status"] = status
    report["summary"] = summary

    # K13：新文件中的历史期列与库内已确认值比对（不一致提示重述）
    report["restatement_hints"] = _restatement_hints(conn, company_id)
    return report


def _restatement_hints(conn, company_id: int) -> list[dict[str, Any]]:
    """对同一(指标,期间)的历史版本做差异提示：当前值与更早版本不同即提示。"""
    rows = conn.execute(
        """SELECT mv.id, mv.period_id, mv.value, mv.version_no, m.code, p.fiscal_year, p.period_type
           FROM metric_values mv
           JOIN metrics m ON m.id = mv.metric_id
           JOIN periods p ON p.id = mv.period_id
           WHERE mv.company_id=? AND mv.is_current=1 AND mv.version_no>1
           ORDER BY mv.id DESC LIMIT 50""",
        (company_id,),
    )
    hints = []
    for row in rows:
        old = conn.execute(
            """SELECT value, version_no FROM metric_values
               WHERE company_id=? AND period_id=? AND metric_id=(
                   SELECT id FROM metrics WHERE code=?)
                 AND dimension_type='none' AND is_current=0
               ORDER BY version_no DESC LIMIT 1""",
            (company_id, row["period_id"], row["code"]),
        ).fetchone()
        if old and old["value"] is not None and row["value"] is not None:
            if abs(old["value"] - row["value"]) > 1e-6 * max(abs(old["value"]), abs(row["value"]), 1):
                hints.append({
                    "metric": row["code"],
                    "period": period_text(row["period_type"], row["fiscal_year"]),
                    "old": old["value"],
                    "new": row["value"],
                    "old_version": old["version_no"],
                    "new_version": row["version_no"],
                })
    return hints


def parse_period_text(text: str) -> tuple[str, int]:
    """'2024年报' → ('annual', 2024)。"""
    text = text.strip()
    for suffix, ptype in (("年报", "annual"), ("半年报", "h1"), ("中报", "h1"),
                          ("一季报", "q1"), ("三季报", "q3")):
        if text.endswith(suffix):
            year_text = text[: -len(suffix)].strip()
            try:
                return ptype, int(year_text)
            except ValueError as exc:
                raise ValueError(f"无法解析报告期：{text}") from exc
    try:
        return "annual", int(text)
    except ValueError as exc:
        raise ValueError(f"无法解析报告期：{text}") from exc


def findings_to_json(findings: list[Finding]) -> str:
    return json_dumps([f.as_dict() for f in findings])
