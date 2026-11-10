"""P0 核心链路测试：首次建企、增量一期、新增企业、改值联动、金融禁用、缺净现金。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from app.db.repositories import companies as company_repo
from app.db.repositories import metrics as metric_repo
from app.db.repositories import reports as report_repo
from app.db.repositories import snapshots as snapshot_repo
from app.db.repositories import valuations as valuation_repo
from app.services import ingestion_service, metric_service, report_service, valuation_service
from tests.conftest import build_workbook


def _create_company(db, name="测试医疗", code="600000", **kwargs):
    return company_repo.create_company(
        db, name=name, market=kwargs.pop("market", "A"), code=code,
        industry=kwargs.pop("industry", "医疗器械"), **kwargs,
    )


def _ingest_and_confirm(db, company_id, path, reason="first_build"):
    report = ingestion_service.ingest_files(db, company_id, [path])
    assert report["status"] == "completed", report
    confirmed = metric_service.confirm_high_confidence(db, company_id)
    assert confirmed > 0
    assert metric_service.pending_items(db, company_id) == []
    snapshot_id = metric_service.create_snapshot(db, company_id, reason)
    db.commit()
    return report, snapshot_id


def _publish_all(db, version_id):
    view = report_service.report_view(db, version_id)
    for section in view["sections"]:
        if section["part_code"] in ("part6", "part7"):
            report_service.review_qualitative(db, version_id, section["id"], "still_valid", "测试复核")
    report_service.publish(db, version_id)


def test_first_company_closed_loop(db, tmp_path):
    company_id = _create_company(db)
    path = build_workbook(tmp_path / "2021-2024.xlsx", [2021, 2022, 2023, 2024])
    report, snapshot_id = _ingest_and_confirm(db, company_id, path)

    snapshot = snapshot_repo.get_snapshot(db, snapshot_id)
    assert snapshot["code"] == "S1"
    assert snapshot["value_count"] > 40
    assert len(report["periods"]) == 4

    # 勾稽校验全部通过或跳过，不出现 fail
    fails = [f for f in report["findings"] if f["status"] == "fail"]
    assert fails == []

    result = report_service.generate_report(db, company_id)
    assert result["version_no"] == 1
    view = report_service.report_view(db, result["version_id"])
    assert len(view["sections"]) == 7
    assert {s["part_code"] for s in view["sections"]} == {f"part{i}" for i in range(1, 8)}
    assert view["sections"][1]["blocks"], "Part 2 必须有实际内容块"
    qualitative = [s for s in view["sections"] if s["part_code"] in ("part6", "part7")]
    assert all(s["review_status"] == "pending" for s in qualitative)

    # 未复核不可发布
    with pytest.raises(report_service.ReportPublishError):
        report_service.publish(db, result["version_id"])

    _publish_all(db, result["version_id"])
    published = report_repo.get_version(db, result["version_id"])
    assert published["status"] == "published"
    # 引用的指标值已锁定
    locked = db.execute(
        "SELECT COUNT(*) AS n FROM metric_values WHERE company_id=? AND status='locked'", (company_id,)
    ).fetchone()["n"]
    assert locked > 0


def test_incremental_new_period_keeps_old_versions(db, tmp_path):
    company_id = _create_company(db)
    first = build_workbook(tmp_path / "v1.xlsx", [2021, 2022, 2023, 2024])
    _, snap1 = _ingest_and_confirm(db, company_id, first)
    v1 = report_service.generate_report(db, company_id)
    _publish_all(db, v1["version_id"])

    valuation = valuation_service.run_valuation(
        db, company_id, shares=1_000_000_000, market_cap=200e8, market_cap_date="2026-11-01"
    )
    valuation_service.confirm(db, valuation["version_id"])

    # 同一企业新增一期（2025 年报，文件同时含历史列）
    second = build_workbook(tmp_path / "v2.xlsx", [2021, 2022, 2023, 2024, 2025])
    _, snap2 = _ingest_and_confirm(db, company_id, second, reason="new_period")
    assert snapshot_repo.get_snapshot(db, snap2)["code"] == "S2"

    v2 = report_service.generate_report(db, company_id)
    assert v2["version_no"] == 2
    versions = {v["version_no"]: v for v in report_repo.list_versions(db, company_id)}
    assert len(versions) == 2
    assert versions[1]["status"] == "published"
    assert versions[2]["status"] == "pending_review"

    view2 = report_service.report_view(db, v2["version_id"])
    part2 = next(s for s in view2["sections"] if s["part_code"] == "part2")
    assert part2["change_type"] in ("conclusion_rewrite", "data_refresh")
    part6 = next(s for s in view2["sections"] if s["part_code"] == "part6")
    assert part6["review_status"] == "pending"
    assert part6["change_type"] != "carried"

    # 旧版本内容仍可读取
    old_view = report_service.report_view(db, v1["version_id"])
    assert len(old_view["sections"]) == 7
    old_sections = json.dumps(old_view["sections"], ensure_ascii=False, default=str)
    assert "2025年报" not in old_sections

    # 上游快照变化后，已确认估值转待复核（不自动改数）
    refreshed = valuation_repo.get_version(db, valuation["version_id"])
    assert refreshed["status"] == "pending_review"

    _publish_all(db, v2["version_id"])
    assert report_repo.get_version(db, v2["version_id"])["status"] == "published"
    assert report_repo.get_version(db, v1["version_id"])["status"] == "archived"


def test_new_company_isolated(db, tmp_path):
    a = _create_company(db, name="甲公司", code="600001")
    b = _create_company(db, name="乙公司", code="600002", industry="医疗器械")
    _ingest_and_confirm(db, a, build_workbook(tmp_path / "a.xlsx", [2021, 2022, 2023, 2024], name="甲公司"))
    _ingest_and_confirm(db, b, build_workbook(tmp_path / "b.xlsx", [2022, 2023, 2024], name="乙公司"))

    assert len(company_repo.list_periods(db, a)) == 4
    assert len(company_repo.list_periods(db, b)) == 3
    assert snapshot_repo.latest_snapshot(db, a)["code"] == "S1"
    assert snapshot_repo.latest_snapshot(db, b)["code"] == "S1"
    # 重复企业应被拒绝，避免同一公司分裂
    with pytest.raises(company_repo.DuplicateCompanyError):
        _create_company(db, name="甲公司", code="600001")


def test_override_creates_version_and_invalidates_valuation(db, tmp_path):
    company_id = _create_company(db)
    _ingest_and_confirm(db, company_id, build_workbook(tmp_path / "x.xlsx", [2021, 2022, 2023, 2024]))
    valuation = valuation_service.run_valuation(
        db, company_id, shares=1_000_000_000, market_cap=200e8, market_cap_date="2026-11-01"
    )
    valuation_service.confirm(db, valuation["version_id"])

    latest_period = company_repo.list_periods(db, company_id, annual_only=True)[-1]
    ocf_metric = metric_repo.get_metric(db, "ocf")
    old = metric_repo.current_value(db, company_id, latest_period["id"], ocf_metric["id"])
    assert old["status"] == "locked"

    new_id = metric_service.override_value(db, old["id"], old["value"] * 1.05, "口径更正")
    new = metric_repo.get_value(db, new_id)
    assert new["version_no"] == 2
    assert new["is_current"] == 1
    assert metric_repo.get_value(db, old["id"])["is_current"] == 0

    assert valuation_repo.get_version(db, valuation["version_id"])["status"] == "pending_review"


def test_financial_company_disabled(db, tmp_path):
    company_id = _create_company(db, name="测试银行", code="600099", industry="银行", is_financial=True)
    _ingest_and_confirm(db, company_id, build_workbook(
        tmp_path / "bank.xlsx", [2021, 2022, 2023, 2024], name="测试银行", code="600099",
        industry="银行", financial=True,
    ))
    with pytest.raises(valuation_service.ValuationError):
        valuation_service.run_valuation(db, company_id)


def test_missing_net_cash_blocks_equity_value(db, tmp_path):
    company_id = _create_company(db, name="缺项公司", code="600003")
    _ingest_and_confirm(db, company_id, build_workbook(
        tmp_path / "missing.xlsx", [2021, 2022, 2023, 2024], name="缺项公司", code="600003",
        include_net_cash=False,
    ))
    result = valuation_service.run_valuation(db, company_id, shares=1_000_000_000)
    assert result["equity_value"] is None
    assert result["per_share_value"] is None
    assert result["enterprise_value"] is not None
    assert "禁止" in result["applicability_note"]


def test_discount_rate_priority_and_dcf_math(db, tmp_path):
    company_id = _create_company(db)
    _ingest_and_confirm(db, company_id, build_workbook(tmp_path / "x.xlsx", [2021, 2022, 2023, 2024]))

    valuation_repo.create_config(db, scope_level="industry", industry_tag="医疗器械", rate=0.09)
    valuation_repo.create_config(db, scope_level="company", company_id=company_id, rate=0.11)
    company = company_repo.get_company(db, company_id)
    resolved = valuation_repo.resolve_rate(db, company)
    assert resolved["rate"] == 0.11 and resolved["level"] == "company"

    result = valuation_service.run_valuation(
        db, company_id, base_method="1y", market_cap=200e8, shares=1_000_000_000,
        market_cap_date="2026-11-01",
    )
    assert result["params"]["r"] == 0.11
    assert result["params"]["r_source"] == "company"

    # 独立复算 DCF：1 年基准 FCF + 10 年分段增长 + 永续
    annual = metric_service.snapshot_series(db, snapshot_repo.latest_snapshot(db, company_id), "annual")
    base = annual[-1]["values"]["ocf"] - annual[-1]["values"]["capex"]
    assert result["base_fcf"] == pytest.approx(base)
    r, g1, g2, g = 0.11, 0.10, 0.05, 0.03
    fcf = base
    pv = 0.0
    for year in range(1, 11):
        fcf *= 1 + (g1 if year <= 5 else g2)
        pv += fcf / (1 + r) ** year
    terminal = fcf * (1 + g) / (r - g) / (1 + r) ** 10
    expected_ev = pv + terminal
    assert result["enterprise_value"] == pytest.approx(expected_ev, rel=1e-9)

    override = valuation_service.run_valuation(db, company_id, r_override=0.12, shares=1_000_000_000)
    assert override["params"]["r"] == 0.12
    assert override["params"]["r_source"] == "override"


def test_csv_import_channel(db, tmp_path):
    company_id = _create_company(db, name="CSV公司", code="600004")
    path = tmp_path / "2024年报.csv"
    rows = [
        ["指标", "2022年报", "2023年报", "2024年报"],
        ["营业收入", "1000000000", "1100000000", "1200000000"],
        ["扣非净利润", "100000000", "120000000", "140000000"],
        ["经营活动现金流量净额", "110000000", "130000000", "150000000"],
        ["购建固定资产、无形资产和其他长期资产支付的现金", "50000000", "50000000", "60000000"],
        ["货币资金", "200000000", "220000000", "240000000"],
        ["短期借款", "10000000", "10000000", "10000000"],
        ["长期借款", "20000000", "20000000", "20000000"],
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)

    report = ingestion_service.ingest_files(db, company_id, [path])
    assert report["status"] == "completed"
    assert set(report["periods"]) == {"2022年报", "2023年报", "2024年报"}
    metric = metric_repo.get_metric(db, "revenue")
    period = next(p for p in company_repo.list_periods(db, company_id, annual_only=True) if p["fiscal_year"] == 2024)
    value = metric_repo.current_value(db, company_id, period["id"], metric["id"])
    assert value["value"] == pytest.approx(1_200_000_000)


def test_duplicate_file_is_skipped(db, tmp_path):
    company_id = _create_company(db)
    path = build_workbook(tmp_path / "dup.xlsx", [2021, 2022, 2023, 2024])
    ingestion_service.ingest_files(db, company_id, [path])
    second = ingestion_service.ingest_files(db, company_id, [path])
    assert second["duplicates"] and second["inserted"] == 0
    assert second["status"] == "completed"
