"""页面读取模型：首页、企业概览、版本时间轴。"""

from __future__ import annotations

from typing import Any

from app.db.repositories import audit
from app.db.repositories import companies as company_repo
from app.db.repositories import reports as report_repo
from app.db.repositories import snapshots as snapshot_repo
from app.db.repositories import valuations as valuation_repo
from app.domain.metrics_catalog import GROUP_TITLES, METRICS
from app.domain.periods import period_label
from app.services import metric_service


def badge_for(stats: dict[str, Any]) -> list[dict[str, str]]:
    badges = []
    if stats["pending"]:
        badges.append({"text": f"待确认 {stats['pending']}", "kind": "warn"})
    report = stats.get("report")
    if report and report["status"] == "pending_review":
        badges.append({"text": f"报告 v{report['version_no']} 待审阅", "kind": "warn"})
    if stats["completeness"]["ratio"] < 0.8:
        badges.append({"text": "估值数据不完整", "kind": "warn"})
    return badges


def dashboard(conn) -> dict[str, Any]:
    cards = []
    for company in company_repo.list_companies(conn):
        stats = metric_service.company_stats(conn, company["id"])
        cards.append({
            "company": dict(company),
            "stats": stats,
            "badges": badge_for(stats),
            "report": stats.get("report"),
            "valuation": stats.get("valuation"),
            "snapshot": stats.get("snapshot"),
        })
    totals = {
        "companies": len(cards),
        "periods": conn.execute("SELECT COUNT(*) AS n FROM periods").fetchone()["n"],
        "pending": conn.execute(
            "SELECT COUNT(*) AS n FROM metric_values WHERE is_current=1 AND status IN ('pending','extracted')"
        ).fetchone()["n"],
        "reports": conn.execute("SELECT COUNT(*) AS n FROM report_versions").fetchone()["n"],
        "valuations": conn.execute("SELECT COUNT(*) AS n FROM valuations").fetchone()["n"],
    }
    return {"cards": cards, "totals": totals}


def company_overview(conn, company_id: int) -> dict[str, Any]:
    company = company_repo.get_company(conn, company_id)
    if not company:
        raise ValueError("企业不存在")
    stats = metric_service.company_stats(conn, company_id)
    periods = list(reversed(company_repo.list_periods(conn, company_id)))
    annual = list(reversed(metric_service.period_series(conn, company_id, "annual")[-6:]))
    latest_snapshot = stats.get("snapshot")
    return {
        "company": dict(company),
        "stats": stats,
        "badges": badge_for(stats),
        "periods": [dict(p) for p in periods],
        "annual": annual,
        "snapshot": latest_snapshot,
        "report": stats.get("report"),
        "valuation": stats.get("valuation"),
        "recent_reviews": [dict(r) for r in audit.list_recent(conn, company_id, 8)],
    }


def lineage(conn, company_id: int) -> dict[str, Any]:
    from app.db.repositories import companies as repo

    company = repo.get_company(conn, company_id)
    if not company:
        raise ValueError("企业不存在")
    events: list[dict[str, Any]] = []
    for batch in repo.list_batches(conn, company_id):
        events.append({
            "time": batch["started_at"], "kind": "抽取批次",
            "title": f"批次 B{batch['id']}（{batch['status']}）",
            "detail": batch["result_summary"] or batch["note"] or "",
        })
    for f in repo.list_source_files(conn, company_id):
        events.append({
            "time": f["created_at"], "kind": "来源文件",
            "title": f["filename"],
            "detail": f"sha256 {f['sha256'][:12]}…｜{f['channel'] or 'upload'}｜{f['status']}",
        })
    for s in snapshot_repo.list_snapshots(conn, company_id):
        events.append({
            "time": s["created_at"], "kind": "数据快照",
            "title": f"{s['code']}（{s['value_count']} 项指标值）",
            "detail": f"原因：{s['reason']}｜{s['note'] or ''}",
        })
    for v in report_repo.list_versions(conn, company_id):
        events.append({
            "time": v["generated_at"], "kind": "报告版本",
            "title": f"R{v['version_no']}（{v['status']}）",
            "detail": (v["change_summary"] or "").split("\n")[0],
        })
    valuation = valuation_repo.get_valuation(conn, company_id)
    if valuation:
        events.append({
            "time": valuation["updated_at"], "kind": "估值",
            "title": f"每股 {valuation['per_share_value']:.2f} 元" if valuation["per_share_value"] else "DCF 估值",
            "detail": f"基准 {valuation['base_fcf_method']}｜快照 #{valuation['based_on_snapshot_id']}",
        })
    for rec in audit.list_recent(conn, company_id, 30):
        events.append({
            "time": rec["created_at"], "kind": "复核记录",
            "title": f"{rec['object_type']}#{rec['object_id']} {rec['action']}",
            "detail": rec["reason"] or "",
        })
    events.sort(key=lambda e: e["time"], reverse=True)
    return {"company": dict(company), "events": events}


def metric_dictionary(conn) -> list[dict[str, Any]]:
    out = []
    for group_code, title in GROUP_TITLES.items():
        metrics = [{
            "code": m.code, "name": m.name, "unit_type": m.unit_type,
            "dimension_type": m.dimension_type,
            "required": m.is_valuation_required, "definition": m.definition,
        } for m in METRICS if m.group_code == group_code]
        out.append({"code": group_code, "title": title, "metrics": metrics})
    return out


def processing(conn) -> dict[str, Any]:
    batches = []
    for batch in company_repo.list_batches(conn, None, 50):
        company = company_repo.get_company(conn, batch["company_id"])
        batches.append({
            "batch": dict(batch),
            "company": dict(company) if company else None,
        })
    files = conn.execute(
        "SELECT sf.*, c.name AS company_name FROM source_files sf "
        "JOIN companies c ON c.id=sf.company_id ORDER BY sf.created_at DESC LIMIT 50"
    ).fetchall()
    fetch_logs = [dict(r) for r in company_repo.list_fetch_logs(conn, None, 30)]
    return {"batches": batches, "files": [dict(f) for f in files], "fetch_logs": fetch_logs}


def company_period_labels(conn, company_id: int) -> dict[int, str]:
    return {
        p["id"]: period_label(p["period_type"], p["fiscal_year"])
        for p in company_repo.list_periods(conn, company_id)
    }
