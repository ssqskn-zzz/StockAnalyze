"""工作台页面测试：页面可渲染、跳转与关键操作（确认/复核/发布）可用。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.repositories import companies as company_repo
from app.services import ingestion_service, metric_service, report_service, valuation_service
from app.web.app import app
from tests.conftest import build_workbook


@pytest.fixture()
def client(db):
    # db fixture 已完成 init_db/seed；此处不触发 lifespan，避免第二个写连接与事务互锁
    return TestClient(app)


def _seed_company(db, tmp_path, name="测试医疗", code="600000"):
    company_id = company_repo.create_company(db, name=name, market="A", code=code, industry="医疗器械")
    path = build_workbook(tmp_path / f"{code}.xlsx", [2021, 2022, 2023, 2024], name=name, code=code)
    report = ingestion_service.ingest_files(db, company_id, [path])
    assert report["status"] == "completed", report
    metric_service.confirm_high_confidence(db, company_id)
    metric_service.create_snapshot(db, company_id, "first_build")
    db.commit()
    return company_id


def test_empty_pages_render(client):
    for path in ("/", "/processing", "/metrics", "/discount-rates"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "价值投资工作台" in response.text


def test_company_pages_render(client, db, tmp_path):
    company_id = _seed_company(db, tmp_path)

    for path in (
        "/",
        f"/companies/{company_id}",
        f"/companies/{company_id}/data",
        f"/companies/{company_id}/data?scope=annual",
        f"/companies/{company_id}/pending",
        f"/companies/{company_id}/lineage",
        f"/companies/{company_id}/reports",
        f"/companies/{company_id}/valuation",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "测试医疗" in response.text, path

    # 数据矩阵应出现真实指标数字（亿元）
    matrix = client.get(f"/companies/{company_id}/data?scope=annual")
    assert "营业收入" in matrix.text
    assert "100.00" in matrix.text


def test_report_generated_as_published_via_web(client, db, tmp_path):
    company_id = _seed_company(db, tmp_path)
    result = report_service.generate_report(db, company_id)
    db.commit()

    page = client.get(f"/companies/{company_id}/reports")
    assert page.status_code == 200
    assert "Part 1" in page.text or "企业印象" in page.text
    # 版本标识带报告期；不再出现版本对比与待审阅
    assert "2021年报-2024年报" in page.text
    assert "版本对比" not in page.text
    assert "待审阅" not in page.text
    assert "pending_review" not in page.text

    # 版本对比路由已移除
    assert client.get(f"/companies/{company_id}/reports/compare").status_code == 404
    db.commit()
    assert report_service.report_view(db, result["version_id"])["version"]["status"] == "published"


def test_pending_bulk_confirm_via_web(client, db, tmp_path):
    company_id = company_repo.create_company(
        db, name="批量公司", market="A", code="600010", industry="医疗器械"
    )
    path = build_workbook(tmp_path / "bulk.xlsx", [2021, 2022, 2023, 2024],
                          name="批量公司", code="600010")
    assert ingestion_service.ingest_files(db, company_id, [path])["status"] == "completed"
    db.commit()

    items = metric_service.pending_items(db, company_id)
    assert len(items) >= 2
    picked = [item for item in items if item["status"] != "disputed"][:2]
    assert len(picked) == 2

    response = client.post(
        f"/companies/{company_id}/pending/confirm",
        data={"value_ids": [str(item["id"]) for item in picked], "next": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    statuses = {
        row["id"]: row["status"]
        for row in db.execute(
            "SELECT id, status FROM metric_values WHERE id IN (?, ?)",
            (picked[0]["id"], picked[1]["id"]),
        ).fetchall()
    }
    assert all(status == "confirmed" for status in statuses.values())


def test_valuation_page_and_confirm_via_web(client, db, tmp_path):
    company_id = _seed_company(db, tmp_path)
    valuation = valuation_service.run_valuation(
        db, company_id, shares=1_000_000_000, market_cap=200e8, market_cap_date="2026-11-01"
    )
    db.commit()

    page = client.get(f"/companies/{company_id}/valuation")
    assert page.status_code == 200
    assert "DCF" in page.text or "估值" in page.text

    response = client.post(
        f"/valuations/{valuation['version_id']}/confirm",
        data={"next": "/"}, follow_redirects=False,
    )
    assert response.status_code == 303
    db.commit()
    assert valuation_service.valuation_view(db, valuation["version_id"])["version"]["status"] == "confirmed"


def test_value_override_via_web(client, db, tmp_path):
    company_id = _seed_company(db, tmp_path)
    value = db.execute(
        """SELECT mv.id, mv.value FROM metric_values mv
           JOIN metrics m ON m.id=mv.metric_id
           WHERE mv.company_id=? AND m.code='revenue' AND mv.is_current=1
           ORDER BY mv.period_id DESC LIMIT 1""",
        (company_id,),
    ).fetchone()

    response = client.post(
        f"/values/{value['id']}/override",
        data={"value": str(value["value"] * 1.02), "reason": "口径更正", "next": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    new_row = db.execute(
        """SELECT version_no FROM metric_values
           WHERE company_id=? AND metric_id=(SELECT id FROM metrics WHERE code='revenue')
             AND is_current=1 ORDER BY period_id DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    assert new_row["version_no"] == 2


def test_discount_rate_config_via_web(client, db):
    response = client.post(
        "/discount-rates",
        data={"scope": "industry", "rate": "0.11", "industry_tag": "医疗器械", "reason": "测试"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/discount-rates")
    assert page.status_code == 200
    assert "11.00%" in page.text

    # 非法区间应被拒绝
    bad = client.post(
        "/discount-rates",
        data={"scope": "global", "rate": "0.9", "industry_tag": "", "reason": "越界"},
    )
    assert bad.status_code == 400
