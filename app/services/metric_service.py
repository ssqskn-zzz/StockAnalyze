"""指标确认、改值、矩阵组装。"""

from __future__ import annotations

from typing import Any

from app.db.repositories import audit
from app.db.repositories import companies as company_repo
from app.db.repositories import metrics as metric_repo
from app.db.repositories import snapshots as snapshot_repo
from app.domain import derived
from app.domain.metrics_catalog import GROUP_TITLES, METRICS, METRIC_BY_CODE
from app.domain.periods import period_label, period_sort_key
from app.utils import now_iso


def confirm_value(conn, value_id: int, actor: str = "user") -> None:
    metric_repo.confirm(conn, value_id, actor)
    audit.add(conn, object_type="metric_value", object_id=value_id, action="confirm", actor=actor)


def confirm_many(conn, value_ids: list[int], actor: str = "user") -> tuple[int, list[str]]:
    """批量确认待确认指标；跳过不可确认项并返回失败原因。"""
    confirmed, failed = 0, []
    for value_id in value_ids:
        try:
            confirm_value(conn, int(value_id), actor)
            confirmed += 1
        except ValueError as exc:
            failed.append(f"{value_id}: {exc}")
    return confirmed, failed


def confirm_high_confidence(conn, company_id: int, actor: str = "user") -> int:
    rows = conn.execute(
        """SELECT id FROM metric_values
           WHERE company_id=? AND is_current=1 AND status IN ('extracted','pending')
             AND confidence='high' AND is_manual=0""",
        (company_id,),
    ).fetchall()
    for row in rows:
        metric_repo.confirm(conn, int(row["id"]), actor)
        audit.add(conn, object_type="metric_value", object_id=int(row["id"]), action="confirm",
                  reason="高置信度批量确认", actor=actor)
    return len(rows)


def override_value(conn, value_id: int, new_value: float, reason: str, actor: str = "user") -> int:
    new_id = metric_repo.override(conn, value_id, new_value, reason, actor)
    valuation_pending = _mark_company_valuations_pending(conn, metric_repo.get_value(conn, new_id)["company_id"])
    return new_id


def dispute_value(conn, value_id: int, reason: str, actor: str = "user") -> None:
    metric_repo.dispute(conn, value_id, reason, actor)


def _mark_company_valuations_pending(conn, company_id: int) -> int:
    from app.db.repositories import valuations as valuation_repo
    return valuation_repo.mark_pending_review(conn, company_id, "输入指标值被改值或作废")


def create_snapshot(conn, company_id: int, reason: str = "manual", note: str = "") -> int:
    snapshot_id = snapshot_repo.create_snapshot(conn, company_id, reason=reason, note=note)
    # 上游快照更新 ⇒ 已确认估值转待复核（PRD 2.5 估值版本状态机）
    from app.db.repositories import valuations as valuation_repo
    valuation_repo.mark_pending_review(conn, company_id, "上游数据快照更新")
    return snapshot_id


def build_matrix(conn, company_id: int, *, include_quarters: bool = True) -> dict[str, Any]:
    """组装数据矩阵：行 = 指标，列 = 报告期，附带维度行与衍生指标。"""
    periods = company_repo.list_periods(conn, company_id)
    if not include_quarters:
        periods = [p for p in periods if p["period_type"] == "annual"]

    value_rows = metric_repo.list_values(conn, company_id)
    current = {(v["period_id"], v["metric_code"], v["dimension_type"], v["dimension_name"]): v
               for v in value_rows}
    total_map: dict[int, dict[str, float | None]] = {}
    for v in value_rows:
        if v["dimension_type"] == "none":
            total_map.setdefault(v["period_id"], {})[v["metric_code"]] = v["value"]

    # 衍生指标：按年度序列计算
    annual_ids = [p["id"] for p in periods if p["period_type"] == "annual"]
    derived_map: dict[int, dict[str, float | None]] = {}
    for idx, pid in enumerate(annual_ids):
        prev = total_map.get(annual_ids[idx - 1]) if idx > 0 else None
        derived_map[pid] = derived.compute(total_map.get(pid, {}), prev)

    groups: list[dict[str, Any]] = []
    for group_code, title in GROUP_TITLES.items():
        metric_rows: list[dict[str, Any]] = []
        for m in METRICS:
            if m.group_code != group_code or m.dimension_type != "none":
                continue
            cells = {}
            for p in periods:
                row = current.get((p["id"], m.code, "none", "合计"))
                if m.group_code == "G7":
                    value = derived_map.get(p["id"], {}).get(m.code)
                else:
                    value = row["value"] if row else None
                cells[p["id"]] = {
                    "value": value,
                    "status": row["status"] if row else ("derived" if m.group_code == "G7" else None),
                    "value_id": row["id"] if row else None,
                    "manual": bool(row["is_manual"]) if row else False,
                }
            metric_rows.append({
                "code": m.code,
                "name": m.name,
                "unit_type": m.unit_type,
                "required": m.is_valuation_required,
                "derived": m.group_code == "G7",
                "cells": cells,
            })
        groups.append({"code": group_code, "title": title, "metrics": metric_rows})

    segment_rows = []
    for v in value_rows:
        if v["dimension_type"] == "none":
            continue
        segment_rows.append({
            "name": v["dimension_name"],
            "dimension_type": v["dimension_type"],
            "metric": v["metric_name"],
            "code": v["metric_code"],
            "unit_type": v["unit_type"],
            "year": v["fiscal_year"],
            "period_label": period_label(v["period_type"], v["fiscal_year"]),
            "value": v["value"],
            "status": v["status"],
            "value_id": v["id"],
        })

    period_list = [{
        "id": p["id"],
        "label": period_label(p["period_type"], p["fiscal_year"]),
        "type": p["period_type"],
        "year": p["fiscal_year"],
        "key": period_sort_key(p["period_type"], p["fiscal_year"], p["period_seq"]),
    } for p in periods]

    return {
        "periods": period_list,
        "groups": groups,
        "segments": segment_rows,
        "totals": total_map,
        "derived": derived_map,
    }


def period_series(conn, company_id: int, period_type: str = "annual") -> list[dict[str, Any]]:
    """返回按时间升序的 [{period, values, derived}]，供报告与估值使用。"""
    periods = [p for p in company_repo.list_periods(conn, company_id) if p["period_type"] == period_type]
    total_map = metric_repo.value_map_by_period(conn, company_id)
    out = []
    prev_values = None
    for p in periods:
        values = total_map.get(p["id"], {})
        calc = derived.compute(values, prev_values)
        out.append({
            "period": {
                "id": p["id"],
                "type": p["period_type"],
                "year": p["fiscal_year"],
                "label": period_label(p["period_type"], p["fiscal_year"]),
            },
            "values": values,
            "derived": calc,
        })
        prev_values = values
    return out


def snapshot_series(conn, snapshot, period_type: str = "annual") -> list[dict[str, Any]]:
    """按快照冻结的指标值重建时间序列（报告与估值都必须走这里）。"""
    rows = metric_repo.values_by_ids(conn, snapshot_repo.snapshot_value_ids(snapshot))
    by_period: dict[int, dict[str, float | None]] = {}
    period_meta: dict[int, Any] = {}
    for row in rows:
        if row["period_type"] != period_type or row["dimension_type"] != "none":
            continue
        by_period.setdefault(row["period_id"], {})[row["metric_code"]] = row["value"]
        period_meta.setdefault(row["period_id"], row)
    ordered = sorted(by_period, key=lambda pid: (period_meta[pid]["fiscal_year"], period_meta[pid]["period_seq"]))
    out = []
    prev_values = None
    for pid in ordered:
        values = by_period[pid]
        meta = period_meta[pid]
        out.append({
            "period": {
                "id": pid,
                "type": meta["period_type"],
                "year": meta["fiscal_year"],
                "label": period_label(meta["period_type"], meta["fiscal_year"]),
            },
            "values": values,
            "derived": derived.compute(values, prev_values),
        })
        prev_values = values
    return out


def pending_items(conn, company_id: int, only_required: bool = False) -> list[dict[str, Any]]:
    rows = metric_repo.pending_values(conn, company_id)
    items = []
    for row in rows:
        metric = METRIC_BY_CODE.get(row["metric_code"])
        if only_required and not (metric and metric.is_valuation_required):
            continue
        detail = metric_repo.get_value_detail(conn, row["id"]) or {}
        items.append({
            "id": row["id"],
            "metric": row["metric_name"],
            "code": row["metric_code"],
            "period": period_label(row["period_type"], row["fiscal_year"]),
            "value": row["value"],
            "unit_type": row["unit_type"],
            "status": row["status"],
            "confidence": row["confidence"],
            "required": bool(metric.is_valuation_required) if metric else False,
            "source": (detail.get("extraction") or {}).get("filename"),
            "page": (detail.get("extraction") or {}).get("page_no"),
            "row_label": (detail.get("extraction") or {}).get("row_label"),
            "column_label": (detail.get("extraction") or {}).get("column_label"),
        })
    return items


def completeness(conn, company_id: int) -> dict[str, Any]:
    ratio, ok, total = metric_repo.completeness(conn, company_id)
    return {"ratio": ratio, "ok": ok, "total": total}


def company_stats(conn, company_id: int) -> dict[str, Any]:
    from app.db.repositories import reports as report_repo
    from app.db.repositories import valuations as valuation_repo

    periods = company_repo.list_periods(conn, company_id)
    return {
        "period_count": len(periods),
        "annual_count": len([p for p in periods if p["period_type"] == "annual"]),
        "latest_period": period_label(periods[-1]["period_type"], periods[-1]["fiscal_year"]) if periods else "—",
        "pending": metric_repo.pending_count(conn, company_id),
        "completeness": completeness(conn, company_id),
        "report": report_repo.latest_version(conn, company_id),
        "valuation": valuation_repo.latest_version(conn, company_id),
        "snapshot": snapshot_repo.latest_snapshot(conn, company_id),
    }


def log_override_note(conn, value_id: int, reason: str) -> None:
    audit.add(conn, object_type="metric_value", object_id=value_id, action="override_note",
              reason=reason, actor="user", old_value=None, new_value=now_iso())
