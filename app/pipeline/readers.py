"""材料读取：识别文件类型、计算哈希、读取 xlsx / csv / pdf。"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import pdfplumber


@dataclass
class Sheet:
    name: str
    rows: list[list[object]] = field(default_factory=list)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_content_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        return "xlsx"
    if ext == ".csv":
        return "csv"
    if ext == ".pdf":
        return "pdf"
    if ext in (".txt", ".md"):
        return "text"
    return "other"


def read_xlsx(path: str | Path) -> list[Sheet]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheets = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        sheets.append(Sheet(name=ws.title, rows=rows))
    wb.close()
    return sheets


def read_csv(path: str | Path) -> list[Sheet]:
    for encoding in ("utf-8-sig", "gbk", "utf-8"):
        try:
            with open(path, "r", encoding=encoding, newline="") as fh:
                rows = [row for row in csv.reader(fh)]
            return [Sheet(name=Path(path).stem, rows=rows)]
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV 编码无法识别（尝试 utf-8/gbk 失败）")


def read_pdf(path: str | Path) -> list[str]:
    """按页返回文本；扫描件（无文本层）返回空字符串页。"""
    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return pages


def read_text(path: str | Path) -> list[str]:
    return [Path(path).read_text(encoding="utf-8", errors="ignore")]
