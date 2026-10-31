"""文书渲染：docxtpl 填充与占位符校验。

**生成链路完全脱离 Office**（不调 win32com、不起 Word/WPS 后台进程），
因此无法调用 Office 做版式校验——程序只保证「值与结构正确」，
版式由模板本身保证（01-需求与可行性评估 §6）。

占位符被 Word 拆成多个 run 是已知失效点（§3.7）。本模块在渲染前先做
占位符清单比对，未解析即阻断，不留「生成成功但占位符还在」的残次品。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import template_search_dirs

PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


class UnresolvedPlaceholderError(RuntimeError):
    """模板中存在无法被上下文解析的占位符。"""


class TemplateNotFoundError(FileNotFoundError):
    """映射表指向的模板文件不在任何搜索目录中。"""


def find_template(
    name: str,
    dirs: Optional[Sequence[Path]] = None,
) -> Path:
    """按文件名在只读基线 + 现场补丁两层目录中查找模板。

    模板是受控材料，**不随仓库分发**（见 ``resources/templates/README.md``），
    现场必须自己放一份。因此找不到时要报清楚「放哪里」，而不是抛 ``FileNotFoundError``
    让用户去猜。
    """
    search = list(dirs) if dirs is not None else template_search_dirs()

    found: Optional[Path] = None
    for directory in search:
        candidate = directory / name
        if candidate.is_file():
            found = candidate

    if found is None:
        available = sorted(
            {path.name for directory in search if directory.is_dir()
             for path in directory.glob("*.docx")}
        )
        raise TemplateNotFoundError(
            f"未找到模板「{name}」。请把 .docx 模板放入以下任一目录："
            f"{'、'.join(str(d) for d in search)}。"
            f"当前可用模板：{'、'.join(available) if available else '（无）'}"
        )
    return found


@dataclass
class RenderReport:
    output: Path
    placeholders: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    unused: list[str] = field(default_factory=list)


def _iter_docx_text(document) -> Sequence[str]:
    texts: list[str] = []
    for paragraph in document.paragraphs:
        texts.append(paragraph.text)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    texts.append(paragraph.text)
    return texts


def lint_placeholders(template_path: Path | str) -> list[str]:
    """列出模板中出现的全部占位符名（去重排序）。

    需要 ``python-docx``。导入放在函数内，避免无 docx 环境下的模块导入失败。
    """
    from docx import Document

    document = Document(str(template_path))
    found: set[str] = set()
    for text in _iter_docx_text(document):
        found.update(match.group(1) for match in PLACEHOLDER_RE.finditer(text))
    return sorted(found)


def render(
    template_path: Path | str,
    context: dict[str, str],
    output_path: Path | str,
    *,
    strict: bool = True,
) -> RenderReport:
    """渲染文书。

    Args:
        strict: 为 ``True`` 时，模板里有上下文未提供的占位符即抛异常。
            **生产路径必须为 True**——残次文书比不生成更危险。

    Raises:
        UnresolvedPlaceholderError: 存在未解析占位符且 ``strict``。
    """
    from docxtpl import DocxTemplate

    template = Path(template_path)
    output = Path(output_path)

    placeholders = lint_placeholders(template)
    unresolved = [name for name in placeholders if name not in context]
    unused = [name for name in context if name not in placeholders]

    if unresolved and strict:
        raise UnresolvedPlaceholderError(
            f"模板 {template.name} 中有 {len(unresolved)} 个占位符无法解析："
            f"{', '.join(unresolved)}。请检查映射表或模板是否被 Word 拆分了占位符。"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    doc = DocxTemplate(str(template))
    doc.render(context)
    doc.save(str(output))

    return RenderReport(
        output=output,
        placeholders=placeholders,
        unresolved=unresolved,
        unused=unused,
    )


__all__ = [
    "PLACEHOLDER_RE",
    "RenderReport",
    "TemplateNotFoundError",
    "UnresolvedPlaceholderError",
    "find_template",
    "lint_placeholders",
    "render",
]
