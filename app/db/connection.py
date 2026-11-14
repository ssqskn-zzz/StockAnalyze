"""SQLite 连接与初始化。

P0 使用标准库 sqlite3，所有 SQL 收敛在仓储层（app/db/repositories），
便于后续迁移到 PostgreSQL（schema.sql 未使用 SQLite 专有类型）。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app import config
from app.utils import json_dumps, json_loads

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str | Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate_reports(conn)
        _migrate_report_paths(conn)
        _migrate_single_valuation(conn)
        conn.commit()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _clean_valuation_params(raw: str | None) -> str:
    """迁移时清掉旧版本字段，并把旧的预期收益率换算为逐年 FCF 增长率。

    旧口径的 g1/g2 分段增长由 growth_rates 取代，去掉后估值会在下次打开时按新口径重算。
    """
    params = json_loads(raw, {})
    if not isinstance(params, dict):
        params = {}
    for key in ("r_source", "r_config_version", "expected_return_source",
                "expected_return_config_version", "g1", "g2"):
        params.pop(key, None)
    legacy = params.pop("expected_returns", None)
    if "growth_rates" not in params and isinstance(legacy, list):
        params["growth_rates"] = legacy
    return json_dumps(params)


def _migrate_valuation_param_columns(conn: sqlite3.Connection) -> None:
    """valuation_params 由「预期收益率」升级为「逐年增长率 + 折现率」。"""
    if not _table_exists(conn, "valuation_params"):
        return
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(valuation_params)")}
    if "expected_returns" in columns and "growth_rates" not in columns:
        conn.execute(
            "ALTER TABLE valuation_params RENAME COLUMN expected_returns TO growth_rates"
        )
    if "discount_rate" not in columns:
        default_rate = repr(float(config.DCF_DEFAULTS["r"]))
        conn.execute(
            f"ALTER TABLE valuation_params ADD COLUMN discount_rate REAL NOT NULL DEFAULT {default_rate}"
        )


def _migrate_single_valuation(conn: sqlite3.Connection) -> None:
    """把历史「多估值版本 + 折现率配置」收敛为单份估值与单份参数。

    - valuation_versions：每家企业只保留最新一条，写入 valuations 后删表；
    - discount_rate_configs：迁出最近一次企业级增长率与折现率到 valuation_params 后删表；
    - valuation_models 不再需要，一并删除。
    """
    _migrate_valuation_param_columns(conn)
    if _table_exists(conn, "valuation_versions"):
        rows = conn.execute(
            """SELECT v.* FROM valuation_versions v
               WHERE v.id = (SELECT MAX(id) FROM valuation_versions WHERE company_id=v.company_id)"""
        ).fetchall()
        for row in rows:
            exists = conn.execute(
                "SELECT 1 FROM valuations WHERE company_id=?", (row["company_id"],)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """INSERT INTO valuations
                   (company_id, based_on_snapshot_id, base_period_id, base_fcf_method, base_fcf_value,
                    fcf_detail, params, projection, net_cash, shares, market_cap, market_cap_date,
                    enterprise_value, equity_value, per_share_value, premium_discount, stability,
                    applicability, applicability_note, sensitivity, input_values, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["company_id"], row["based_on_snapshot_id"], row["base_period_id"],
                    row["base_fcf_method"], row["base_fcf_value"], row["fcf_detail"],
                    _clean_valuation_params(row["params"]),
                    row["projection"], row["net_cash"], row["shares"], row["market_cap"],
                    row["market_cap_date"], row["enterprise_value"], row["equity_value"],
                    row["per_share_value"], row["premium_discount"], row["stability"],
                    row["applicability"], row["applicability_note"], row["sensitivity"],
                    row["input_values"], row["created_at"], row["created_at"],
                ),
            )
        conn.execute("DROP TABLE valuation_versions")
    if _table_exists(conn, "valuation_models"):
        conn.execute("DROP TABLE valuation_models")
    if _table_exists(conn, "discount_rate_configs"):
        rows = conn.execute(
            """SELECT d.* FROM discount_rate_configs d
               WHERE d.scope_level='company' AND d.company_id IS NOT NULL
                 AND (d.expected_returns IS NOT NULL OR d.expected_return IS NOT NULL)
                 AND d.id = (SELECT MAX(id) FROM discount_rate_configs x
                             WHERE x.company_id=d.company_id AND x.scope_level='company')"""
        ).fetchall()
        for row in rows:
            values = json_loads(row["expected_returns"], None)
            if not isinstance(values, list) or len(values) != config.DCF_DEFAULTS["explicit_years"]:
                if row["expected_return"] is None:
                    continue
                values = [float(row["expected_return"])] * config.DCF_DEFAULTS["explicit_years"]
            conn.execute(
                "INSERT OR IGNORE INTO valuation_params "
                "(company_id, growth_rates, discount_rate, updated_at) VALUES (?,?,?,?)",
                (
                    row["company_id"],
                    json_dumps([float(v) for v in values]),
                    float(row["rate"]) if row["rate"] is not None else float(config.DCF_DEFAULTS["r"]),
                    row["created_at"],
                ),
            )
        conn.execute("DROP TABLE discount_rate_configs")
    # 已迁移过的库：清掉估值参数 JSON 中遗留的折现率来源/配置版本字段
    for row in conn.execute("SELECT id, params FROM valuations").fetchall():
        cleaned = _clean_valuation_params(row["params"])
        if cleaned != row["params"]:
            conn.execute("UPDATE valuations SET params=? WHERE id=?", (cleaned, row["id"]))


def _migrate_report_paths(conn: sqlite3.Connection) -> None:
    """把历史报告版本里记录的绝对路径改写为可随目录搬迁的相对路径。

    只处理能解析到真实文件的行；解析不到的行保持原样，读取时会再走兼容回落。
    """
    from app.services import report_files

    rows = conn.execute(
        "SELECT id, file_path FROM report_versions WHERE file_path IS NOT NULL AND file_path<>''"
    ).fetchall()
    for row in rows:
        resolved = report_files.resolve_path(row["file_path"])
        if resolved is None:
            continue
        portable = report_files.stored_path(resolved)
        if portable != row["file_path"]:
            conn.execute(
                "UPDATE report_versions SET file_path=? WHERE id=?", (portable, row["id"])
            )


def _migrate_reports(conn: sqlite3.Connection) -> None:
    """轻量迁移：报告正文外置为 Markdown 文件后，旧库需补列并把 body 改为可空。

    schema.sql 使用 CREATE TABLE IF NOT EXISTS，已有库不会自动改结构，这里做幂等补齐。
    """
    version_cols = {row["name"] for row in conn.execute("PRAGMA table_info(report_versions)")}
    if "file_path" not in version_cols:
        conn.execute("ALTER TABLE report_versions ADD COLUMN file_path TEXT")
    if "content_hash" not in version_cols:
        conn.execute("ALTER TABLE report_versions ADD COLUMN content_hash TEXT")

    # 报告改为「生成即发布」后，历史遗留的待审阅/草稿版本统一归档，避免出现 pending_review。
    conn.execute(
        "UPDATE report_versions SET status='archived' WHERE status IN ('pending_review', 'draft')"
    )

    body_col = next(
        (row for row in conn.execute("PRAGMA table_info(report_sections)") if row["name"] == "body"),
        None,
    )
    if body_col is None or not body_col["notnull"]:
        return
    # SQLite 不支持 ALTER COLUMN，重建表以去掉 body 的 NOT NULL 约束。
    conn.execute("ALTER TABLE report_sections RENAME TO report_sections_legacy")
    conn.execute(
        """CREATE TABLE report_sections (
               id               INTEGER PRIMARY KEY AUTOINCREMENT,
               version_id       INTEGER NOT NULL REFERENCES report_versions(id) ON DELETE CASCADE,
               part_no          INTEGER NOT NULL,
               part_code        TEXT    NOT NULL,
               title            TEXT    NOT NULL,
               body             TEXT,
               change_type      TEXT    NOT NULL DEFAULT 'new'
                                CHECK (change_type IN ('new', 'carried', 'data_refresh', 'conclusion_rewrite', 'deleted')),
               carry_over_from  INTEGER,
               cited_value_ids  TEXT,
               review_status    TEXT    NOT NULL DEFAULT 'not_required'
                                CHECK (review_status IN ('not_required', 'pending', 'still_valid', 'needs_rewrite', 'invalid')),
               review_reason    TEXT,
               reviewed_at      TEXT,
               reviewed_by      TEXT,
               UNIQUE (version_id, part_code)
           )"""
    )
    conn.execute(
        """INSERT INTO report_sections
           (id, version_id, part_no, part_code, title, body, change_type, carry_over_from,
            cited_value_ids, review_status, review_reason, reviewed_at, reviewed_by)
           SELECT id, version_id, part_no, part_code, title, body, change_type, carry_over_from,
                  cited_value_ids, review_status, review_reason, reviewed_at, reviewed_by
           FROM report_sections_legacy"""
    )
    conn.execute("DROP TABLE report_sections_legacy")


@contextmanager
def session(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
