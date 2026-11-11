"""企业数据获取编排：公开接口与公告下载并行，避免串行等待。

两个任务各自持有独立 SQLite 连接（WAL + busy timeout），网络请求互不阻塞：
- public：东方财富免费公开接口 → 指标库（pending）
- filing：巨潮/SEC 公告下载 → 内容哈希去重 → 可选入库
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.db import connection
from app.db.repositories import companies as company_repo
from app.pipeline import downloader, public_data
from app.services import ingestion_service


def _public_task(company_id: int, years: int) -> dict[str, Any]:
    try:
        with connection.session() as conn:
            company = company_repo.get_company(conn, company_id)
            if not company:
                return {"ok": False, "error": "企业不存在"}
            result = public_data.fetch_public_metrics(conn, company, years=years)
            return {"ok": True, **result}
    except Exception as exc:  # noqa: BLE001 - 并行任务需要把错误带回主流程
        return {"ok": False, "error": str(exc)}


def _filing_task(
    company_id: int, *, years: int, period_types: list[str], limit: int,
    allow_hk: bool, ingest: bool,
) -> dict[str, Any]:
    try:
        with connection.session() as conn:
            company = company_repo.get_company(conn, company_id)
            if not company:
                return {"ok": False, "error": "企业不存在"}
            try:
                fetched = downloader.fetch_for_company(
                    conn, company, years=years, allow_hk=allow_hk,
                    period_types=period_types, limit=limit,
                )
            except downloader.FetchError as exc:
                return {"ok": False, "error": str(exc), "files": []}
            new_files = [item for item in fetched if item.get("path") and not item.get("duplicate")]
            duplicates = [item for item in fetched if item.get("duplicate")]
            report = None
            if ingest and new_files:
                meta = {item["filename"]: item for item in new_files}
                report = ingestion_service.ingest_files(
                    conn, company_id, [item["path"] for item in new_files],
                    trigger="auto", channel="auto", file_meta=meta,
                    note=f"自动获取（{company['market']}）",
                )
            return {
                "ok": True,
                "files": fetched,
                "new_files": new_files,
                "duplicates": duplicates,
                "ingest": report,
            }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "files": []}


def acquire_company(
    company_id: int,
    *,
    years: int = 5,
    period_types: list[str] | None = None,
    limit: int = 10,
    allow_hk: bool = False,
    ingest: bool = True,
    include_public: bool = True,
) -> dict[str, Any]:
    """同时抓取公开数据与季报/年报，返回两路结果。"""
    types = period_types or ["annual"]
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="acquire") as pool:
        filing_future = pool.submit(
            _filing_task, company_id,
            years=years, period_types=types, limit=limit,
            allow_hk=allow_hk, ingest=ingest,
        )
        public_future = pool.submit(_public_task, company_id, years) if include_public else None
        filing = filing_future.result()
        public = public_future.result() if public_future else {"ok": False, "error": "已跳过公开数据抓取"}
    return {"company_id": company_id, "public": public, "filing": filing}
