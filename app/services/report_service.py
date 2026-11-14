"""报告生成与版本演进：生成即发布，历史版本只读保留。"""

from __future__ import annotations

from typing import Any

from app.db.repositories import audit
from app.db.repositories import companies as company_repo
from app.db.repositories import metrics as metric_repo
from app.db.repositories import reports as report_repo
from app.db.repositories import snapshots as snapshot_repo
from app.db.repositories import valuations as valuation_repo
from app.domain import derived as dv
from app.domain.periods import period_label
from app.services import metric_service, report_files, report_writer
from app.utils import json_loads, now_iso


def _snapshot_series(conn, company_id: int, snapshot, period_type: str) -> list[dict[str, Any]]:
    return metric_service.snapshot_series(conn, snapshot, period_type)


def _segments_from_snapshot(conn, snapshot) -> list[dict[str, Any]]:
    rows = metric_repo.values_by_ids(conn, snapshot_repo.snapshot_value_ids(snapshot))
    out = []
    for row in rows:
        if row["dimension_type"] == "none":
            continue
        if row["metric_code"] not in ("segment_revenue", "segment_cost", "segment_gross_margin", "sales_volume"):
            continue
        out.append({
            "dimension_type": row["dimension_type"],
            "name": row["dimension_name"],
            "metric_code": row["metric_code"],
            "year": row["fiscal_year"],
            "period_label": period_label(row["period_type"], row["fiscal_year"]),
            "value": row["value"],
        })
    return out


def _peer_rows(conn, company) -> list[dict[str, Any]]:
    if not company["industry"]:
        return []
    peers = conn.execute(
        "SELECT * FROM companies WHERE industry=? AND id<>? AND status='active' LIMIT 6",
        (company["industry"], company["id"]),
    ).fetchall()
    out = []
    for peer in peers:
        series = metric_service.period_series(conn, peer["id"], "annual")
        if not series:
            continue
        latest = series[-1]
        values, calc = latest["values"], latest["derived"]
        out.append({
            "name": peer["name"],
            "market": peer["market"],
            "period": latest["period"]["label"],
            "revenue": values.get("revenue"),
            "gross_margin": calc.get("gross_margin"),
            "net_margin": calc.get("net_margin_deducted"),
            "roe": calc.get("roe_deducted"),
            "cash_conv": calc.get("ocf_to_net_profit"),
            "debt_ratio": calc.get("debt_ratio"),
            "ar_days": calc.get("ar_turnover_days"),
        })
    return out


def _resolve_citations(conn, snapshot, codes: list[str]) -> list[int]:
    rows = metric_repo.values_by_ids(conn, snapshot_repo.snapshot_value_ids(snapshot))
    code_set = set(codes)
    return sorted({int(r["id"]) for r in rows if r["metric_code"] in code_set})


def section_markdowns(conn, version) -> dict[str, str]:
    """读取某版本按 Part 切分的 Markdown。优先文件，旧版本回退到库内 blocks。"""
    if not version:
        return {}
    path = version["file_path"] if "file_path" in version.keys() else None
    if path:
        sections = report_files.split_sections(report_files.read_markdown(path))
        if sections:
            return sections
    out: dict[str, str] = {}
    for section in report_repo.get_sections(conn, version["id"]):
        blocks = json_loads(section["body"], []) or []
        out[section["part_code"]] = report_files.blocks_to_markdown(blocks)
    return out


def _write_version_markdown(company, *, version_no: int, snapshot_code: str,
                            coverage_labels: list[str], parts: list[dict[str, Any]],
                            generated_at: str, status: str, change_summary: str) -> dict[str, Any]:
    content = report_files.build_markdown(
        company=company, version_no=version_no, snapshot_code=snapshot_code,
        coverage=coverage_labels, parts=parts, generated_at=generated_at,
        status=status, change_summary=change_summary,
    )
    path = report_files.report_file_path(
        company, snapshot_code=snapshot_code, version_no=version_no, coverage=coverage_labels,
    )
    saved, sha = report_files.write_report(path, content)
    return {"file_path": report_files.stored_path(saved), "content_hash": sha}


def generate_report(
    conn,
    company_id: int,
    *,
    snapshot_id: int | None = None,
    actor: str = "assistant",
) -> dict[str, Any]:
    """生成新版报告：以当前发布版为底稿做段落级影响判定，旧版本保留。"""
    company = company_repo.get_company(conn, company_id)
    if not company:
        raise ValueError("企业不存在")
    snapshot = (snapshot_repo.get_snapshot(conn, snapshot_id) if snapshot_id
                else snapshot_repo.latest_snapshot(conn, company_id))
    if not snapshot:
        raise ValueError("尚无数据快照，请先确认指标值并生成快照")

    annual = _snapshot_series(conn, company_id, snapshot, "annual")
    if len(annual) < 3:
        raise ValueError("报告生成需要至少 3 个年度的已确认数据")
    quarterly = []
    for ptype in ("h1", "q1", "q3"):
        quarterly.extend(_snapshot_series(conn, company_id, snapshot, ptype))
    quarterly.sort(key=lambda x: (x["period"]["year"], x["period"]["type"]))
    segments = _segments_from_snapshot(conn, snapshot)
    peers = _peer_rows(conn, company)
    valuation = valuation_repo.get_valuation(conn, company_id)

    coverage = snapshot_repo.snapshot_coverage(conn, snapshot)
    coverage_labels = [period_label(c["period_type"], c["fiscal_year"]) for c in coverage]

    parts = report_writer.build_content(
        company=company, annual=annual, quarterly=quarterly, segments=segments,
        peers=peers, valuation=valuation, snapshot_code=snapshot["code"],
    )

    report_id = report_repo.ensure_report(conn, company_id, f"{company['name']} 深度分析报告")
    version_no = report_repo.next_version_no(conn, report_id)
    previous = report_repo.latest_published(conn, company_id) or report_repo.latest_version(conn, company_id)
    prev_md = section_markdowns(conn, previous) if previous else {}

    version_id = report_repo.create_version(
        conn,
        report_id=report_id,
        company_id=company_id,
        version_no=version_no,
        snapshot_id=snapshot["id"],
        coverage=coverage,
        status="published",
    )

    summary_lines = [
        f"v{version_no} 基于快照 {snapshot['code']}（覆盖 {len(coverage)} 个报告期，"
        f"{coverage_labels[0] if coverage_labels else '—'} 至 {coverage_labels[-1] if coverage_labels else '—'}）。"
    ]
    changed, carried = [], []
    for part in parts:
        prev_text = prev_md.get(part["code"])
        part_md = report_files.blocks_to_markdown(part["blocks"])
        change_type = "new"
        carry_from = None
        if prev_text is not None:
            same_text = prev_text.strip() == part_md.strip()
            same_without_numbers = report_files.signature(prev_text) == report_files.signature(part_md)
            if part["code"] in ("part6", "part7"):
                change_type = "data_refresh" if same_without_numbers else "conclusion_rewrite"
            elif same_text:
                change_type = "carried"
                carry_from = previous["version_no"]
            elif same_without_numbers:
                change_type = "data_refresh"
            else:
                change_type = "conclusion_rewrite"
            report_repo.add_section_change(
                conn,
                version_id=version_id,
                part_code=part["code"],
                change_type=change_type,
                affected_periods=coverage_labels[-2:],
                old_text=prev_text[:400],
                new_text=part_md[:400],
            )
        review_status = "not_required"
        report_repo.add_section(
            conn,
            version_id=version_id,
            part_no=int(part["code"].replace("part", "")),
            part_code=part["code"],
            title=part["title"],
            body=None,  # 正文写入 Markdown 文件，库内只留元数据
            change_type=change_type,
            carry_over_from=carry_from,
            cited_value_ids=_resolve_citations(conn, snapshot, part["cited"]),
            review_status=review_status,
        )
        label = {
            "carried": "沿用", "data_refresh": "数据刷新",
            "conclusion_rewrite": "结论改写", "new": "新增", "deleted": "删除",
        }[change_type]
        if change_type == "carried":
            carried.append(part["title"])
        else:
            changed.append(f"{part['title']}（{label}）")

    summary_lines.append(
        "变更：" + ("、".join(changed) if changed else "无结构变化")
        + "；沿用：" + ("、".join(carried) if carried else "无")
        + "。报告生成即发布，定性章节随版本归档。"
    )
    report_repo.set_changes(conn, version_id, "\n".join(summary_lines), {
        "snapshot": snapshot["code"],
        "coverage": coverage_labels,
        "changed": changed,
        "carried": carried,
        "previous_version": previous["version_no"] if previous else None,
    })
    file_info = _write_version_markdown(
        company, version_no=version_no, snapshot_code=snapshot["code"],
        coverage_labels=coverage_labels, parts=parts, generated_at=now_iso(),
        status="published", change_summary="\n".join(summary_lines),
    )
    report_repo.set_version_file(conn, version_id, **file_info)
    # 报告生成即发布：不再产生 pending_review 版本
    report_repo.set_status(conn, version_id, "published", published_by=actor, forced_note=None)
    report_repo.archive_older_published(conn, company_id, version_id)
    for section in report_repo.get_sections(conn, version_id):
        metric_repo.lock_values(conn, json_loads(section["cited_value_ids"], []) or [])
    audit.add(conn, object_type="report_version", object_id=version_id, action="generate",
              reason=f"基于快照 {snapshot['code']}", actor=actor)
    return {
        "version_id": version_id,
        "version_no": version_no,
        "snapshot": snapshot["code"],
        "coverage": coverage_labels,
        "change_summary": "\n".join(summary_lines),
        "file_path": file_info["file_path"],
        "qualitative_pending": 0,
    }


def coverage_label(coverage_json: str | None) -> str:
    """把版本覆盖期渲染成「2021年报-2026三季报」，用于版本标识。"""
    items = json_loads(coverage_json, []) or []
    labels: list[str] = []
    for item in items:
        if isinstance(item, str):
            labels.append(item)
        elif isinstance(item, dict):
            labels.append(period_label(item.get("period_type"), item.get("fiscal_year")))
    if not labels:
        return "未标注报告期"
    return labels[0] if len(labels) == 1 else f"{labels[0]}-{labels[-1]}"


def report_view(conn, version_id: int) -> dict[str, Any]:
    version = report_repo.get_version(conn, version_id)
    if not version:
        raise ValueError("报告版本不存在")
    md_map = section_markdowns(conn, version)
    sections = []
    for section in report_repo.get_sections(conn, version_id):
        markdown = md_map.get(section["part_code"], "")
        legacy_blocks = json_loads(section["body"], []) or []
        sections.append({
            "id": section["id"],
            "part_code": section["part_code"],
            "title": section["title"],
            "markdown": markdown,
            "html": report_files.render_markdown(markdown),
            # 兼容旧调用方：新版正文在文件里，这里给一个可读的占位 block
            "blocks": legacy_blocks or ([{"type": "markdown", "text": markdown}] if markdown else []),
            "change_type": section["change_type"],
            "review_status": section["review_status"],
            "review_reason": section["review_reason"],
            "carry_over_from": section["carry_over_from"],
            "cited_count": len(json_loads(section["cited_value_ids"], []) or []),
        })
    version_row = dict(version)
    version_row["coverage_label"] = coverage_label(version["coverage"])
    versions = []
    for row in report_repo.list_versions(conn, version["company_id"]):
        item = dict(row)
        item["coverage_label"] = coverage_label(row["coverage"])
        versions.append(item)
    return {
        "version": version_row,
        "sections": sections,
        "changes": [dict(r) for r in report_repo.list_section_changes(conn, version_id)],
        "versions": versions,
    }


def export_legacy_reports(conn, *, actor: str = "assistant") -> list[dict[str, Any]]:
    """把旧版本库内正文一次性导出为 Markdown 文件，并清空库内 body（幂等）。"""
    exported: list[dict[str, Any]] = []
    versions = conn.execute(
        "SELECT * FROM report_versions WHERE file_path IS NULL OR file_path='' ORDER BY id"
    ).fetchall()
    for version in versions:
        sections = report_repo.get_sections(conn, version["id"])
        if not sections or not any(section["body"] for section in sections):
            continue
        company = company_repo.get_company(conn, version["company_id"])
        snapshot = snapshot_repo.get_snapshot(conn, version["based_on_snapshot_id"]) if version["based_on_snapshot_id"] else None
        parts = [
            {
                "code": section["part_code"],
                "title": section["title"],
                "blocks": json_loads(section["body"], []) or [],
            }
            for section in sections
        ]
        coverage_labels = json_loads(version["coverage"], []) or []
        coverage_labels = [
            item if isinstance(item, str) else period_label(item.get("period_type"), item.get("fiscal_year"))
            for item in coverage_labels
        ]
        info = _write_version_markdown(
            company, version_no=version["version_no"],
            snapshot_code=snapshot["code"] if snapshot else "S?",
            coverage_labels=coverage_labels, parts=parts,
            generated_at=version["generated_at"], status=version["status"],
            change_summary=version["change_summary"] or "",
        )
        report_repo.set_version_file(conn, version["id"], **info)
        conn.execute(
            "UPDATE report_sections SET body=NULL WHERE version_id=?", (version["id"],)
        )
        audit.add(conn, object_type="report_version", object_id=version["id"], action="export_markdown",
                  reason=info["file_path"], actor=actor)
        exported.append({"version_id": version["id"], **info})
    return exported
