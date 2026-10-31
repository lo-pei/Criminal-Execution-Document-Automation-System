"""PDF 通道（唯一生产通道）。

用 PyMuPDF 的 ``page.get_text("dict")`` 按文本块提取，保留页码、字号与 bbox，
供页眉页脚判定与标题识别使用。

不使用 OCR：输入为带文本层 PDF，引入视觉模型只会增加体积与失败面。
"""

from __future__ import annotations

from pathlib import Path

from .base import RawBlock


class PdfUnavailableError(RuntimeError):
    """PyMuPDF 未安装或文件无法解析。"""


def _import_pymupdf():
    try:
        import pymupdf  # type: ignore
    except ImportError:  # 兼容旧版本包名
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise PdfUnavailableError(
                "未安装 PyMuPDF，无法处理 PDF。请执行 pip install pymupdf。"
            ) from exc
    return pymupdf


def read(path: Path | str) -> list[RawBlock]:
    """提取 PDF 文本层，按文本块返回。

    每块内部的换行以 ``\\n`` 保留，由清洗层负责拼接——此处不做任何合并，
    否则会丢失软换行信号。
    """
    pymupdf = _import_pymupdf()
    file_path = Path(path)

    try:
        document = pymupdf.open(str(file_path))
    except Exception as exc:
        raise PdfUnavailableError(f"无法打开 PDF：{file_path.name}（{exc}）") from exc

    blocks: list[RawBlock] = []
    try:
        for page_number, page in enumerate(document, start=1):
            page_dict = page.get_text("dict")
            page_height = float(page.rect.height)
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:  # 0=文本，1=图片
                    continue

                pieces: list[str] = []
                sizes: list[float] = []
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        pieces.append(span.get("text", ""))
                        size = span.get("size")
                        if size:
                            sizes.append(float(size))
                    pieces.append("\n")

                text = "".join(pieces).strip()
                if not text:
                    continue

                bbox = block.get("bbox")
                blocks.append(
                    RawBlock(
                        text=text,
                        page=page_number,
                        font_size=max(sizes) if sizes else None,
                        bbox=tuple(bbox) if bbox else None,
                        page_height=page_height,
                    )
                )
    finally:
        document.close()

    return blocks


__all__ = ["PdfUnavailableError", "read"]
