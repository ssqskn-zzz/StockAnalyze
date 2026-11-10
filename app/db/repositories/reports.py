"""报告与报告版本的仓储。报告版本只增不改。"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from app.utils import json_dumps, now_iso


def ensure_report(conn: sqlite3.Connection, company_id: int, title: str) -> int:
    row = conn.execute(
        "SELECT id FROM reports WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO reports (company_id, title, created_at) VALUES (?,?,?)",
        (company_id, title, now_iso()),
    )
    return int(cur.lastrowid)


def next_version_no(conn: sqlite3.Connection, report_id: int) -> int:
    row = conn.execute(
        "SELECT MAX(version_no) AS v FROM report_versions WHERE report_id=?", (report_id,)
    ).fetchone()
    return int(row["v"] or 0) + 1


def create_version(
    conn: sqlite3.Connection,
    *,
    report_id: int,
    company_id: int,
    version_no: int,
    snapshot_id: int | None,
    coverage: list[dict[str, Any]],
    status: str = "draft",
) -> int:
    cur = conn.execute(
        """INSERT INTO report_versions
           (report_id, company_id, version_no, based_on_snapshot_id, coverage, status, generated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (report_id, company_id, version_no, snapshot_id, json_dumps(coverage), status, now_iso()),
    )
    return int(cur.lastrowid)


def add_section(
    conn: sqlite3.Connection,
    *,
    version_id: int,
    part_no: int,
    part_code: str,
    title: str,
    body: list[dict[str, Any]],
    change_type: str,
    carry_over_from: int | None,
    cited_value_ids: Iterable[int],
    review_status: str = "not_required",
) -> int:
    cur = conn.execute(
        """INSERT INTO report_sections
           (version_id, part_no, part_code, title, body, change_type, carry_over_from,
            cited_value_ids, review_status)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            version_id, part_no, part_code, title, json_dumps(body), change_type,
            carry_over_from, json_dumps(list(cited_value_ids)), review_status,
        ),
    )
    return int(cur.lastrowid)


def add_section_change(
    conn: sqlite3.Connection,
    *,
    version_id: int,
    part_code: str,
    change_type: str,
    affected_periods: list[str] | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    old_text: str | None = None,
    new_text: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO section_changes
           (version_id, part_code, change_type, affected_periods, old_value, new_value,
            old_text, new_text)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            version_id, part_code, change_type, json_dumps(affected_periods or []),
            old_value, new_value, old_text, new_text,
        ),
    )


def get_version(conn: sqlite3.Connection, version_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM report_versions WHERE id=?", (version_id,)).fetchone()


def get_version_by_no(conn: sqlite3.Connection, company_id: int, version_no: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM report_versions WHERE company_id=? AND version_no=? ORDER BY id DESC LIMIT 1",
        (company_id, version_no),
    ).fetchone()


def list_versions(conn: sqlite3.Connection, company_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM report_versions WHERE company_id=? ORDER BY version_no DESC", (company_id,)
    ))


def latest_version(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM report_versions WHERE company_id=? ORDER BY version_no DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return row


def latest_published(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM report_versions WHERE company_id=? AND status='published' "
        "ORDER BY version_no DESC LIMIT 1",
        (company_id,),
    ).fetchone()


def get_sections(conn: sqlite3.Connection, version_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM report_sections WHERE version_id=? ORDER BY part_no", (version_id,)
    ))


def get_section(conn: sqlite3.Connection, section_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM report_sections WHERE id=?", (section_id,)).fetchone()


def set_section_review(
    conn: sqlite3.Connection, section_id: int, decision: str, reason: str = "", actor: str = "user"
) -> None:
    if decision not in ("still_valid", "needs_rewrite", "invalid"):
        raise ValueError("无效的复核结论")
    conn.execute(
        """UPDATE report_sections
           SET review_status=?, review_reason=?, reviewed_at=?, reviewed_by=?
           WHERE id=?""",
        (decision, reason, now_iso(), actor, section_id),
    )


def set_changes(conn: sqlite3.Connection, version_id: int, summary: str, meta: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE report_versions SET change_summary=?, change_meta=? WHERE id=?",
        (summary, json_dumps(meta), version_id),
    )


def set_status(
    conn: sqlite3.Connection, version_id: int, status: str, *, published_by: str | None = None,
    forced_note: str | None = None,
) -> None:
    if status == "published":
        conn.execute(
            "UPDATE report_versions SET status=?, published_at=?, published_by=?, forced_publish_note=? "
            "WHERE id=?",
            (status, now_iso(), published_by or "user", forced_note, version_id),
        )
    else:
        conn.execute("UPDATE report_versions SET status=? WHERE id=?", (status, version_id))


def archive_older_published(conn: sqlite3.Connection, company_id: int, keep_version_id: int) -> None:
    conn.execute(
        "UPDATE report_versions SET status='archived' "
        "WHERE company_id=? AND status='published' AND id<>?",
        (company_id, keep_version_id),
    )


def list_section_changes(conn: sqlite3.Connection, version_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM section_changes WHERE version_id=? ORDER BY part_code", (version_id,)
    ))


def pending_qualitative_sections(conn: sqlite3.Connection, version_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        """SELECT * FROM report_sections
           WHERE version_id=? AND part_code IN ('part6','part7')
             AND review_status IN ('pending','needs_rewrite')""",
        (version_id,),
    ))
