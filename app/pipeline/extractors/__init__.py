"""抽取器入口：按文件类型分派。"""

from __future__ import annotations

from pathlib import Path

from app.pipeline.extractors.base import ExtractionResult, ExtractedValue
from app.pipeline.extractors.pdf_extractor import extract_pdf
from app.pipeline.extractors.xlsx_extractor import extract_workbook
from app.pipeline.readers import detect_content_type

__all__ = ["ExtractionResult", "ExtractedValue", "extract_file"]


def extract_file(
    path: str | Path, *, declared_period: tuple[str, int] | None = None
) -> ExtractionResult:
    kind = detect_content_type(str(path))
    if kind in ("xlsx", "csv"):
        return extract_workbook(path, declared_period=declared_period)
    if kind == "pdf":
        return extract_pdf(path, declared_period=declared_period)
    result = ExtractionResult(source_kind=kind)
    result.warnings.append(f"不支持的文件类型：{Path(path).suffix or '无扩展名'}")
    return result
