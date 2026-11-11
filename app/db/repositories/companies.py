"""企业、报告期、来源文件、抽取批次、抓取日志的仓储。"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.domain.periods import period_end_date, period_sort_key, period_start_date
from app.utils import json_dumps, json_loads, now_iso


class DuplicateCompanyError(ValueError):
    pass


def create_company(
    conn: sqlite3.Connection,
    *,
    name: str,
    market: str,
    code: str | None,
    industry: str | None = None,
    industry_tags: list[str] | None = None,
    is_financial: bool = False,
    is_cyclical: bool = False,
    currency: str = "CNY",
    accounting_standard: str = "CAS",
) -> int:
    name = name.strip()
    if not name:
        raise ValueError("企业名称不能为空")
    existing = find_company(conn, name=name, code=code, market=market)
    if existing:
        raise DuplicateCompanyError(f"已存在企业：{existing['name']}（id={existing['id']}），避免同一公司分裂")
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO companies
           (name, market, code, industry, industry_tags, is_financial, is_cyclical,
            currency, accounting_standard, statement_scope, status, source, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            name, market, code, industry,
            json_dumps(industry_tags or ([industry] if industry else [])),
            int(is_financial), int(is_cyclical), currency, accounting_standard,
            "CONSOLIDATED", "active", "self", ts, ts,
        ),
    )
    return int(cur.lastrowid)


def find_company(
    conn: sqlite3.Connection, *, name: str | None = None, code: str | None = None, market: str | None = None
) -> sqlite3.Row | None:
    if code and market:
        row = conn.execute("SELECT * FROM companies WHERE code=? AND market=?", (code, market)).fetchone()
        if row:
            return row
    if name:
        return conn.execute("SELECT * FROM companies WHERE name=?", (name,)).fetchone()
    return None


def get_company(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()


def list_companies(conn: sqlite3.Connection, include_archived: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM companies"
    if not include_archived:
        sql += " WHERE status='active'"
    sql += " ORDER BY created_at DESC"
    return list(conn.execute(sql))


def update_company(conn: sqlite3.Connection, company_id: int, **fields: Any) -> None:
    allowed = {
        "name", "former_names", "industry", "industry_tags", "is_financial",
        "is_cyclical", "currency", "accounting_standard", "statement_scope", "status",
    }
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return
    if "industry_tags" in data and isinstance(data["industry_tags"], list):
        data["industry_tags"] = json_dumps(data["industry_tags"])
    sets = ", ".join(f"{k}=?" for k in data)
    conn.execute(
        f"UPDATE companies SET {sets}, updated_at=? WHERE id=?",
        (*data.values(), now_iso(), company_id),
    )


# ---------------- 报告期 ----------------

def get_or_create_period(
    conn: sqlite3.Connection,
    company_id: int,
    period_type: str,
    fiscal_year: int,
    period_seq: int = 0,
    *,
    audited: bool | None = None,
    is_corrected: bool = False,
    authority_rank: int = 2,
) -> tuple[int, bool]:
    row = conn.execute(
        """SELECT id FROM periods
           WHERE company_id=? AND period_type=? AND fiscal_year=? AND period_seq=?""",
        (company_id, period_type, fiscal_year, period_seq),
    ).fetchone()
    if row:
        return int(row["id"]), False
    cur = conn.execute(
        """INSERT OR IGNORE INTO periods
           (company_id, period_type, fiscal_year, period_seq, start_date, end_date,
            audited, is_corrected, authority_rank, status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            company_id, period_type, fiscal_year, period_seq,
            period_start_date(period_type, fiscal_year),
            period_end_date(period_type, fiscal_year),
            None if audited is None else int(audited),
            int(is_corrected), authority_rank, "valid", now_iso(),
        ),
    )
    if cur.rowcount == 0:
        # 并行获取时另一路线程可能已插入同一期间，回查即可
        row = conn.execute(
            """SELECT id FROM periods
               WHERE company_id=? AND period_type=? AND fiscal_year=? AND period_seq=?""",
            (company_id, period_type, fiscal_year, period_seq),
        ).fetchone()
        if row:
            return int(row["id"]), False
    return int(cur.lastrowid), True


def get_period(conn: sqlite3.Connection, period_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM periods WHERE id=?", (period_id,)).fetchone()


def list_periods(
    conn: sqlite3.Connection, company_id: int, *, annual_only: bool = False
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM periods WHERE company_id=? AND status='valid'"
    params: list[Any] = [company_id]
    if annual_only:
        sql += " AND period_type='annual'"
    rows = list(conn.execute(sql, params))
    return sorted(rows, key=lambda r: period_sort_key(r["period_type"], r["fiscal_year"], r["period_seq"]))


# ---------------- 抽取批次 ----------------

def create_batch(conn: sqlite3.Connection, company_id: int, trigger_type: str = "upload", note: str = "") -> int:
    cur = conn.execute(
        """INSERT INTO ingestion_batches
           (company_id, trigger_type, operator, status, started_at, note)
           VALUES (?,?,?,?,?,?)""",
        (company_id, trigger_type, "assistant", "parsing", now_iso(), note),
    )
    return int(cur.lastrowid)


def finish_batch(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    status: str,
    summary: str,
    failures: list[dict[str, Any]] | None = None,
) -> None:
    conn.execute(
        "UPDATE ingestion_batches SET status=?, finished_at=?, result_summary=?, failures=? WHERE id=?",
        (status, now_iso(), summary, json_dumps(failures or []), batch_id),
    )


def get_batch(conn: sqlite3.Connection, batch_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM ingestion_batches WHERE id=?", (batch_id,)).fetchone()


def list_batches(conn: sqlite3.Connection, company_id: int | None = None, limit: int = 100) -> list[sqlite3.Row]:
    if company_id:
        return list(conn.execute(
            "SELECT * FROM ingestion_batches WHERE company_id=? ORDER BY started_at DESC LIMIT ?",
            (company_id, limit),
        ))
    return list(conn.execute(
        "SELECT * FROM ingestion_batches ORDER BY started_at DESC LIMIT ?", (limit,)
    ))


# ---------------- 来源文件 ----------------

def find_source_file_by_hash(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM source_files WHERE sha256=?", (sha256,)).fetchone()


def create_source_file(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    batch_id: int | None,
    filename: str,
    sha256: str,
    stored_path: str,
    size_bytes: int | None = None,
    page_count: int | None = None,
    channel: str = "upload",
    source_url: str | None = None,
    announcement_no: str | None = None,
    disclosure_date: str | None = None,
    fetched_at: str | None = None,
    content_type: str | None = None,
    is_scanned: bool = False,
) -> int:
    cur = conn.execute(
        """INSERT INTO source_files
           (company_id, batch_id, filename, sha256, stored_path, size_bytes, page_count,
            channel, source_url, announcement_no, disclosure_date, fetched_at, content_type,
            is_scanned, status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            company_id, batch_id, filename, sha256, stored_path, size_bytes, page_count,
            channel, source_url, announcement_no, disclosure_date, fetched_at or now_iso(),
            content_type, int(is_scanned), "active", now_iso(),
        ),
    )
    return int(cur.lastrowid)


def get_source_file(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM source_files WHERE id=?", (file_id,)).fetchone()


def list_source_files(conn: sqlite3.Connection, company_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM source_files WHERE company_id=? ORDER BY created_at DESC", (company_id,)
    ))


def supersede_source_file(conn: sqlite3.Connection, old_id: int, new_id: int) -> None:
    conn.execute(
        "UPDATE source_files SET status='superseded', superseded_by_id=? WHERE id=?",
        (new_id, old_id),
    )


def update_file_status(conn: sqlite3.Connection, file_id: int, status: str) -> None:
    conn.execute("UPDATE source_files SET status=? WHERE id=?", (status, file_id))


# ---------------- 抓取日志 ----------------

def log_fetch(
    conn: sqlite3.Connection,
    *,
    company_id: int | None,
    channel: str,
    url: str,
    status_code: int | None,
    retry_count: int,
    elapsed_ms: int,
    note: str = "",
) -> None:
    conn.execute(
        """INSERT INTO fetch_logs
           (company_id, channel, url, requested_at, status_code, retry_count, elapsed_ms, note)
           VALUES (?,?,?,?,?,?,?,?)""",
        (company_id, channel, url, now_iso(), status_code, retry_count, elapsed_ms, note),
    )


def list_fetch_logs(conn: sqlite3.Connection, company_id: int | None = None, limit: int = 50) -> list[sqlite3.Row]:
    if company_id:
        return list(conn.execute(
            "SELECT * FROM fetch_logs WHERE company_id=? ORDER BY requested_at DESC LIMIT ?",
            (company_id, limit),
        ))
    return list(conn.execute("SELECT * FROM fetch_logs ORDER BY requested_at DESC LIMIT ?", (limit,)))


def decode_tags(row: sqlite3.Row) -> list[str]:
    return json_loads(row["industry_tags"], []) or []
