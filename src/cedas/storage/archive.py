"""归档命名。

命名规则：``{案号}_{被告人}_{文书类型}_{日期}.docx``，落盘路径
``{根目录}/{案号}/{被告人}/``（见 01 §3.8）。

三点必须处理，否则现场会出错：
1. Windows 非法字符 ``\\ / : * ? " < > |``
2. 同案多人同文书类型的版本冲突
3. 260 字符路径上限
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_SPACE_RE = re.compile(r"\s+")

# Windows 保留名（不区分大小写、带扩展名也保留）
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}

MAX_SEGMENT_LEN = 60
MAX_PATH_LEN = 250


class PathTooLongError(ValueError):
    """路径超过 Windows 限制。应缩短目录层级或案号，不得静默截断。"""


def sanitize(segment: str, *, max_len: int = MAX_SEGMENT_LEN) -> str:
    """清理单个路径片段。

    >>> sanitize('京0105刑初123号')
    '京0105刑初123号'
    >>> sanitize('盗窃/抢夺')
    '盗窃-抢夺'
    """
    text = _ILLEGAL_RE.sub("-", segment or "")
    text = _SPACE_RE.sub("", text).strip(" .-")
    if len(text) > max_len:
        text = text[:max_len].rstrip(" .-")
    if text.upper() in _RESERVED:
        text = f"_{text}"
    return text or "未命名"


def build_filename(
    case_no: str,
    defendant_name: str,
    doc_type: str,
    when: date,
    *,
    version: int = 1,
    suffix: str = ".docx",
) -> str:
    """生成规范文件名。

    >>> build_filename('（2023）京0105刑初123号', '张三', '执行通知书', date(2023, 5, 1))
    '（2023）京0105刑初123号_张三_执行通知书_20230501.docx'
    """
    parts = [
        sanitize(case_no),
        sanitize(defendant_name),
        sanitize(doc_type),
        when.strftime("%Y%m%d"),
    ]
    if version > 1:
        parts.insert(3, f"v{version}")
    return "_".join(parts) + suffix


def archive_dir(root: Path | str, case_no: str, defendant_name: str) -> Path:
    return Path(root) / sanitize(case_no, max_len=40) / sanitize(defendant_name)


def archive_path(
    root: Path | str,
    case_no: str,
    defendant_name: str,
    doc_type: str,
    when: date,
    *,
    version: int = 1,
) -> Path:
    """计算归档绝对路径，并校验长度。"""
    path = archive_dir(root, case_no, defendant_name) / build_filename(
        case_no, defendant_name, doc_type, when, version=version
    )
    if len(str(path)) > MAX_PATH_LEN:
        raise PathTooLongError(
            f"归档路径 {len(str(path))} 字符，超过 {MAX_PATH_LEN} 限制：{path}"
        )
    return path


def next_version(root: Path | str, case_no: str, defendant_name: str, doc_type: str, when: date) -> int:
    """探测下一个可用版本号，避免覆盖既有文书。"""
    directory = archive_dir(root, case_no, defendant_name)
    version = 1
    while True:
        candidate = directory / build_filename(case_no, defendant_name, doc_type, when, version=version)
        if not candidate.exists():
            return version
        version += 1


__all__ = [
    "MAX_PATH_LEN",
    "PathTooLongError",
    "archive_dir",
    "archive_path",
    "build_filename",
    "next_version",
    "sanitize",
]
