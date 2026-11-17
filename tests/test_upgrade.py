"""本轮改造测试：公开数据解析、下载去重、报告 Markdown 文件化与渲染、局域网地址。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app import config
from app.db.repositories import companies as company_repo
from app.db.repositories import snapshots as snapshot_repo
from app.pipeline import downloader, public_data
from app.pipeline.period_detector import detect_period
from app.services import acquire_service, ingestion_service, metric_service, report_files, report_service
from app.utils import json_dumps, local_ipv4_addresses
from tests.conftest import build_workbook


@pytest.mark.parametrize(("title", "expected"), [
    ("2026年第一季度报告", ("q1", 2026)),
    ("2026年一季度报告", ("q1", 2026)),
    ("2026年第三季度报告", ("q3", 2026)),
    ("2026年三季度报告", ("q3", 2026)),
])
def test_detect_period_accepts_current_cninfo_quarterly_titles(title, expected):
    assert detect_period(title) == expected


def test_public_parse_rows_maps_period_and_dedupes():
    rows = [
        {
            "REPORT_DATE": "2026-09-30 00:00:00",
            "REPORT_TYPE": "三季报",
            "TOTALOPERATEREVE": 100.0,
            "PARENTNETPROFIT": 20.0,
            "KCFJCXSYJLR": 19.0,
            "XSMLL": 80.0,
        },
        {  # 同一期间重复行应被跳过
            "REPORT_DATE": "2026-09-30 00:00:00",
            "REPORT_TYPE": "三季报",
            "TOTALOPERATEREVE": 999.0,
        },
        {
            "REPORT_DATE": "2025-12-31 00:00:00",
            "REPORT_TYPE": "年报",
            "TOTALOPERATEREVE": 300.0,
        },
    ]
    items = public_data.parse_rows(rows, public_data.KPI_FIELDS, report_type_field="REPORT_TYPE")
    revenue = [i for i in items if i["metric_code"] == "revenue"]
    assert {(i["period_type"], i["fiscal_year"]) for i in revenue} == {("q3", 2026), ("annual", 2025)}
    assert next(i for i in revenue if i["period_type"] == "q3")["value"] == 100.0
    margin = next(i for i in items if i["metric_code"] == "gross_margin")
    assert margin["value"] == pytest.approx(0.80)

    cash = public_data.parse_rows(
        [{"REPORT_DATE": "2025-12-31", "NETCASH_OPERATE": 55.0, "CONSTRUCT_LONG_ASSET": 11.0}],
        public_data.CASH_FIELDS,
    )
    assert {i["metric_code"] for i in cash} == {"ocf", "capex"}
    assert next(i for i in cash if i["metric_code"] == "ocf")["period_type"] == "annual"

    balance = public_data.parse_rows(
        [{
            "REPORT_DATE": "2026-09-30",
            "MONETARYFUNDS": 100.0,
            "SHORT_LOAN": 10.0,
            "NONCURRENT_LIAB_1YEAR": 2.0,
            "LONG_LOAN": None,
            "BOND_PAYABLE": None,
            "SHARE_CAPITAL": 66.0,
        }],
        public_data.BALANCE_FIELDS,
    )
    assert {i["metric_code"] for i in balance} == {
        "cash", "short_term_borrowings", "current_portion_lt_debt", "share_capital",
    }
    assert next(i for i in balance if i["metric_code"] == "cash")["period_type"] == "q3"


def test_snapshot_coverage_sorted_by_end_date(db):
    """报告期覆盖范围需按实际截止日期排序，不能依赖插入顺序。"""
    company_id = company_repo.create_company(
        db, name="覆盖排序测试", market="A", code="000001", industry="测试"
    )
    q1_id, _ = company_repo.get_or_create_period(db, company_id, "q1", 2026)
    annual_id, _ = company_repo.get_or_create_period(db, company_id, "annual", 2025)
    q3_id, _ = company_repo.get_or_create_period(db, company_id, "q3", 2026)
    h1_id, _ = company_repo.get_or_create_period(db, company_id, "h1", 2026)
    snapshot = {"period_ids": json_dumps([q1_id, annual_id, q3_id, h1_id])}

    coverage = snapshot_repo.snapshot_coverage(db, snapshot)

    assert [(c["period_type"], c["fiscal_year"]) for c in coverage] == [
        ("annual", 2025), ("q1", 2026), ("h1", 2026), ("q3", 2026),
    ]


def test_public_parse_trading_fin_assets_under_new_standard():
    """新准则下交易性金融资产可能只出现在 NOTFVTPL 字段，需映射到同一指标。"""
    balance = public_data.parse_rows(
        [{
            "REPORT_DATE": "2025-12-31",
            "MONETARYFUNDS": 100.0,
            "TRADE_FINASSET": None,
            "TRADE_FINASSET_NOTFVTPL": 58.99,
        }],
        public_data.BALANCE_FIELDS,
    )
    trading = [i for i in balance if i["metric_code"] == "trading_fin_assets"]
    assert len(trading) == 1
    assert trading[0]["value"] == pytest.approx(58.99)
    assert trading[0]["period_type"] == "annual"


def test_public_parse_full_statements_and_derived_margin():
    income = public_data.parse_rows(
        [{
            "REPORT_DATE": "2026-09-30",
            "TOTAL_OPERATE_INCOME": 1000.0,
            "OPERATE_COST": 300.0,
            "SALE_EXPENSE": 120.0,
            "MANAGE_EXPENSE": 80.0,
            "RESEARCH_EXPENSE": 60.0,
            "FINANCE_EXPENSE": -5.0,
            "NETPROFIT": 210.0,
            "PARENT_NETPROFIT": 205.0,
            "DEDUCT_PARENT_NETPROFIT": 190.0,
        }],
        public_data.INCOME_FIELDS,
    )
    by_code = {i["metric_code"]: i["value"] for i in income}
    assert by_code["revenue"] == 1000.0
    assert by_code["operating_cost"] == 300.0
    assert by_code["rnd_expense"] == 60.0
    assert by_code["net_profit"] == 210.0

    cash = public_data.parse_rows(
        [{"REPORT_DATE": "2026-09-30", "NETCASH_OPERATE": 88.0, "RATE_CHANGE_EFFECT": -3.0}],
        public_data.FULL_CASH_FIELDS,
    )
    assert {i["metric_code"] for i in cash} == {"ocf", "fx_effect"}

    balance = public_data.parse_rows(
        [{
            "REPORT_DATE": "2026-09-30",
            "NOTE_ACCOUNTS_RECE": 11.0,
            "INVENTORY": 22.0,
            "TOTAL_ASSETS": 500.0,
            "NOTE_ACCOUNTS_PAYABLE": 33.0,
            "TOTAL_LIABILITIES": 120.0,
            "TOTAL_EQUITY": 380.0,
        }],
        public_data.BALANCE_FIELDS,
    )
    assert {i["metric_code"] for i in balance} == {
        "receivables", "inventory", "total_assets", "payables", "total_liabilities", "total_equity",
    }

    # 毛利率缺失时由营收与营业成本推算，已有的不覆盖
    items = public_data._fill_gross_margin(
        [dict(i) for i in income] + [{"metric_code": "gross_margin", "period_type": "annual",
                                     "fiscal_year": 2025, "value": 0.5, "unit": "ratio",
                                     "raw_value": 50.0, "raw_unit": "%"}]
    )
    margins = {i["fiscal_year"]: i for i in items if i["metric_code"] == "gross_margin"}
    assert margins[2026]["value"] == pytest.approx(0.70)
    assert margins[2026]["is_derived"] is True
    assert margins[2025]["value"] == 0.5


def test_download_store_dedupes_by_content(db, tmp_path):
    company_repo.create_company(db, name="去重公司", market="A", code="600010")
    first_path, first_sha, first_dup = downloader._store_content(db, b"same-pdf-bytes", "2024年报.pdf")
    second_path, second_sha, second_dup = downloader._store_content(db, b"same-pdf-bytes", "另一个名字.pdf")
    assert first_dup is False and second_dup is True
    assert first_sha == second_sha and first_path == second_path
    assert first_path.exists()
    assert len(list(config.FILES_DIR.glob(f"{first_sha[:16]}_*"))) == 1


def test_report_written_to_markdown_and_rendered(db, tmp_path):
    company_id = company_repo.create_company(db, name="报告公司", market="A", code="600020")
    path = build_workbook(tmp_path / "2021-2024.xlsx", [2021, 2022, 2023, 2024], name="报告公司", code="600020")
    assert ingestion_service.ingest_files(db, company_id, [path])["status"] == "completed"
    metric_service.confirm_high_confidence(db, company_id)
    metric_service.create_snapshot(db, company_id, "test")

    result = report_service.generate_report(db, company_id)
    version = db.execute("SELECT * FROM report_versions WHERE id=?", (result["version_id"],)).fetchone()
    # 库内存相对路径，读取时按项目根/数据目录解析，可随目录搬迁
    assert not Path(version["file_path"]).is_absolute()
    report_path = report_files.resolve_path(version["file_path"])
    assert report_path is not None and report_path.exists() and report_path.suffix == ".md"
    assert "_S1_v1" in report_path.name
    content = report_path.read_text(encoding="utf-8")
    assert "# 报告公司 深度分析报告" in content
    assert "## Part 1" in content and "|" in content

    bodies = db.execute(
        "SELECT body FROM report_sections WHERE version_id=?", (result["version_id"],)
    ).fetchall()
    assert all(row["body"] is None for row in bodies), "新版正文不得再存数据库"

    view = report_service.report_view(db, result["version_id"])
    part2 = next(s for s in view["sections"] if s["part_code"] == "part2")
    assert "<table>" in part2["html"]
    assert "| ---" not in part2["html"]
    assert part2["markdown"] and part2["blocks"]

    # 再生成一版：新文件，旧文件保留，不覆盖
    second = report_service.generate_report(db, company_id)
    second_path = report_files.resolve_path(
        db.execute("SELECT file_path FROM report_versions WHERE id=?", (second["version_id"],)).fetchone()[0]
    )
    assert second_path is not None
    assert second_path != report_path and report_path.exists() and second_path.exists()
    assert "_v2" in second_path.name

    # 旧机器遗留的绝对路径：按 reports/ 之后的目录回落，仍能读到正文
    legacy_path = f"D:/old_pc/StockAnalyze/data/reports/{report_path.parent.name}/{report_path.name}"
    db.execute("UPDATE report_versions SET file_path=? WHERE id=?", (legacy_path, result["version_id"]))
    db.commit()
    assert report_files.resolve_path(legacy_path) == report_path
    moved_view = report_service.report_view(db, result["version_id"])
    assert any(section["html"] for section in moved_view["sections"])


def test_markdown_file_name_contains_company_period_snapshot_version():
    company = {"code": "600276", "name": "恒瑞医药"}
    path = report_files.report_file_path(
        company, snapshot_code="S3", version_no=4, coverage=["2021年报", "2026三季报"],
    )
    assert path.name == "2021年报-2026三季报_S3_v4.md"
    assert path.parent.name == "600276_恒瑞医药"


def test_acquire_runs_public_and_filing_in_parallel(db, monkeypatch):
    company_id = company_repo.create_company(db, name="并行公司", market="A", code="600030")
    db.commit()

    def slow_public(conn, company, *, years=5):
        time.sleep(0.8)
        return {"channel": "test", "source": "test", "items": 0, "periods": [],
                "inserted": 0, "updated": 0, "skipped": 0, "conflicts": 0}

    def slow_filing(conn, company, **kwargs):
        time.sleep(0.8)
        return []

    monkeypatch.setattr(acquire_service.public_data, "fetch_public_metrics", slow_public)
    monkeypatch.setattr(acquire_service.downloader, "fetch_for_company", slow_filing)

    started = time.perf_counter()
    result = acquire_service.acquire_company(company_id, ingest=False, period_types=["annual"])
    elapsed = time.perf_counter() - started

    assert result["public"]["ok"] and result["filing"]["ok"]
    assert elapsed < 1.4, f"两路应并行执行，实际耗时 {elapsed:.2f}s"


def test_local_addresses_helper_returns_list():
    addresses = local_ipv4_addresses()
    assert isinstance(addresses, list)
    assert all(not address.startswith("127.") for address in addresses)
