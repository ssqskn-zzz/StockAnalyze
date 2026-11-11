"""本轮改造测试：公开数据解析、下载去重、报告 Markdown 文件化与渲染、局域网地址。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import config
from app.db.repositories import companies as company_repo
from app.pipeline import downloader, public_data
from app.services import ingestion_service, metric_service, report_files, report_service
from app.utils import local_ipv4_addresses
from tests.conftest import build_workbook


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
    report_path = Path(version["file_path"])
    assert report_path.exists() and report_path.suffix == ".md"
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
    second_path = Path(
        db.execute("SELECT file_path FROM report_versions WHERE id=?", (second["version_id"],)).fetchone()[0]
    )
    assert second_path != report_path and report_path.exists() and second_path.exists()
    assert "_v2" in second_path.name


def test_markdown_file_name_contains_company_period_snapshot_version():
    company = {"code": "600276", "name": "恒瑞医药"}
    path = report_files.report_file_path(
        company, snapshot_code="S3", version_no=4, coverage=["2021年报", "2026三季报"],
    )
    assert path.name == "2021年报-2026三季报_S3_v4.md"
    assert path.parent.name == "600276_恒瑞医药"


def test_local_addresses_helper_returns_list():
    addresses = local_ipv4_addresses()
    assert isinstance(addresses, list)
    assert all(not address.startswith("127.") for address in addresses)
