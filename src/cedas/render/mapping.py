"""模板映射：文书类型 → 占位符 → 字段路径。

不同执行文书所需字段集合不同，映射表按文书类型独立配置（spec §2.3）。
映射表与规则库一样外置为 JSON，现场可增补。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import mapping_search_dirs
from ..domain.models import Case, Defendant, FieldValue
from .formatting import format_cn_date, months_to_chinese


class MappingError(ValueError):
    """映射表不合法。"""


class MappingNotFoundError(LookupError):
    """按文书类型找不到映射表。"""


@dataclass
class Mapping:
    document_type: str
    template: str
    version: int = 1
    fields: dict[str, str] = field(default_factory=dict)
    notes: str = ""
    source: Optional[Path] = None


def find_mapping(
    doc_type: str,
    dirs: Optional[Sequence[Path]] = None,
) -> Path:
    """按文书类型定位映射表。

    文件名约定 ``{文书类型}.json``，允许带用途后缀（``执行通知书.example.json``）。
    搜索顺序与规则库同构：低优先级在前，**同名时后者覆盖前者**，现场因此可以
    只替换「执行通知书.json」而无需改动随包分发的基线。
    """
    search = list(dirs) if dirs is not None else mapping_search_dirs()

    found: Optional[Path] = None
    for directory in search:
        if not directory.is_dir():
            continue
        exact = directory / f"{doc_type}.json"
        if exact.is_file():
            found = exact
            continue
        candidates = sorted(directory.glob(f"{doc_type}.*.json"))
        if candidates:
            found = candidates[0]

    if found is None:
        available = sorted(
            {path.name.split(".")[0] for directory in search if directory.is_dir()
             for path in directory.glob("*.json")}
        )
        raise MappingNotFoundError(
            f"未找到「{doc_type}」的映射表，查找目录：{'、'.join(str(d) for d in search)}。"
            f"现有文书类型：{'、'.join(available) if available else '（无）'}"
        )
    return found


def load(path: Path | str) -> Mapping:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("document_type", "template", "fields"):
        if key not in data:
            raise MappingError(f"{Path(path).name} 缺少字段：{key}")
    if not isinstance(data["fields"], dict) or not data["fields"]:
        raise MappingError(f"{Path(path).name} 的 fields 必须是非空对象")

    return Mapping(
        document_type=str(data["document_type"]),
        template=str(data["template"]),
        version=int(data.get("version", 1)),
        fields={str(k): str(v) for k, v in data["fields"].items()},
        notes=str(data.get("notes", "")),
        source=Path(path),
    )


def _unwrap(value: Any) -> Any:
    return value.value if isinstance(value, FieldValue) else value


def resolve(context: dict[str, Any], path: str) -> Any:
    """按点号路径取值，自动解包 ``FieldValue``。

    ``"defendant.name"`` → ``context["defendant"].name`` 的值
    """
    current: Any = context
    for part in path.split("."):
        current = _unwrap(current)
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, (list, tuple)):
            if not part.isdigit():
                return None
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            current = getattr(current, part, None)
    return _unwrap(current)


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return format_cn_date(value)
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


# 派生字段：模板里需要「三年六个月」而不是「42」
_DERIVED: dict[str, Callable[[Case, Defendant], Any]] = {
    "defendant.merged_term_cn": lambda case, d: months_to_chinese(_unwrap(d.merged_months)),
    "defendant.span_start": lambda case, d: d.span.start,
    "defendant.span_end": lambda case, d: d.span.end,
    "defendant.release_date": lambda case, d: d.span.release_date,
    "case.effective_date": lambda case, d: _unwrap(case.effective_date),
}


def build_context(case: Case, defendant: Defendant, mapping: Mapping) -> dict[str, str]:
    """组装渲染上下文。缺失值一律填空串，避免模板出现 ``None``。"""
    base: dict[str, Any] = {"case": case, "defendant": defendant}
    context: dict[str, str] = {}

    for placeholder, path in mapping.fields.items():
        value = _DERIVED[path](case, defendant) if path in _DERIVED else resolve(base, path)
        context[placeholder] = _to_text(value)

    return context


__all__ = [
    "Mapping",
    "MappingError",
    "MappingNotFoundError",
    "build_context",
    "find_mapping",
    "load",
    "resolve",
]
