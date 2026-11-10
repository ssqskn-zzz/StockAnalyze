"""测试夹具：临时数据库 + 可导入的真实结构工作簿。"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from app import config
from app.db import connection
from app.db.repositories import metrics as metric_repo
from app.db.repositories import valuations as valuation_repo


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FILES_DIR", tmp_path / "files")
    connection.init_db()
    with connection.session() as conn:
        metric_repo.seed_metrics(conn)
        valuation_repo.ensure_default_config(conn)
        conn.commit()  # 先释放写事务，避免与工作台请求的连接互锁
        yield conn


def year_values(idx: int, revenue0: float = 100.0, growth: float = 0.12) -> dict[str, float]:
    """构造一组自洽的年度数据（单位：亿元），所有勾稽关系成立。"""
    revenue = revenue0 * (1 + growth) ** idx
    cost = revenue * 0.60
    net_profit = revenue * 0.15
    deducted = revenue * 0.14
    parent = revenue * 0.148
    ocf = deducted * 1.10
    capex = revenue * 0.05
    investing = -capex * 1.2
    financing = -ocf * 0.30
    cash = revenue * 0.20
    trading = revenue * 0.05
    receivables = revenue * 0.18
    prepayments = revenue * 0.02
    inventory = revenue * 0.08
    current_assets = cash + trading + receivables + prepayments + inventory
    fixed = revenue * 0.15
    cip = revenue * 0.03
    intangible = revenue * 0.04
    dev = revenue * 0.01
    goodwill = revenue * 0.02
    lti = revenue * 0.03
    non_current = fixed + cip + intangible + dev + goodwill + lti
    total_assets = current_assets + non_current
    short_term = revenue * 0.02
    current_portion = 0.0
    long_term = revenue * 0.05
    bonds = 0.0
    payables = revenue * 0.10
    contract = revenue * 0.03
    payroll = revenue * 0.02
    taxes = revenue * 0.01
    current_liab = short_term + payables + contract + payroll + taxes + current_portion
    total_liab = current_liab + long_term + bonds
    equity = total_assets - total_liab
    return {
        "营业收入": revenue,
        "营业成本": cost,
        "扣非净利润": deducted,
        "归母净利润": parent,
        "净利润": net_profit,
        "经营活动现金流量净额": ocf,
        "销售费用": revenue * 0.10,
        "管理费用": revenue * 0.05,
        "研发费用": revenue * 0.08,
        "财务费用": revenue * 0.005,
        "毛利率": 0.40,
        "货币资金": cash,
        "交易性金融资产": trading,
        "应收款": receivables,
        "预付款项": prepayments,
        "存货": inventory,
        "流动资产合计": current_assets,
        "固定资产": fixed,
        "在建工程": cip,
        "无形资产": intangible,
        "开发支出": dev,
        "商誉": goodwill,
        "长期股权投资": lti,
        "非流动资产合计": non_current,
        "资产总计": total_assets,
        "短期借款": short_term,
        "应付款": payables,
        "合同负债": contract,
        "应付职工薪酬": payroll,
        "应交税费": taxes,
        "一年内到期的非流动负债": current_portion,
        "流动负债合计": current_liab,
        "长期借款": long_term,
        "应付债券": bonds,
        "非流动负债合计": long_term + bonds,
        "负债合计": total_liab,
        "所有者权益合计": equity,
        "股本": 10.0,
        "购建固定资产、无形资产和其他长期资产支付的现金": capex,
        "投资活动产生的现金流量净额": investing,
        "筹资活动产生的现金流量净额": financing,
        "现金及现金等价物净增加额": ocf + investing + financing,
    }


def build_workbook(
    path: Path,
    years: list[int],
    *,
    name: str = "测试医疗",
    code: str = "600000",
    market: str = "A",
    industry: str = "医疗器械",
    financial: bool = False,
    include_net_cash: bool = True,
    segments: bool = True,
) -> Path:
    wb = Workbook()
    meta = wb.active
    meta.title = "基本信息"
    meta.append(["公司名称", name])
    meta.append(["证券代码", code])
    meta.append(["市场", market])
    meta.append(["行业", industry])
    meta.append(["币种", "CNY"])
    meta.append(["会计准则", "CAS"])
    meta.append(["是否金融", "是" if financial else "否"])
    meta.append(["是否周期", "否"])
    meta.append(["金额单位", "亿元"])

    ws = wb.create_sheet("主要指标")
    header = ["指标"] + [f"{y}年报" for y in years]
    ws.append(header)
    data = [year_values(i) for i in range(len(years))]
    for code_name in data[0]:
        if not include_net_cash and code_name in ("货币资金", "短期借款", "长期借款"):
            continue
        ws.append([code_name] + [data[i][code_name] for i in range(len(years))])

    if segments:
        seg = wb.create_sheet("分部数据")
        seg.append(["维度类型", "维度名称", "指标"] + [f"{y}年报" for y in years[-2:]])
        for i, year_idx in enumerate(range(len(years) - 2, len(years))):
            revenue = data[year_idx]["营业收入"]
            cost = revenue * 0.6
            seg.append(["产品", "产品A", "营业收入", revenue * 0.6, revenue * 0.58])
            seg.append(["产品", "产品B", "营业收入", revenue * 0.4, revenue * 0.42])
            seg.append(["产品", "产品A", "营业成本", cost * 0.6, cost * 0.58])
            seg.append(["产品", "产品B", "营业成本", cost * 0.4, cost * 0.42])
            seg.append(["产品", "产品A", "毛利率", 0.40, 0.40])
            seg.append(["产品", "产品B", "毛利率", 0.40, 0.40])
            seg.append(["区域", "境内", "营业收入", revenue * 0.8, revenue * 0.79])
            seg.append(["区域", "境外", "营业收入", revenue * 0.2, revenue * 0.21])
            seg.append(["区域", "境内", "毛利率", 0.40, 0.40])
            seg.append(["区域", "境外", "毛利率", 0.40, 0.40])
    wb.save(path)
    return path
