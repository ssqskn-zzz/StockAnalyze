"""报告生成、版本演进、对比、定性复核与发布。"""

from __future__ import annotations

import json
from typing import Any

from app.db.repositories import audit
from app.db.repositories import companies as company_repo
from app.db.repositories import metrics as metric_repo
from app.db.repositories import reports as report_repo
from app.db.repositories import snapshots as snapshot_repo
from app.db.repositories import valuations as valuation_repo
from app.domain import derived as dv
from app.domain.periods import period_label
from app.services import metric_service, report_writer
from app.utils import json_dumps, json_loads
from app import config


class ReportPublishError(ValueError):
    pass


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


def _block_texts(blocks: list[dict[str, Any]]) -> list[str]:
    texts = []
    for block in blocks:
        if block["type"] in ("paragraph", "note", "heading"):
            texts.append(block.get("text", ""))
        elif block["type"] == "bullets":
            texts.extend(block.get("items", []))
        elif block["type"] == "valuation_card":
            texts.append(f"valuation:{block.get('version_id')}:{block.get('version_no')}")
    return texts


def _table_signature(blocks: list[dict[str, Any]]) -> str:
    tables = [b for b in blocks if b["type"] == "table"]
    return json.dumps(tables, ensure_ascii=False, sort_keys=True)


def _resolve_citations(conn, snapshot, codes: list[str]) -> list[int]:
    rows = metric_repo.values_by_ids(conn, snapshot_repo.snapshot_value_ids(snapshot))
    code_set = set(codes)
    return sorted({int(r["id"]) for r in rows if r["metric_code"] in code_set})


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
    valuation = valuation_repo.latest_usable(conn, company_id)

    coverage = snapshot_repo.snapshot_coverage(conn, snapshot)
    coverage_labels = [period_label(c["period_type"], c["fiscal_year"]) for c in coverage]

    parts = report_writer.build_content(
        company=company, annual=annual, quarterly=quarterly, segments=segments,
        peers=peers, valuation=valuation, snapshot_code=snapshot["code"],
    )

    report_id = report_repo.ensure_report(conn, company_id, f"{company['name']} 深度分析报告")
    version_no = report_repo.next_version_no(conn, report_id)
    previous = report_repo.latest_published(conn, company_id) or report_repo.latest_version(conn, company_id)
    prev_sections = {s["part_code"]: s for s in report_repo.get_sections(conn, previous["id"])} if previous else {}

    version_id = report_repo.create_version(
        conn,
        report_id=report_id,
        company_id=company_id,
        version_no=version_no,
        snapshot_id=snapshot["id"],
        coverage=coverage,
        status="pending_review",
    )

    summary_lines = [
        f"v{version_no} 基于快照 {snapshot['code']}（覆盖 {len(coverage)} 个报告期，"
        f"{coverage_labels[0] if coverage_labels else '—'} 至 {coverage_labels[-1] if coverage_labels else '—'}）。"
    ]
    changed, carried = [], []
    for part in parts:
        prev = prev_sections.get(part["code"])
        change_type = "new"
        carry_from = None
        if prev:
            prev_blocks = json_loads(prev["body"], []) or []
            same_text = _block_texts(prev_blocks) == _block_texts(part["blocks"])
            same_table = _table_signature(prev_blocks) == _table_signature(part["blocks"])
            if part["code"] in ("part6", "part7"):
                change_type = "data_refresh" if same_text and same_table else "conclusion_rewrite"
            elif same_text and same_table:
                change_type = "carried"
                carry_from = previous["version_no"]
            elif same_text and not same_table:
                change_type = "data_refresh"
            else:
                change_type = "conclusion_rewrite"
            report_repo.add_section_change(
                conn,
                version_id=version_id,
                part_code=part["code"],
                change_type=change_type,
                affected_periods=coverage_labels[-2:],
                old_text=" / ".join(_block_texts(prev_blocks))[:400],
                new_text=" / ".join(_block_texts(part["blocks"]))[:400],
            )
        review_status = "pending" if part["code"] in ("part6", "part7") else "not_required"
        report_repo.add_section(
            conn,
            version_id=version_id,
            part_no=int(part["code"].replace("part", "")),
            part_code=part["code"],
            title=part["title"],
            body=part["blocks"],
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
        + "。定性章节（Part 6/7）需逐条复核后方可发布。"
    )
    report_repo.set_changes(conn, version_id, "\n".join(summary_lines), {
        "snapshot": snapshot["code"],
        "coverage": coverage_labels,
        "changed": changed,
        "carried": carried,
        "previous_version": previous["version_no"] if previous else None,
    })
    audit.add(conn, object_type="report_version", object_id=version_id, action="generate",
              reason=f"基于快照 {snapshot['code']}", actor=actor)
    return {
        "version_id": version_id,
        "version_no": version_no,
        "snapshot": snapshot["code"],
        "coverage": coverage_labels,
        "change_summary": "\n".join(summary_lines),
        "qualitative_pending": 2,
    }


def review_qualitative(conn, version_id: int, section_id: int, decision: str, reason: str = "") -> None:
    section = report_repo.get_section(conn, section_id)
    if not section or section["version_id"] != version_id:
        raise ValueError("章节不属于该报告版本")
    report_repo.set_section_review(conn, section_id, decision, reason)
    audit.add(conn, object_type="report_section", object_id=section_id, action=f"review:{decision}",
              reason=reason)


def publish(conn, version_id: int, *, actor: str = "user", force_reason: str = "") -> None:
    version = report_repo.get_version(conn, version_id)
    if not version:
        raise ValueError("报告版本不存在")
    if version["status"] not in ("pending_review", "draft"):
        raise ReportPublishError("只有待审阅版本可以发布")
    pending = report_repo.pending_qualitative_sections(conn, version_id)
    if pending:
        names = "、".join(s["title"] for s in pending)
        raise ReportPublishError(f"定性章节未完成逐条复核：{names}")
    comp = metric_service.completeness(conn, version["company_id"])
    forced_note = None
    if comp["ratio"] < config.COMPLETENESS_THRESHOLD:
        if not force_reason:
            raise ReportPublishError(
                f"估值必需指标完整度 {comp['ratio']:.0%} 低于 {config.COMPLETENESS_THRESHOLD:.0%}，"
                "如确需发布请填写强制发布原因"
            )
        forced_note = force_reason
    report_repo.set_status(conn, version_id, "published", published_by=actor, forced_note=forced_note)
    report_repo.archive_older_published(conn, version["company_id"], version_id)
    for section in report_repo.get_sections(conn, version_id):
        metric_repo.lock_values(conn, json_loads(section["cited_value_ids"], []) or [])
    audit.add(conn, object_type="report_version", object_id=version_id, action="publish",
              reason=forced_note or "")


def compare_versions(conn, version_a: int, version_b: int) -> list[dict[str, Any]]:
    a = report_repo.get_version(conn, version_a)
    b = report_repo.get_version(conn, version_b)
    if not a or not b:
        raise ValueError("版本不存在")
    sec_a = {s["part_code"]: s for s in report_repo.get_sections(conn, version_a)}
    sec_b = {s["part_code"]: s for s in report_repo.get_sections(conn, version_b)}
    rows = []
    for code in sorted(set(sec_a) | set(sec_b), key=lambda c: int(c.replace("part", ""))):
        sa, sb = sec_a.get(code), sec_b.get(code)
        rows.append({
            "part_code": code,
            "title": (sb or sa)["title"],
            "left": json_loads(sa["body"], []) if sa else [],
            "right": json_loads(sb["body"], []) if sb else [],
            "change_type": sb["change_type"] if sb else "deleted",
            "review_status": sb["review_status"] if sb else "—",
        })
    return rows


def report_view(conn, version_id: int) -> dict[str, Any]:
    version = report_repo.get_version(conn, version_id)
    if not version:
        raise ValueError("报告版本不存在")
    sections = []
    for section in report_repo.get_sections(conn, version_id):
        sections.append({
            "id": section["id"],
            "part_code": section["part_code"],
            "title": section["title"],
            "blocks": json_loads(section["body"], []),
            "change_type": section["change_type"],
            "review_status": section["review_status"],
            "review_reason": section["review_reason"],
            "carry_over_from": section["carry_over_from"],
            "cited_count": len(json_loads(section["cited_value_ids"], []) or []),
        })
    return {
        "version": dict(version),
        "sections": sections,
        "changes": [dict(r) for r in report_repo.list_section_changes(conn, version_id)],
        "pending_review": [dict(r) for r in report_repo.pending_qualitative_sections(conn, version_id)],
        "versions": [dict(r) for r in report_repo.list_versions(conn, version["company_id"])],
    }
