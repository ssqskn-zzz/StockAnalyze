"""深度报告正文的文件化读写（Markdown）。

设计约束（本轮改造）：
- 报告正文不落数据库，统一写到 data/reports/<代码_企业名>/ 下的 .md 文件；
- 文件名包含「企业 + 分析报告期 + 快照 + 版本」，旧版本永不覆盖；
- 数据库只保留 file_path/content_hash 指针与章节元数据。

本模块只做格式转换与文件读写，不查库、不做业务判断，避免与报告服务互相依赖。
"""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
from typing import Any, Iterable

from app import config

PART_HEADING = re.compile(r"^## (Part (\d+)[^\n]*)$", re.MULTILINE)
_NUMERIC = re.compile(r"[-+]?\d[\d,.]*\s*(?:%|亿元|元|天|倍)?")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")


def _safe_name(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", str(text).strip())
    return cleaned.strip("._") or "未命名"


def _cell(text: Any) -> str:
    return str(text if text is not None else "—").replace("|", "\\|").replace("\n", " ")


def blocks_to_markdown(blocks: Iterable[dict[str, Any]]) -> str:
    """把结构化 block 转成 Markdown；用于历史版本兼容与新版本正文生成。"""
    lines: list[str] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "heading":
            lines.append(f"### {block.get('text', '')}")
        elif kind == "paragraph":
            lines.append(str(block.get("text", "")))
        elif kind == "note":
            lines.append(f"> {block.get('text', '')}")
        elif kind == "bullets":
            lines.extend(f"- {item}" for item in block.get("items", []))
        elif kind == "table":
            columns = block.get("columns") or []
            if columns:
                lines.append("| " + " | ".join(_cell(c) for c in columns) + " |")
                lines.append("| " + " | ".join("---" for _ in columns) + " |")
                for row in block.get("rows", []):
                    lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
            caption = block.get("title") or ""
            if block.get("note"):
                caption = f"{caption} · {block['note']}" if caption else str(block["note"])
            if caption:
                lines.append(f"*{caption}*")
        elif kind == "valuation_card":
            company_id = block.get("company_id")
            label = "DCF 估值"
            link = f"/companies/{company_id}/valuation" if company_id else ""
            parts = [f"估值引用：[{label}]({link})" if link else f"估值引用：{label}"]
            if block.get("equity_value") is not None:
                parts.append(f"股权价值 {block['equity_value'] / 1e8:,.2f} 亿元")
            else:
                parts.append("净现金项缺失，未出股权价值")
            if block.get("per_share_value") is not None:
                parts.append(f"每股 {block['per_share_value']:,.2f} 元")
            parts.append(f"引用快照 {block.get('snapshot_code')}")
            lines.append("> " + " ｜ ".join(parts))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def coverage_text(coverage: list[str]) -> str:
    if not coverage:
        return "未覆盖"
    return coverage[0] if len(coverage) == 1 else f"{coverage[0]}-{coverage[-1]}"


def stored_path(path: str | Path) -> str:
    """把磁盘路径转成可随项目目录搬迁的引用。

    优先存「相对项目根」的路径（如 data/reports/600276_恒瑞医药/x.md）；
    数据目录被环境变量指到项目外时，退回「相对数据目录」；两者都不适用才保留绝对路径。
    """
    try:
        resolved = Path(path).resolve()
    except OSError:
        return str(path)
    for base in (config.BASE_DIR, config.DATA_DIR):
        try:
            return resolved.relative_to(Path(base).resolve()).as_posix()
        except ValueError:
            continue
    return str(resolved)


def resolve_path(stored: str | Path | None) -> Path | None:
    """解析库内记录的报告文件路径，兼容旧机器遗留的绝对路径。

    依次尝试：原样路径 → 相对项目根 / 数据目录 / reports 目录 → 旧绝对路径中
    `reports/` 之后的部分 → 按文件名在 reports 目录下兜底查找。
    """
    if not stored:
        return None
    raw = str(stored).strip()
    if not raw:
        return None
    recorded = Path(raw)
    if not recorded.is_absolute() and "\\" in raw:
        recorded = Path(raw.replace("\\", "/"))
    candidates: list[Path] = []
    if recorded.is_absolute():
        candidates.append(recorded)
        parts = recorded.as_posix().split("/")
        if "reports" in parts:
            candidates.append(config.REPORTS_DIR.joinpath(*parts[parts.index("reports") + 1:]))
    else:
        candidates.extend([config.BASE_DIR / recorded, config.DATA_DIR / recorded, config.REPORTS_DIR / recorded])
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    if recorded.name:
        try:
            matches = sorted(config.REPORTS_DIR.rglob(recorded.name))
        except OSError:
            matches = []
        if matches:
            return matches[0]
    return None


def report_directory(company) -> Path:
    code = company["code"] or "NA"
    return config.REPORTS_DIR / f"{_safe_name(code)}_{_safe_name(company['name'])}"


def report_file_path(
    company, *, snapshot_code: str, version_no: int, coverage: list[str],
) -> Path:
    """文件名体现企业、分析报告期范围、快照与版本，便于人工辨识与追溯。"""
    name = f"{coverage_text(coverage)}_{snapshot_code}_v{version_no}.md"
    return report_directory(company) / _safe_name(name)


def build_markdown(
    *,
    company,
    version_no: int,
    snapshot_code: str,
    coverage: list[str],
    parts: list[dict[str, Any]],
    generated_at: str,
    status: str = "pending_review",
    change_summary: str = "",
) -> str:
    title = f"# {company['name']} 深度分析报告"
    meta = (
        f"> 企业：{company['name']}（{company['market']}·{company['code'] or '无代码'}，"
        f"{company['industry'] or '行业未标注'}）  \n"
        f"> 报告版本：v{version_no} ｜ 数据快照：{snapshot_code} ｜ 覆盖期间：{coverage_text(coverage)}  \n"
        f"> 生成时间：{generated_at} ｜ 状态：{status}  \n"
        f"> 正文以本文件为准；估值结论见估值版本，不在正文复制计算过程。"
    )
    body: list[str] = [title, "", meta, ""]
    if change_summary:
        body.extend(["## 变更摘要", "", change_summary, ""])
    for part in parts:
        heading = f"## {part['title']}"
        content = blocks_to_markdown(part.get("blocks", []))
        body.extend([heading, "", content])
    return "\n".join(body).rstrip() + "\n"


def write_report(path: Path, content: str) -> tuple[Path, str]:
    """写文件并返回 (路径, sha256)。已存在同名文件且内容不同则追加短哈希，绝不覆盖旧版本。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_bytes()
        if hashlib.sha256(existing).hexdigest() == hashlib.sha256(content.encode("utf-8")).hexdigest():
            return path, hashlib.sha256(existing).hexdigest()
        path = path.with_name(f"{path.stem}-{hashlib.sha256(content.encode('utf-8')).hexdigest()[:6]}{path.suffix}")
    path.write_text(content, encoding="utf-8")
    return path, hashlib.sha256(content.encode("utf-8")).hexdigest()


def read_markdown(path: str | Path) -> str:
    resolved = resolve_path(path)
    if resolved is None:
        return ""
    try:
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def split_sections(content: str) -> dict[str, str]:
    """按 `## Part N` 切分正文，返回 {part1: 正文 Markdown}。"""
    matches = list(PART_HEADING.finditer(content))
    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections[f"part{match.group(2)}"] = content[start:end].strip()
    return sections


def _inline(text: str) -> str:
    escaped = html.escape(str(text), quote=False)
    escaped = _LINK.sub(r'<a href="\2">\1</a>', escaped)
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITALIC.sub(r"<em>\1</em>", escaped)
    return escaped


def _builtin_markdown(content: str) -> str:
    """渲染本项目生成的 Markdown 子集（标题/段落/引用/列表/表格/链接）。

    不依赖第三方库，避免离线环境把 Markdown 语法原样显示。
    """
    lines = content.splitlines()
    out: list[str] = []
    paragraph: list[str] = []
    bullets: list[str] = []
    quotes: list[str] = []
    table: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_bullets() -> None:
        if bullets:
            out.append("<ul>" + "".join(f"<li>{_inline(item)}</li>" for item in bullets) + "</ul>")
            bullets.clear()

    def flush_quotes() -> None:
        if quotes:
            out.append("<blockquote>" + "".join(f"<p>{_inline(item)}</p>" for item in quotes) + "</blockquote>")
            quotes.clear()

    def flush_table() -> None:
        if not table:
            return
        rows = [row for row in table if row.strip()]
        table.clear()
        if len(rows) < 2:
            return
        def cells(row: str) -> list[str]:
            return [cell.strip().replace("\\|", "|") for cell in row.strip().strip("|").split("|")]
        header = cells(rows[0])
        body_rows = [cells(row) for row in rows[2:]]
        thead = "".join(f"<th>{_inline(cell)}</th>" for cell in header)
        tbody = "".join(
            "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>"
            for row in body_rows
        )
        out.append(
            '<div class="table-wrap flush"><table>'
            f"<thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table></div>"
        )

    def flush_all() -> None:
        flush_paragraph()
        flush_bullets()
        flush_quotes()
        flush_table()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            flush_paragraph(); flush_bullets(); flush_quotes()
            table.append(stripped)
            continue
        flush_table()
        if not stripped:
            flush_all()
        elif stripped.startswith("### "):
            flush_all()
            out.append(f"<h4>{_inline(stripped[4:])}</h4>")
        elif stripped.startswith("## "):
            flush_all()
            out.append(f"<h3>{_inline(stripped[3:])}</h3>")
        elif stripped.startswith("# "):
            flush_all()
            out.append(f"<h2>{_inline(stripped[2:])}</h2>")
        elif stripped.startswith("> "):
            flush_paragraph(); flush_bullets()
            quotes.append(stripped[2:])
        elif stripped.startswith("- "):
            flush_paragraph(); flush_quotes()
            bullets.append(stripped[2:])
        elif stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("**"):
            flush_all()
            out.append(f'<p class="table-note">{_inline(stripped.strip("*"))}</p>')
        else:
            flush_bullets(); flush_quotes()
            paragraph.append(stripped)
    flush_all()
    return "\n".join(out)


def render_markdown(content: str) -> str:
    """Markdown → HTML。调用方负责 |safe 输出。"""
    return _builtin_markdown(content) if content else ""


def signature(content: str) -> str:
    """去掉数字后的正文签名：用于区分「数据刷新」与「结论改写」。"""
    return _NUMERIC.sub("#", content).strip()
