# 价值投资工作台

一个在本机运行的价值投资研究工具：把公司年报/季报和公开财务数据整理成可复核的指标库，生成带版本记录的深度分析报告，并给出可追溯的自由现金流（DCF）估值。

面向使用者，不是开发文档。按下面的步骤即可完成「建企业 → 取数据 → 确认指标 → 出报告 → 做估值 → 手机查看」。

---

## 1. 安装

需要 Python 3.10 或更高版本（Windows 安装时请勾选 **Add Python to PATH**）。

打开项目目录，在命令行执行：

```bat
python -m pip install -r requirements.txt
```

> 如果下载很慢，可换国内镜像：
> `python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

## 2. 启动

最简单的方式：**双击 `start.bat`**。

它会自动检查 Python 和依赖、检查端口是否已被占用，然后启动服务，窗口保持打开。

也可以手动启动：

```bat
python -m app.cli serve
```

启动后终端会显示：

- 本机访问：`http://127.0.0.1:8000`
- 局域网访问：`http://<你的局域网IP>:8000`（服务默认监听 `0.0.0.0`）

同一 Wi-Fi / 局域网内的手机、平板、其他电脑可直接用局域网地址打开。

**停止服务**：在运行服务的窗口按 `Ctrl+C`，或直接关闭该窗口。脚本不会结束其他程序，也不会误杀占用端口的其他进程。

如果端口被别的程序占用，`start.bat` 会提示是哪个 PID 并停下，不会强行再起一个服务。

## 3. 添加企业

```bat
python -m app.cli company-add --name 恒瑞医药 --market A --code 600276 --industry 医药生物
```

- `--market`：`A` / `HK` / `US` / `UNLISTED`
- 上市 A 股填 6 位代码；港股/美股填对应代码；非上市可只填名称

查看已有企业：

```bat
python -m app.cli list
```

## 4. 投递数据（两条路径）

### 4.1 自动获取（推荐）

```bat
python -m app.cli fetch --company 1 --years 5 --types annual,h1,q1,q3 --limit 30 --ingest
```

指定企业后，程序会**同时**做两件事，互不串行等待：

1. **抓取公开数据**：从东方财富免费公开接口读取营收、归母净利润、扣非净利润、毛利率、经营现金流、资本开支等，先写入待确认队列；
2. **下载季报/年报**：从巨潮资讯下载公告 PDF，并按**文件内容哈希**去重，同一报告期只保留一份，不重复占空间。

`--ingest` 表示下载完立即解析入库。公开数据与 PDF 抽取会互相校验，冲突项会留给你确认。

> 公开数据通道目前只支持 A 股（免 token 的公开接口）。港股/美股公告通道存在，但公开财务指标暂未接入；非上市企业请用手工投递。

### 4.2 手工投递

```bat
python -m app.cli ingest --company 1 --file "D:\财报\2025年报.pdf" --file "D:\财报\2025三季报.pdf"
```

支持 PDF / Excel。文件名无法识别报告期时，可显式指定：

```bat
python -m app.cli ingest --company 1 --file "report.pdf" --period "report.pdf=2025年报"
```

## 5. 确认指标

自动抽取和公开数据都会先进入「待确认」状态，避免机器错误直接进入结论。

```bat
python -m app.cli pending --company 1
```

逐条确认、改值或标记争议：

```bat
python -m app.cli confirm --company 1 --high
python -m app.cli override --value-id 123 --value 123456789 --reason "按年报更正"
```

也可以在网页上进入企业 →「数据」或「待确认」，对每个数值点进行确认/改值/标记争议。

确认完成后生成数据快照（报告和估值都会锚定到快照）：

```bat
python -m app.cli snapshot --company 1 --reason new_period
```

`--reason` 可选：`first_build` / `new_period` / `revalue` / `restatement` / `manual`。

## 6. 看报告

生成深度报告：

```bat
python -m app.cli report --company 1
```

网页进入「企业 → 深度报告」即可阅读。报告包含 7 个部分，并显示：

- 本版基于哪个数据快照
- 相比上一版哪些章节「沿用 / 数据刷新 / 结论改写」
- 第七部分（定性结论）需要逐条复核后才能发布

复核并发布：

```bat
python -m app.cli review --version-id 2 --section-id 14 --decision still_valid
python -m app.cli publish --version-id 2
```

### 报告文件在哪里

报告正文**不存数据库**，统一保存为 Markdown：

```
data/reports/<代码_企业名>/<分析报告期>_<快照>_v<版本号>.md
```

例如：

```
data/reports/600276_恒瑞医药/2021年报-2026三季报_S1_v3.md
```

文件名同时包含企业、覆盖期间、数据快照和版本号，旧版本永远不会被覆盖。数据库只保留文件路径、内容哈希和章节元数据；网页会读取 Markdown 并渲染成正常排版（标题、段落、表格、链接），不会显示原始 Markdown 语法。

如需把旧版本库内正文一次性导出为文件：

```bat
python -m app.cli report-export
```

## 7. 跑估值

```bat
python -m app.cli value --company 1 --market-cap 322236000000 --shares 6637000000 --confirm
```

- `--market-cap`：当前总市值（元）
- `--shares`：总股本（股）

工作台会按 DCF 口径计算：经营净现金流减资本开支、近 5 年平均基准、10 年显式期、折现率默认 10%，并给出敏感性矩阵与相对市值的折价/溢价。

估值参数、版本和引用快照都可在网页「估值」页查看；确认后的版本不会被静默改写。

## 8. 手机/局域网访问

服务默认绑定 `0.0.0.0`，启动日志会列出本机所有可用局域网地址。手机与电脑连同一网络后，浏览器访问 `http://电脑IP:8000`。

页面已适配窄屏：导航可横向滑动、卡片自动单列、表格可横向滚动，表格和按钮都可正常点按。

如果手机打不开：

1. 确认手机与电脑在同一 Wi-Fi；
2. 检查 Windows 防火墙是否允许 Python 访问专用网络；
3. 用启动日志里列出的局域网地址，而不是 `127.0.0.1`。

## 9. 目录说明

```
data/
  workbench.db          # 指标、报告/估值版本、复核记录的 SQLite 数据库
  files/                # 原始年报/季报文件（按内容哈希命名，天然去重）
  reports/              # 深度报告 Markdown 正文（按企业分目录）
```

`data/` 是运行时数据，不进入 git。删除 `data/` 等于清空全部本地数据。

## 10. 常用命令速查

| 目的 | 命令 |
| --- | --- |
| 初始化数据库 | `python -m app.cli init-db` |
| 新建企业 | `python -m app.cli company-add --name ... --market A --code ...` |
| 并行取数 | `python -m app.cli fetch --company 1 --types annual,h1,q1,q3 --ingest` |
| 手工投递 | `python -m app.cli ingest --company 1 --file ...` |
| 看待确认 | `python -m app.cli pending --company 1` |
| 生成快照 | `python -m app.cli snapshot --company 1` |
| 生成报告 | `python -m app.cli report --company 1` |
| 导出旧报告 | `python -m app.cli report-export` |
| 估值 | `python -m app.cli value --company 1 --market-cap ... --shares ...` |
| 启动网页 | `python -m app.cli serve`（或双击 `start.bat`） |

## 11. 边界与限制

- 自动取数依赖巨潮资讯、东方财富等公开接口；接口变更或限流时会给出明确提示，可改用手工投递。
- 公开数据指标先进入待确认状态，不会直接覆盖已确认/已锁定数值。
- 金融业公司默认不适用该 DCF 口径；净现金项缺失时不输出股权价值。
- 本工具只做数据整理与计算辅助，不构成投资建议。
