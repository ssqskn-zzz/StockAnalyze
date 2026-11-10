"""助手侧命令行：投递、抽取、确认、快照、报告、估值。

工作台网页不提供"上传/生成/抓取"按钮（PRD 2.4），这些动作都在这里执行。
用法示例：
  python -m app.cli init-db
  python -m app.cli company-add --name 示例公司 --market A --code 600000 --industry 医疗器械
  python -m app.cli ingest --company 1 --file a.xlsx --file b.pdf
  python -m app.cli confirm --company 1 --high
  python -m app.cli snapshot --company 1 --reason first_build
  python -m app.cli report --company 1
  python -m app.cli value --company 1 --market-cap 120000000000 --shares 9000000000
  python -m app.cli serve
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app import config
from app.db import connection
from app.db.repositories import audit
from app.db.repositories import companies as company_repo
from app.db.repositories import metrics as metric_repo
from app.db.repositories import reports as report_repo
from app.db.repositories import valuations as valuation_repo
from app.domain.metrics_catalog import METRICS
from app.pipeline import downloader
from app.services import ingestion_service, metric_service, report_service, valuation_service


def _fmt_yi(value: float | None) -> str:
    return "—" if value is None else f"{value / 1e8:,.2f} 亿元"


def _get_company(conn, company_id: int):
    company = company_repo.get_company(conn, company_id)
    if not company:
        print(f"企业 id={company_id} 不存在")
        sys.exit(2)
    return company


def cmd_init(args) -> None:
    connection.init_db()
    with connection.session() as conn:
        inserted = metric_repo.seed_metrics(conn)
        seeded = valuation_repo.ensure_default_config(conn)
    print(f"数据库已初始化：{config.DB_PATH}")
    print(f"指标字典：新增 {inserted} 项，共 {len(METRICS)} 项；折现率默认配置：{'已写入' if seeded else '已存在'}")


def cmd_company_add(args) -> None:
    with connection.session() as conn:
        try:
            company_id = company_repo.create_company(
                conn, name=args.name, market=args.market, code=args.code,
                industry=args.industry, is_financial=args.financial,
                is_cyclical=args.cyclical, currency=args.currency,
                accounting_standard=args.accounting,
            )
        except company_repo.DuplicateCompanyError as exc:
            print(str(exc))
            sys.exit(1)
    print(f"已创建企业 id={company_id}：{args.name}（{args.market}·{args.code or '无代码'}）")


def cmd_list(args) -> None:
    with connection.session() as conn:
        for company in company_repo.list_companies(conn):
            stats = metric_service.company_stats(conn, company["id"])
            print(f"[{company['id']}] {company['name']}（{company['market']}·{company['code'] or '—'}）"
                  f" 报告期 {stats['period_count']}｜待确认 {stats['pending']}｜"
                  f"报告 {('v' + str(stats['report']['version_no'])) if stats['report'] else '—'}｜"
                  f"估值 {('V' + str(stats['valuation']['version_no'])) if stats['valuation'] else '—'}")


def cmd_ingest(args) -> None:
    declared = {}
    for item in args.period or []:
        if "=" not in item:
            print("--period 格式应为 文件名=2024年报")
            sys.exit(2)
        filename, text = item.split("=", 1)
        declared[filename] = ingestion_service.parse_period_text(text)
    with connection.session() as conn:
        _get_company(conn, args.company)
        report = ingestion_service.ingest_files(
            conn, args.company, [Path(f) for f in args.file],
            declared_periods=declared, trigger="upload", note=args.note or "",
        )
        print(f"批次 B{report['batch_id']}：{report['summary']}")
        for entry in report["files"]:
            print(f"  - {entry['file']}：抽取 {entry['values']} 项"
                  + (f"；警告：{'；'.join(entry['warnings'])}" if entry["warnings"] else ""))
        for warning in report["warnings"]:
            print(f"  ! {warning}")
        for conflict in report["conflicts"]:
            print(f"  ! 冲突：{conflict['period']} {conflict['metric']}（{conflict['message']}）")
        if report["duplicates"]:
            for dup in report["duplicates"]:
                print(f"  = 跳过重复文件：{dup['file']}（已存在 id={dup['existing_id']}）")
        if args.confirm_high:
            count = metric_service.confirm_high_confidence(conn, args.company)
            print(f"已批量确认高置信度指标值 {count} 项")
        if args.snapshot:
            snapshot_id = metric_service.create_snapshot(conn, args.company, "manual", args.note or "")
            print(f"已生成快照 id={snapshot_id}")


def cmd_fetch(args) -> None:
    with connection.session() as conn:
        company = _get_company(conn, args.company)
        try:
            fetched = downloader.fetch_for_company(
                conn, company, years=args.years, allow_hk=args.allow_hk,
                period_types=[t.strip() for t in args.types.split(",") if t.strip()],
                limit=args.limit,
            )
        except downloader.FetchError as exc:
            print(f"自动获取失败：{exc}")
            print("可改用手工投递：python -m app.cli ingest --company ID --file <文件>")
            sys.exit(1)
        paths = [item["path"] for item in fetched if item.get("path")]
        meta = {item["filename"]: item for item in fetched if item.get("path")}
        print(f"已获取 {len(paths)} 份文件：")
        for item in fetched:
            if item.get("path"):
                print(f"  - {item['filename']}（{item['channel']}，{item.get('disclosure_date') or ''}）")
            else:
                print(f"  ! 下载失败：{item.get('url')} {item.get('error')}")
        if args.ingest and paths:
            report = ingestion_service.ingest_files(
                conn, args.company, paths, trigger="auto", channel="auto", file_meta=meta,
                note=f"自动获取（{company['market']}）",
            )
            print(f"入库批次 B{report['batch_id']}：{report['summary']}")
            for warning in report["warnings"]:
                print(f"  ! {warning}")


def cmd_pending(args) -> None:
    with connection.session() as conn:
        _get_company(conn, args.company)
        items = metric_service.pending_items(conn, args.company)
        if not items:
            print("没有待确认指标值")
            return
        for item in items:
            flag = "必需" if item["required"] else "    "
            print(f"#{item['id']:<5} {item['period']:<8} {item['metric']:<14} "
                  f"{item['value']:>16,.2f} {item['unit_type']:<7} {item['status']:<8} "
                  f"{item['confidence']:<6} {flag} ← {item['source'] or '手工'}")


def cmd_confirm(args) -> None:
    with connection.session() as conn:
        _get_company(conn, args.company)
        if args.value_id:
            for value_id in args.value_id:
                metric_service.confirm_value(conn, value_id)
            print(f"已确认 {len(args.value_id)} 项")
        if args.high:
            count = metric_service.confirm_high_confidence(conn, args.company)
            print(f"已确认高置信度指标值 {count} 项")
        if getattr(args, "all", False):
            items = metric_service.pending_items(conn, args.company)
            for item in items:
                metric_service.confirm_value(conn, item["id"])
            print(f"已确认全部待确认指标值 {len(items)} 项")
        pending = metric_service.pending_items(conn, args.company)
        if pending and not args.high and not args.value_id and not getattr(args, "all", False):
            print(f"仍有 {len(pending)} 项待确认（可用 --high 或 --value-id 逐项确认）")


def cmd_override(args) -> None:
    with connection.session() as conn:
        new_id = metric_service.override_value(conn, args.value_id, args.value, args.reason)
        print(f"已改值，新指标值 id={new_id}（旧值保留；下游估值已转待复核）")


def cmd_dispute(args) -> None:
    with connection.session() as conn:
        metric_service.dispute_value(conn, args.value_id, args.reason)
        print("已标记争议；该值不进入快照的估值可用集合")


def cmd_snapshot(args) -> None:
    with connection.session() as conn:
        _get_company(conn, args.company)
        try:
            snapshot_id = metric_service.create_snapshot(conn, args.company, args.reason, args.note or "")
        except ValueError as exc:
            print(str(exc))
            sys.exit(1)
        snapshot = conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
    print(f"已生成数据快照 {snapshot['code']}（{snapshot['value_count']} 项指标值）")


def cmd_report(args) -> None:
    with connection.session() as conn:
        _get_company(conn, args.company)
        try:
            result = report_service.generate_report(conn, args.company)
        except ValueError as exc:
            print(f"生成失败：{exc}")
            sys.exit(1)
    print(f"已生成报告 v{result['version_no']}（id={result['version_id']}），基于快照 {result['snapshot']}")
    print(result["change_summary"])
    print("下一步：在网页逐条复核 Part 6/7 后发布。")


def cmd_review(args) -> None:
    with connection.session() as conn:
        report_service.review_qualitative(conn, args.version_id, args.section_id, args.decision, args.reason or "")
    print(f"已记录复核结论：section #{args.section_id} → {args.decision}")


def cmd_publish(args) -> None:
    with connection.session() as conn:
        try:
            report_service.publish(conn, args.version_id, force_reason=args.force_reason or "")
        except report_service.ReportPublishError as exc:
            print(f"无法发布：{exc}")
            sys.exit(1)
    print(f"报告版本 #{args.version_id} 已发布")


def cmd_value(args) -> None:
    with connection.session() as conn:
        _get_company(conn, args.company)
        try:
            result = valuation_service.run_valuation(
                conn, args.company, scheme=args.scheme, base_method=args.base,
                g1=args.g1, g2=args.g2, g=args.g, r_override=args.r,
                market_cap=args.market_cap, market_cap_date=args.market_cap_date,
                shares=args.shares, snapshot_id=args.snapshot_id,
            )
        except (valuation_service.ValuationError, ValueError) as exc:
            print(f"估值失败：{exc}")
            sys.exit(1)
        params = result["params"]
        print(f"估值 V{result['version_no']}（id={result['version_id']}）｜快照 {result['snapshot']}｜"
              f"r={params['r']:.2%}（{valuation_service.R_LEVEL_LABELS.get(params['r_source'])}）")
        print(f"  基准 FCF（{params['base_method']}）= {_fmt_yi(result['base_fcf'])}")
        print(f"  企业价值 = {_fmt_yi(result['enterprise_value'])}")
        print(f"  净现金 = {_fmt_yi(result['net_cash']['cash'])} + 交易性金融资产 "
              f"{_fmt_yi(result['net_cash']['trading_fin_assets'])} − 有息负债 "
              f"{_fmt_yi(result['net_cash']['interest_bearing_debt'])}")
        print(f"  股权价值 = {_fmt_yi(result['equity_value'])}"
              + (f"｜每股 {result['per_share_value']:,.2f} 元" if result['per_share_value'] else ""))
        if result["applicability_note"]:
            print(f"  说明：{result['applicability_note']}")
        if args.confirm:
            valuation_service.confirm(conn, result["version_id"])
            print("  已确认该估值版本")


def cmd_confirm_value(args) -> None:
    with connection.session() as conn:
        valuation_service.confirm(conn, args.version_id)
    print(f"估值版本 #{args.version_id} 已确认")


def cmd_rate_add(args) -> None:
    with connection.session() as conn:
        try:
            valuation_repo.create_config(
                conn, scope_level=args.scope, rate=args.rate,
                industry_tag=args.industry_tag, company_id=args.company,
                reason=args.reason or "",
            )
        except ValueError as exc:
            print(str(exc))
            sys.exit(1)
        count = valuation_service.mark_pending_after_upstream_change(conn, args.company, "折现率配置变更") if args.company else 0
    print(f"已新增折现率配置：{args.scope} {args.rate:.2%}" + (f"；{count} 个估值版本转待复核" if count else ""))


def cmd_serve(args) -> None:
    import uvicorn

    uvicorn.run("app.web.app:app", host=args.host, port=args.port, reload=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="价值投资工作台（助手侧）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="初始化数据库与指标字典")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("company-add", help="新建企业")
    p.add_argument("--name", required=True)
    p.add_argument("--market", required=True, choices=["A", "HK", "US", "UNLISTED"])
    p.add_argument("--code")
    p.add_argument("--industry")
    p.add_argument("--currency", default="CNY")
    p.add_argument("--accounting", default="CAS")
    p.add_argument("--financial", action="store_true")
    p.add_argument("--cyclical", action="store_true")
    p.set_defaults(func=cmd_company_add)

    p = sub.add_parser("list", help="企业列表")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("ingest", help="投递材料并抽取")
    p.add_argument("--company", type=int, required=True)
    p.add_argument("--file", action="append", required=True)
    p.add_argument("--period", action="append", help="文件名=2024年报（可选，用于覆盖识别结果）")
    p.add_argument("--confirm-high", action="store_true")
    p.add_argument("--snapshot", action="store_true")
    p.add_argument("--note")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("fetch", help="自动获取年报（A 股巨潮 / 美股 SEC）")
    p.add_argument("--company", type=int, required=True)
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--types", default="annual",
                   help="逗号分隔：annual,h1,q1,q3（默认仅年报）")
    p.add_argument("--limit", type=int, default=10, help="单次最多下载份数")
    p.add_argument("--allow-hk", action="store_true")
    p.add_argument("--ingest", action="store_true")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("pending", help="待确认清单")
    p.add_argument("--company", type=int, required=True)
    p.set_defaults(func=cmd_pending)

    p = sub.add_parser("confirm", help="确认指标值")
    p.add_argument("--company", type=int, required=True)
    p.add_argument("--value-id", type=int, action="append")
    p.add_argument("--high", action="store_true")
    p.add_argument("--all", action="store_true", help="确认全部待确认项（助手侧批量确认）")
    p.set_defaults(func=cmd_confirm)

    p = sub.add_parser("override", help="人工改值")
    p.add_argument("--value-id", type=int, required=True)
    p.add_argument("--value", type=float, required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_override)

    p = sub.add_parser("dispute", help="标记争议")
    p.add_argument("--value-id", type=int, required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_dispute)

    p = sub.add_parser("snapshot", help="生成数据快照")
    p.add_argument("--company", type=int, required=True)
    p.add_argument("--reason", default="manual",
                   choices=["first_build", "new_period", "revalue", "restatement", "manual"])
    p.add_argument("--note")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("report", help="生成深度分析报告")
    p.add_argument("--company", type=int, required=True)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("review", help="记录定性章节复核结论")
    p.add_argument("--version-id", type=int, required=True)
    p.add_argument("--section-id", type=int, required=True)
    p.add_argument("--decision", required=True, choices=["still_valid", "needs_rewrite", "invalid"])
    p.add_argument("--reason")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("publish", help="发布报告版本")
    p.add_argument("--version-id", type=int, required=True)
    p.add_argument("--force-reason")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("value", help="运行 DCF 估值")
    p.add_argument("--company", type=int, required=True)
    p.add_argument("--scheme", default="中性")
    p.add_argument("--base", default=config.DCF_DEFAULTS["base_method"],
                   choices=["1y", "avg3", "avg5", "avg7"])
    p.add_argument("--g1", type=float)
    p.add_argument("--g2", type=float)
    p.add_argument("--g", type=float)
    p.add_argument("--r", type=float)
    p.add_argument("--market-cap", type=float)
    p.add_argument("--market-cap-date")
    p.add_argument("--shares", type=float)
    p.add_argument("--snapshot-id", type=int)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_value)

    p = sub.add_parser("confirm-value", help="确认估值版本")
    p.add_argument("--version-id", type=int, required=True)
    p.set_defaults(func=cmd_confirm_value)

    p = sub.add_parser("rate-add", help="新增折现率配置")
    p.add_argument("--scope", required=True, choices=["global", "industry", "company"])
    p.add_argument("--rate", type=float, required=True)
    p.add_argument("--industry-tag")
    p.add_argument("--company", type=int)
    p.add_argument("--reason")
    p.set_defaults(func=cmd_rate_add)

    p = sub.add_parser("serve", help="启动工作台")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if args.command != "init-db":
        connection.init_db()
    args.func(args)


if __name__ == "__main__":
    main()
