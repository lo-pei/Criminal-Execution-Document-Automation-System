"""归档路径。

命名规则本身在 :mod:`cedas.storage.naming`——源文件与输出文书共用同一套，
不在这里另立一份。本模块只负责**路径**：目录层级、长度校验、版本探测。

落盘路径：``{根目录}/{案号}/{被告人}/{案号}_{被告人}_{文书类型}_{日期}_{vN}.docx``
（见 01 §3.8）。

三点必须处理，否则现场会出错：
1. Windows 非法字符 ``\\ / : * ? " < > |``（交给 ``naming.clean``）
2. 同案多人同文书类型的版本冲突
3. 260 字符路径上限
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .naming import (
    CASE_NO_MAX,
    MAX_PATH_LEN,
    MAX_SEGMENT_LEN,
    PathTooLongError,
    canonical_filename,
    sanitize,
)


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
    return canonical_filename(
        case_no, defendant_name, doc_type, when, version=version, suffix=suffix
    )


def archive_dir(root: Path | str, case_no: str, defendant_name: str) -> Path:
    return Path(root) / sanitize(case_no, max_len=CASE_NO_MAX) / sanitize(defendant_name)


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
    "MAX_SEGMENT_LEN",
    "PathTooLongError",
    "archive_dir",
    "archive_path",
    "build_filename",
    "next_version",
    "sanitize",
]
