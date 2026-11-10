-- 价值投资工作台 P0 数据库结构（SQLite）
-- 业务对象对应 PRD v2.0 第 14 章；字段命名保持可迁移（无 SQLite 专有类型）。

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL,
    former_names        TEXT,
    market              TEXT    NOT NULL CHECK (market IN ('A', 'HK', 'US', 'UNLISTED')),
    code                TEXT,
    industry            TEXT,
    industry_tags       TEXT,                       -- JSON 数组，如 ["医疗器械","高成长"]
    is_financial        INTEGER NOT NULL DEFAULT 0, -- 银行/保险/证券/信托
    is_cyclical         INTEGER NOT NULL DEFAULT 0,
    currency            TEXT    NOT NULL DEFAULT 'CNY',
    accounting_standard TEXT    NOT NULL DEFAULT 'CAS',
    statement_scope     TEXT    NOT NULL DEFAULT 'CONSOLIDATED',
    status              TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    source              TEXT    NOT NULL DEFAULT 'self' CHECK (source IN ('self', 'pool')),
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    UNIQUE (market, code)
);

CREATE TABLE IF NOT EXISTS periods (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    period_type     TEXT    NOT NULL CHECK (period_type IN ('annual', 'h1', 'q1', 'q3')),
    fiscal_year     INTEGER NOT NULL,
    period_seq      INTEGER NOT NULL DEFAULT 0,
    start_date      TEXT,
    end_date        TEXT,
    audited         INTEGER,
    is_corrected    INTEGER NOT NULL DEFAULT 0,
    authority_rank  INTEGER NOT NULL DEFAULT 3,      -- 1 更正版 > 2 正式 > 3 摘要 > 4 英文
    status          TEXT    NOT NULL DEFAULT 'valid' CHECK (status IN ('valid', 'superseded')),
    created_at      TEXT    NOT NULL,
    UNIQUE (company_id, period_type, fiscal_year, period_seq)
);

CREATE TABLE IF NOT EXISTS ingestion_batches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    trigger_type    TEXT    NOT NULL DEFAULT 'upload' CHECK (trigger_type IN ('upload', 'auto', 'retry')),
    operator        TEXT    NOT NULL DEFAULT 'assistant',
    status          TEXT    NOT NULL DEFAULT 'parsing',
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    result_summary  TEXT,
    failures        TEXT,                            -- JSON
    note            TEXT
);

CREATE TABLE IF NOT EXISTS source_files (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id        INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    batch_id          INTEGER REFERENCES ingestion_batches(id) ON DELETE SET NULL,
    filename          TEXT    NOT NULL,
    sha256            TEXT    NOT NULL,
    stored_path       TEXT    NOT NULL,
    size_bytes        INTEGER,
    page_count        INTEGER,
    channel           TEXT,                          -- cninfo / sec / upload
    source_url        TEXT,
    announcement_no   TEXT,
    disclosure_date   TEXT,
    fetched_at        TEXT,
    content_type      TEXT,                          -- annual/interim/quarter/other
    is_scanned        INTEGER NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'unusable')),
    superseded_by_id  INTEGER REFERENCES source_files(id) ON DELETE SET NULL,
    created_at        TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_source_files_sha ON source_files (sha256);

CREATE TABLE IF NOT EXISTS metrics (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    code                  TEXT    NOT NULL UNIQUE,
    name                  TEXT    NOT NULL,
    group_code            TEXT    NOT NULL,          -- G1–G7
    definition            TEXT,
    unit_type             TEXT    NOT NULL,          -- amount / ratio / days / volume
    dimension_type        TEXT    NOT NULL DEFAULT 'none' CHECK (dimension_type IN ('none', 'product', 'region', 'channel')),
    is_valuation_required INTEGER NOT NULL DEFAULT 0,
    is_enabled            INTEGER NOT NULL DEFAULT 1,
    version               INTEGER NOT NULL DEFAULT 1,
    sort_order            INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS metric_values (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    period_id        INTEGER NOT NULL REFERENCES periods(id) ON DELETE CASCADE,
    metric_id        INTEGER NOT NULL REFERENCES metrics(id),
    dimension_type   TEXT    NOT NULL DEFAULT 'none',
    dimension_name   TEXT    NOT NULL DEFAULT '合计',
    value            REAL,
    unit             TEXT    NOT NULL DEFAULT '元',
    raw_value        REAL,
    raw_unit         TEXT,
    raw_currency     TEXT,
    status           TEXT    NOT NULL DEFAULT 'extracted'
                     CHECK (status IN ('extracted', 'pending', 'confirmed', 'locked', 'disputed', 'void')),
    version_no       INTEGER NOT NULL DEFAULT 1,
    is_current       INTEGER NOT NULL DEFAULT 1,
    is_derived       INTEGER NOT NULL DEFAULT 0,
    derived_from     TEXT,                          -- JSON
    caliber_note     TEXT,
    confidence       TEXT    NOT NULL DEFAULT 'medium' CHECK (confidence IN ('high', 'medium', 'low')),
    is_manual        INTEGER NOT NULL DEFAULT 0,
    confirmed_by     TEXT,
    confirmed_at     TEXT,
    created_at       TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_metric_values_current
    ON metric_values (company_id, period_id, metric_id, dimension_type, dimension_name)
    WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS ix_metric_values_lookup
    ON metric_values (company_id, metric_id, period_id);

CREATE TABLE IF NOT EXISTS extractions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_value_id    INTEGER NOT NULL REFERENCES metric_values(id) ON DELETE CASCADE,
    source_file_id     INTEGER NOT NULL REFERENCES source_files(id),
    page_no            INTEGER,
    table_name         TEXT,
    row_label          TEXT,
    column_label       TEXT,
    raw_value          TEXT,
    raw_unit           TEXT,
    raw_currency       TEXT,
    statement_scope    TEXT,
    audited            INTEGER,
    extractor_version  TEXT    NOT NULL,
    confidence         TEXT    NOT NULL,
    dimension          TEXT,
    created_at         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    code             TEXT    NOT NULL,              -- S1, S2 ...
    reason           TEXT    NOT NULL,              -- first_build / new_period / revalue / restatement / manual
    note             TEXT,
    period_ids       TEXT    NOT NULL,              -- JSON
    value_ids        TEXT    NOT NULL,              -- JSON
    value_count      INTEGER NOT NULL,
    created_at       TEXT    NOT NULL,
    created_by       TEXT    NOT NULL DEFAULT 'assistant',
    UNIQUE (company_id, code)
);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS report_versions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id           INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    version_no          INTEGER NOT NULL,
    based_on_snapshot_id INTEGER REFERENCES snapshots(id),
    coverage            TEXT,                        -- JSON: 覆盖的报告期
    status              TEXT    NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'pending_review', 'published', 'archived')),
    change_summary      TEXT,                        -- 文本
    change_meta         TEXT,                        -- JSON
    forced_publish_note TEXT,
    generated_at        TEXT    NOT NULL,
    published_at        TEXT,
    published_by        TEXT,
    UNIQUE (report_id, version_no)
);

CREATE TABLE IF NOT EXISTS report_sections (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id       INTEGER NOT NULL REFERENCES report_versions(id) ON DELETE CASCADE,
    part_no          INTEGER NOT NULL,
    part_code        TEXT    NOT NULL,
    title            TEXT    NOT NULL,
    body             TEXT    NOT NULL,               -- JSON blocks
    change_type      TEXT    NOT NULL DEFAULT 'new'
                     CHECK (change_type IN ('new', 'carried', 'data_refresh', 'conclusion_rewrite', 'deleted')),
    carry_over_from  INTEGER,                        -- 来源版本号
    cited_value_ids  TEXT,                           -- JSON
    review_status    TEXT    NOT NULL DEFAULT 'not_required'
                     CHECK (review_status IN ('not_required', 'pending', 'still_valid', 'needs_rewrite', 'invalid')),
    review_reason    TEXT,
    reviewed_at      TEXT,
    reviewed_by      TEXT,
    UNIQUE (version_id, part_code)
);

CREATE TABLE IF NOT EXISTS section_changes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id       INTEGER NOT NULL REFERENCES report_versions(id) ON DELETE CASCADE,
    part_code        TEXT    NOT NULL,
    change_type      TEXT    NOT NULL,
    affected_periods TEXT,                           -- JSON
    old_value        TEXT,
    new_value        TEXT,
    old_text         TEXT,
    new_text         TEXT
);

CREATE TABLE IF NOT EXISTS valuation_models (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    method      TEXT    NOT NULL DEFAULT 'A' CHECK (method IN ('A', 'B')),
    scheme      TEXT    NOT NULL DEFAULT '中性',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    UNIQUE (company_id, method, scheme)
);

CREATE TABLE IF NOT EXISTS valuation_versions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id             INTEGER NOT NULL REFERENCES valuation_models(id) ON DELETE CASCADE,
    company_id           INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    version_no           INTEGER NOT NULL,
    scheme               TEXT    NOT NULL,
    method               TEXT    NOT NULL DEFAULT 'A',
    based_on_snapshot_id INTEGER REFERENCES snapshots(id),
    base_period_id       INTEGER REFERENCES periods(id),
    base_fcf_method      TEXT    NOT NULL,
    base_fcf_value       REAL,
    fcf_detail           TEXT,                       -- JSON: [{year, fcf, ocf, capex}]
    params               TEXT    NOT NULL,           -- JSON: g1/g2/g/r/n/r_source/config_version
    projection           TEXT,                       -- JSON: [{year, fcf, factor, pv}]
    net_cash             TEXT,                       -- JSON: 明细
    shares               REAL,
    market_cap           REAL,
    market_cap_date      TEXT,
    enterprise_value     REAL,
    equity_value         REAL,
    per_share_value      REAL,
    premium_discount     REAL,
    stability            REAL,
    applicability        TEXT    NOT NULL DEFAULT 'normal'
                         CHECK (applicability IN ('normal', 'reference_only', 'not_applicable')),
    applicability_note   TEXT,
    sensitivity          TEXT,                       -- JSON
    input_values         TEXT,                       -- JSON: [{code, value_id, value, version_no, status}]
    status               TEXT    NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft', 'pending_confirm', 'confirmed', 'pending_review', 'archived')),
    status_reason        TEXT,
    created_at           TEXT    NOT NULL,
    confirmed_at         TEXT,
    confirmed_by         TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_valuation_version_no
    ON valuation_versions (model_id, version_no);

CREATE TABLE IF NOT EXISTS discount_rate_configs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_level    TEXT    NOT NULL CHECK (scope_level IN ('global', 'industry', 'company', 'override')),
    industry_tag   TEXT,
    company_id     INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    rate           REAL    NOT NULL,
    effective_from TEXT    NOT NULL,
    config_version INTEGER NOT NULL,
    changed_by     TEXT    NOT NULL DEFAULT 'user',
    reason         TEXT,
    created_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_discount_rate_scope
    ON discount_rate_configs (scope_level, industry_tag, company_id, config_version);

CREATE TABLE IF NOT EXISTS review_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    object_type TEXT    NOT NULL,                   -- metric_value / report_section / valuation_version / snapshot
    object_id   INTEGER NOT NULL,
    action      TEXT    NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    reason      TEXT,
    actor       TEXT    NOT NULL DEFAULT 'user',
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id   INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    channel      TEXT    NOT NULL,
    url          TEXT    NOT NULL,
    requested_at TEXT    NOT NULL,
    status_code  INTEGER,
    retry_count  INTEGER NOT NULL DEFAULT 0,
    elapsed_ms   INTEGER,
    note         TEXT
);
