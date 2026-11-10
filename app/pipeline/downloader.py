"""材料自动获取（助手侧）。

P0 实现两个真实通道：
- A 股：巨潮资讯网公告检索接口（按代码 + 年报类别），串行 + 间隔 1.5s + 指数退避；
- 美股：SEC EDGAR submissions 接口（按 CIK/ticker 取 10-K / 20-F），间隔 0.25s。
港股（披露易）按 PRD Q-08 默认关闭，P0 未实现自动抓取，需用户投递文件。

所有请求写入 fetch_logs（渠道、URL、时间、状态码、重试次数、耗时）。
抓取只负责把文件取回来；抽取结果仍需人工确认。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from app import config
from app.db.repositories import companies as company_repo
from app.pipeline.period_detector import detect_period
from app.utils import now_iso

SEC_UA = "StockAnalyze/0.1 (internal research; contact: local-user)"
CNINFO_UA = "Mozilla/5.0 (compatible; StockAnalyze/0.1; internal research)"
CNINFO_QUERY = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_SEARCH = "http://www.cninfo.com.cn/new/information/topSearch/query"
CNINFO_STATIC = "http://static.cninfo.com.cn/"
CNINFO_CATEGORIES = {
    "annual": ("category_ndbg_szsh", "年度报告"),
    "h1": ("category_bndbg_szsh", "半年度报告"),
    "q1": ("category_yjdbg_szsh", "第一季度报告"),
    "q3": ("category_sjdbg_szsh", "第三季度报告"),
}
CNINFO_EXCLUDE = ("摘要", "英文", "已取消", "更正", "问询", "说明")
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"


class FetchError(RuntimeError):
    pass


def _request(conn, session: requests.Session, *, channel: str, url: str, company_id: int,
             method: str = "GET", **kwargs) -> requests.Response:
    interval = config.FETCH_INTERVAL.get(channel, 1.0)
    retries = 0
    started = time.time()
    status_code = None
    last_error = None
    while retries <= 3:
        time.sleep(interval)
        try:
            response = session.request(method, url, timeout=40, **kwargs)
            status_code = response.status_code
            if response.status_code in (403, 429) or response.status_code >= 500:
                raise FetchError(f"HTTP {response.status_code}")
            company_repo.log_fetch(
                conn, company_id=company_id, channel=channel, url=url,
                status_code=status_code, retry_count=retries,
                elapsed_ms=int((time.time() - started) * 1000),
                note="" if response.ok else "非 2xx",
            )
            return response
        except Exception as exc:  # noqa: BLE001 - 记录后重试
            last_error = exc
            retries += 1
            time.sleep(2 ** retries)
    company_repo.log_fetch(
        conn, company_id=company_id, channel=channel, url=url, status_code=status_code,
        retry_count=retries, elapsed_ms=int((time.time() - started) * 1000), note=str(last_error),
    )
    raise FetchError(f"请求失败（{channel}）：{url}，{last_error}")


def _save(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def fetch_sec(conn, company, *, years: int = 5, limit: int = 10) -> list[dict[str, Any]]:
    """按 ticker 取 10-K / 20-F 主文档。"""
    ticker = (company["code"] or "").upper()
    if not ticker:
        raise FetchError("美股企业缺少 ticker")
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"})
    resp = _request(conn, session, channel="sec", url=SEC_TICKERS, company_id=company["id"])
    data = resp.json()
    cik = None
    for entry in data.values():
        if str(entry.get("ticker", "")).upper() == ticker:
            cik = int(entry["cik_str"])
            break
    if cik is None:
        raise FetchError(f"SEC 未找到 ticker：{ticker}")
    resp = _request(
        conn, session, channel="sec", url=SEC_SUBMISSIONS.format(cik=f"{cik:010d}"),
        company_id=company["id"],
    )
    recent = resp.json().get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    picked: list[dict[str, Any]] = []
    current_year = int(now_iso()[:4])
    for form, filing_date, accession, doc in zip(forms, dates, accessions, docs):
        if form not in ("10-K", "20-F"):
            continue
        try:
            year = int(str(filing_date)[:4])
        except ValueError:
            continue
        if year < current_year - years + 1:
            continue
        accession_path = accession.replace("-", "")
        url = SEC_ARCHIVE.format(cik=cik, accession=accession_path, document=doc)
        try:
            file_resp = _request(conn, session, channel="sec", url=url, company_id=company["id"])
        except FetchError as exc:
            picked.append({"error": str(exc), "url": url})
            continue
        filename = f"{ticker}_{form}_{filing_date}.pdf" if url.lower().endswith(".pdf") else f"{ticker}_{form}_{filing_date}{Path(doc).suffix}"
        saved = _save(config.DATA_DIR / "downloads" / filename, file_resp.content)
        picked.append({
            "path": str(saved),
            "filename": filename,
            "channel": "sec",
            "source_url": url,
            "announcement_no": accession,
            "disclosure_date": filing_date,
        })
        if len(picked) >= min(limit, config.FETCH_MAX_FILES):
            break
    if not picked:
        raise FetchError("SEC 未检索到符合条件的 10-K / 20-F")
    return picked


def _cninfo_org(conn, session: requests.Session, company, code: str) -> tuple[str, str]:
    """解析巨潮 orgId 与检索栏目（接口必须传 code,orgId 才能命中）。"""
    resp = _request(
        conn, session, channel="cninfo", url=CNINFO_SEARCH, company_id=company["id"],
        method="POST", data={"keyWord": code, "maxNum": 10},
    )
    for entry in resp.json() or []:
        if str(entry.get("code")) == code and entry.get("orgId"):
            org_id = str(entry["orgId"])
            if org_id.startswith("gssh"):
                column = "sse"
            elif org_id.startswith("gssz"):
                column = "szse"
            elif org_id.startswith("gsbj"):
                column = "bj"
            else:
                column = "sse" if code.startswith("6") else ("bj" if code[0] in "489" else "szse")
            return org_id, column
    raise FetchError(f"巨潮未找到证券代码：{code}")


def fetch_cninfo(
    conn, company, *, years: int = 5, limit: int = 10,
    period_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """按证券代码检索巨潮公告并下载 PDF（年报/半年报/一季报/三季报）。"""
    code = company["code"]
    if not code:
        raise FetchError("A 股企业缺少证券代码")
    session = requests.Session()
    session.headers.update({"User-Agent": CNINFO_UA, "Referer": "http://www.cninfo.com.cn/"})
    org_id, column = _cninfo_org(conn, session, company, code)
    end_year = int(now_iso()[:4])
    period_types = period_types or ["annual"]
    picked: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for period_type in period_types:
        if period_type not in CNINFO_CATEGORIES:
            raise FetchError(f"不支持的公告类型：{period_type}")
        category, keyword = CNINFO_CATEGORIES[period_type]
        payload = {
            "pageNum": 1,
            "pageSize": 30,
            "column": column,
            "tabName": "fulltext",
            "plate": "",
            "stock": f"{code},{org_id}",
            "searchkey": "",
            "secid": "",
            "category": category,
            "trade": "",
            "seDate": f"{end_year - years}-01-01~{end_year}-12-31",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        resp = _request(conn, session, channel="cninfo", url=CNINFO_QUERY, company_id=company["id"],
                        method="POST", data=payload)
        announcements = resp.json().get("announcements") or []
        for item in announcements:
            title = item.get("announcementTitle") or ""
            if keyword not in title or any(word in title for word in CNINFO_EXCLUDE):
                continue
            period = detect_period(title)
            if not period or period[0] != period_type or period[1] < end_year - years:
                continue
            adjunct = item.get("adjunctUrl")
            if not adjunct:
                continue
            url = CNINFO_STATIC + adjunct
            if url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                file_resp = _request(conn, session, channel="cninfo", url=url, company_id=company["id"])
            except FetchError as exc:
                picked.append({"error": str(exc), "url": url})
                continue
            filename = f"{code}_{title.replace('/', '_')}.pdf"
            saved = _save(config.DATA_DIR / "downloads" / filename, file_resp.content)
            picked.append({
                "path": str(saved),
                "filename": filename,
                "channel": "cninfo",
                "period_type": period_type,
                "fiscal_year": period[1],
                "source_url": url,
                "announcement_no": str(item.get("announcementId") or ""),
                "disclosure_date": str(item.get("announcementTime") or "")[:10],
            })
            if len(picked) >= min(limit, config.FETCH_MAX_FILES):
                return picked
    if not picked:
        raise FetchError("巨潮未检索到匹配公告（可能代码/类型不匹配，或接口字段变化）")
    return picked


def fetch_for_company(
    conn, company, *, years: int = 5, allow_hk: bool = False,
    period_types: list[str] | None = None, limit: int = 10,
) -> list[dict[str, Any]]:
    market = company["market"]
    if market == "A":
        return fetch_cninfo(conn, company, years=years, period_types=period_types, limit=limit)
    if market == "US":
        return fetch_sec(conn, company, years=years)
    if market == "HK":
        if not allow_hk:
            raise FetchError("港股自动获取默认关闭（需显式授权 --allow-hk），请投递文件")
        raise FetchError("港股自动获取（披露易）P0 未实现，请投递文件")
    raise FetchError("非上市企业无公开渠道，请投递文件")
