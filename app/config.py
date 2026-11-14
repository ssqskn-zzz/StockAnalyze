"""全局配置：路径、DCF 默认参数、抽取器版本。"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("STOCKANALYZE_DATA_DIR", BASE_DIR / "data"))
FILES_DIR = DATA_DIR / "files"
REPORTS_DIR = DATA_DIR / "reports"
DB_PATH = Path(os.environ.get("STOCKANALYZE_DB", DATA_DIR / "workbench.db"))

# 工作台监听：默认绑定全网卡，便于同局域网设备访问（PRD 2.5）
SERVE_HOST = os.environ.get("STOCKANALYZE_HOST", "0.0.0.0")
SERVE_PORT = int(os.environ.get("STOCKANALYZE_PORT", "8000"))

EXTRACTOR_VERSION = "p0-1.0.0"

# DCF 默认参数（PRD 7.5）
DCF_DEFAULTS = {
    "base_method": "avg5",          # 最近一年 / avg3 / avg5 / avg7
    "explicit_years": 10,
    "growth_rates": [0.10] * 5 + [0.05] * 5,  # 未来 10 年逐年 FCF 增长率：前 5 年 10%，后 5 年 5%
    "g": 0.03,                      # 永续增长率
    "r": 0.10,                      # 默认折现率，可按企业配置
}

# 折现率允许区间
R_MIN, R_MAX = 0.06, 0.20
# 逐年 FCF 增长率允许区间
GROWTH_MIN, GROWTH_MAX = -0.20, 0.40
G_RANGE = (0.0, 0.05)

# 报告发布：完整度低于该值时禁止发布（可强制覆盖并留痕）
COMPLETENESS_THRESHOLD = 0.80

# 估值市值有效期（天）
MARKET_CAP_VALID_DAYS = 90

# 自动获取限速（秒）与单批次上限（PRD 13.2）
FETCH_INTERVAL = {"cninfo": 1.5, "hkex": 2.0, "sec": 0.25}
FETCH_MAX_FILES = 40
