"""渲染层：模板映射与文书生成。"""

from .formatting import format_cn_date, months_to_chinese
from .mapping import Mapping, MappingError, build_context, load, resolve
from .template import (
    RenderReport,
    UnresolvedPlaceholderError,
    lint_placeholders,
    render,
)

__all__ = [
    "Mapping",
    "MappingError",
    "RenderReport",
    "UnresolvedPlaceholderError",
    "build_context",
    "format_cn_date",
    "lint_placeholders",
    "load",
    "months_to_chinese",
    "render",
    "resolve",
]
