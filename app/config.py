"""全局配置：路径、DCF 默认参数、抽取器版本。"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("STOCKANALYZE_DATA_DIR", BASE_DIR / "data"))
FILES_DIR = DATA_DIR / "files"
DB_PATH = Path(os.environ.get("STOCKANALYZE_DB", DATA_DIR / "workbench.db"))

EXTRACTOR_VERSION = "p0-1.0.0"

# DCF 默认参数（PRD 7.5）
DCF_DEFAULTS = {
    "base_method": "avg5",          # 最近一年 / avg3 / avg5 / avg7
    "explicit_years": 10,
    "g1": 0.10,                     # 第 1–5 年
    "g2": 0.05,                     # 第 6–10 年
    "g": 0.03,                      # 永续
    "r_default": 0.10,              # 折现率全局默认（配置项兜底）
}

# 折现率取值的允许区间（PRD 7.5）
R_MIN, R_MAX = 0.06, 0.20
G1_MIN, G1_MAX = -0.20, 0.40
G_RANGE = (0.0, 0.05)

# 报告发布：完整度低于该值时禁止发布（可强制覆盖并留痕）
COMPLETENESS_THRESHOLD = 0.80

# 估值市值有效期（天）
MARKET_CAP_VALID_DAYS = 90

# 自动获取限速（秒）与单批次上限（PRD 13.2）
FETCH_INTERVAL = {"cninfo": 1.5, "hkex": 2.0, "sec": 0.25}
FETCH_MAX_FILES = 40
