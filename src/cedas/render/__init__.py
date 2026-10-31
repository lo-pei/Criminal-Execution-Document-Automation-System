"""渲染层：模板映射与文书生成。"""

from .formatting import format_cn_date, months_to_chinese
from .mapping import (
    Mapping,
    MappingError,
    MappingNotFoundError,
    build_context,
    find_mapping,
    load,
    resolve,
)
from .template import (
    RenderReport,
    TemplateNotFoundError,
    UnresolvedPlaceholderError,
    find_template,
    lint_placeholders,
    render,
)

__all__ = [
    "Mapping",
    "MappingError",
    "MappingNotFoundError",
    "RenderReport",
    "TemplateNotFoundError",
    "UnresolvedPlaceholderError",
    "build_context",
    "find_mapping",
    "find_template",
    "format_cn_date",
    "lint_placeholders",
    "load",
    "months_to_chinese",
    "render",
    "resolve",
]
