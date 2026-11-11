"""FastAPI 工作台（结果承载与规则界面，不承担解析/生成/测算）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config
from app.db import connection
from app.db.repositories import companies as company_repo
from app.db.repositories import metrics as metric_repo
from app.db.repositories import reports as report_repo
from app.db.repositories import valuations as valuation_repo
from app.services import (
    metric_service,
    query_service,
    report_service,
    valuation_service,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _yi(value):
    if value is None:
        return "—"
    return f"{value / 1e8:,.2f}"


def _pct(value):
    if value is None:
        return "—"
    return f"{value * 100:.2f}%"


def _num(value, digits=2):
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def _cell(value, unit_type):
    if value is None:
        return "—"
    if unit_type == "amount":
        return f"{value / 1e8:,.2f}"
    if unit_type == "ratio":
        return f"{value * 100:.1f}%"
    if unit_type == "days":
        return f"{value:.0f}"
    return f"{value:,.2f}"


templates.env.filters["yi"] = _yi
templates.env.filters["pct"] = _pct
templates.env.filters["num"] = _num
templates.env.filters["cell"] = _cell
templates.env.globals["status_labels"] = {
    "pending": "待确认", "extracted": "机器提取", "confirmed": "已确认",
    "locked": "已锁定", "disputed": "有争议", "void": "已作废",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    connection.init_db()
    with connection.session() as conn:
        metric_repo.seed_metrics(conn)
        valuation_repo.ensure_default_config(conn)
    yield


app = FastAPI(title="价值投资工作台", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    return templates.TemplateResponse(request=request, name=name, context=context, status_code=status_code)


@app.get("/")
def index(request: Request):
    with connection.session() as conn:
        data = query_service.dashboard(conn)
    return _render(request, "index.html", {"page": "index", **data})


@app.get("/companies/{company_id}")
def company_detail(request: Request, company_id: int):
    with connection.session() as conn:
        try:
            data = query_service.company_overview(conn, company_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _render(request, "company.html", {"page": "company", **data})


@app.get("/companies/{company_id}/data")
def company_data(request: Request, company_id: int, scope: str = "all"):
    with connection.session() as conn:
        company = query_service.company_overview(conn, company_id)["company"]
        matrix = metric_service.build_matrix(conn, company_id, include_quarters=(scope != "annual"))
        completeness = metric_service.completeness(conn, company_id)
    return _render(request, "data.html", {
        "page": "data", "company": company, "matrix": matrix,
        "completeness": completeness, "scope": scope,
    })


@app.get("/companies/{company_id}/pending")
def company_pending(request: Request, company_id: int, only_required: bool = False):
    with connection.session() as conn:
        company = query_service.company_overview(conn, company_id)["company"]
        items = metric_service.pending_items(conn, company_id, only_required=only_required)
    return _render(request, "pending.html", {
        "page": "data", "company": company, "items": items, "only_required": only_required,
    })


@app.post("/companies/{company_id}/pending/confirm")
def confirm_pending(
    request: Request, company_id: int,
    value_ids: list[int] = Form(default=[]), next: str = Form("/"),
):
    with connection.session() as conn:
        metric_service.confirm_many(conn, value_ids)
    return RedirectResponse(next, status_code=303)


@app.post("/values/{value_id}/confirm")
def confirm_value(request: Request, value_id: int, next: str = Form("/")):
    with connection.session() as conn:
        try:
            metric_service.confirm_value(conn, value_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(next, status_code=303)


@app.post("/values/{value_id}/override")
def override_value(
    request: Request, value_id: int,
    value: str = Form(...), reason: str = Form(...), next: str = Form("/"),
):
    try:
        number = float(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="改值必须是数字") from exc
    with connection.session() as conn:
        metric_service.override_value(conn, value_id, number, reason)
    return RedirectResponse(next, status_code=303)


@app.post("/values/{value_id}/dispute")
def dispute_value(request: Request, value_id: int, reason: str = Form(""), next: str = Form("/")):
    with connection.session() as conn:
        metric_service.dispute_value(conn, value_id, reason or "用户标记争议")
    return RedirectResponse(next, status_code=303)


@app.get("/companies/{company_id}/reports")
def report_latest(request: Request, company_id: int, version: int | None = None):
    with connection.session() as conn:
        company = query_service.company_overview(conn, company_id)["company"]
        versions = [v for v in report_repo.list_versions(conn, company_id) if v["status"] not in ("pending_review", "draft")]
        row = None
        if version:
            candidate = report_repo.get_version_by_no(conn, company_id, version)
            if candidate and candidate["status"] not in ("pending_review", "draft"):
                row = candidate
        elif versions:
            row = versions[0]
        if not row:
            return _render(request, "report_empty.html", {"page": "reports", "company": company})
        data = report_service.report_view(conn, row["id"])
        completeness = metric_service.completeness(conn, company_id)
    return _render(request, "report.html", {
        "page": "reports", "company": company, "completeness": completeness, **data,
    })


@app.get("/companies/{company_id}/valuation")
def valuation_page(request: Request, company_id: int, version_id: int | None = None):
    with connection.session() as conn:
        overview = query_service.company_overview(conn, company_id)
        company = overview["company"]
        versions = valuation_repo.list_versions(conn, company_id)
        row = None
        if version_id:
            row = valuation_repo.get_version(conn, version_id)
        elif versions:
            row = versions[0]
        if not row:
            return _render(request, "valuation_empty.html", {
                "page": "valuation", "company": company,
                "financial": valuation_service.is_financial(company),
                "completeness": overview["stats"]["completeness"],
            })
        data = valuation_service.valuation_view(conn, row["id"])
    return _render(request, "valuation.html", {
        "page": "valuation", "company": company,
        "versions": [dict(v) for v in versions],
        "financial": valuation_service.is_financial(company),
        **data,
    })


@app.post("/valuations/{version_id}/confirm")
def confirm_valuation(request: Request, version_id: int, next: str = Form("/")):
    with connection.session() as conn:
        try:
            valuation_service.confirm(conn, version_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(next, status_code=303)


@app.get("/companies/{company_id}/lineage")
def lineage_page(request: Request, company_id: int):
    with connection.session() as conn:
        data = query_service.lineage(conn, company_id)
    return _render(request, "lineage.html", {"page": "lineage", **data})


@app.get("/processing")
def processing_page(request: Request):
    with connection.session() as conn:
        data = query_service.processing(conn)
        companies = [dict(c) for c in company_repo.list_companies(conn)]
    return _render(request, "processing.html", {"page": "processing", "companies": companies, **data})


@app.get("/metrics")
def metrics_page(request: Request):
    with connection.session() as conn:
        groups = query_service.metric_dictionary(conn)
    return _render(request, "metrics.html", {"page": "metrics", "groups": groups})


@app.get("/discount-rates")
def discount_rates_page(request: Request):
    with connection.session() as conn:
        data = query_service.discount_rate_page(conn)
    return _render(request, "discount_rates.html", {"page": "rates", **data})


@app.post("/discount-rates")
def discount_rate_add(
    request: Request, scope: str = Form(...), rate: str = Form(...),
    industry_tag: str = Form(""), company_id: str = Form(""), reason: str = Form(""),
):
    try:
        numeric = float(rate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="折现率必须是数字（如 0.10）") from exc
    with connection.session() as conn:
        try:
            valuation_repo.create_config(
                conn,
                scope_level=scope,
                rate=numeric,
                industry_tag=industry_tag or None,
                company_id=int(company_id) if company_id else None,
                reason=reason or "页面配置",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if company_id:
            valuation_service.mark_pending_after_upstream_change(
                conn, int(company_id), "折现率配置变更"
            )
    return RedirectResponse("/discount-rates", status_code=303)
