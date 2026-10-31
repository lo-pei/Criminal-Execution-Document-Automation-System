"""文书命名规范：名字由内容决定，不由人来起。

法院收到的判决书 PDF 常常叫「扫描件001.pdf」「微信图片_20230510103045.pdf」，
出文之后归档目录里又会堆出「执行通知书.docx」「执行通知书(1).docx」。
两种混乱同一个根因：**文件名不承载检索所需的字段**，只能靠人记或翻内容。

本模块定义一套名字，源文件与输出文书共用：

    案号_当事人_文书类型_日期[_vN].扩展名

例：

    （2023）京0105刑初123号_张三等3人_刑事判决书_20230501.pdf
    （2023）京0105刑初123号_张三_执行通知书_20230501.docx

四条规则及其理由：

1. **案号打头。** 法院找卷宗先报案号；文件名以案号开头，按名称排序就是按卷宗排序。
2. **当事人写「第一被告人姓名 + 等N人」。** 人数比名单更有信息量——一眼看出这案
   要不要分开出文；把全名写进去会顶掉案号，反而更不好找。
3. **缺项留空跳过，不写「未识别」占位。** 占位符会被检索当成关键词，也会让人误以为
   名字已定稿；缺什么由 :attr:`NamePlan.missing` 单独报出来，界面照实说。
4. **版本号放末尾。** 夹在类型与日期中间（``v2_执行通知书``）会让同案同人的不同版本
   排不到一起，而「找最新一版」恰恰是复查时最常做的事。

**改名是破坏性操作。** 本模块只产出方案（:func:`plan_rename`），落盘由
:func:`apply_rename` 执行；调用方必须先让人看过「原名 → 新名」再动手。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

SEPARATOR = "_"

# 单个路径片段上限。Windows 单个文件名限 255 字节，中文占 3 字节，
# 60 字已是 180 字节，再长就接近上限了。
MAX_SEGMENT_LEN = 60

# 整条路径上限。Windows 默认 260 字符，留 10 个字符给 ``\\?\`` 之外的重定向余量。
MAX_PATH_LEN = 250

# 各片段各自的长度预算。合计约 90 字，远低于 MAX_SEGMENT_LEN，
# 为的是给目录层级留出空间；真实案号最长约 22 字，40 字足够。
CASE_NO_MAX = 40
PARTY_MAX = 20
DOC_TYPE_MAX = 20

_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_SPACE_RE = re.compile(r"\s+")

# Windows 保留名（不区分大小写、带扩展名也保留）
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}


class PathTooLongError(ValueError):
    """路径超过 Windows 限制。应缩短目录层级或案号，不得静默截断。"""


class RenameError(Exception):
    """改名失败。消息直接显示给用户，写成人话。"""


# ---------------------------------------------------------------------------
# 片段清理
# ---------------------------------------------------------------------------


def clean(text: object, *, max_len: int = MAX_SEGMENT_LEN) -> str:
    """清理一个可空的名字片段：空输入返回空串，不补占位。

    >>> clean('盗窃/抢夺')
    '盗窃-抢夺'
    >>> clean('  ')
    ''
    """
    body = _ILLEGAL_RE.sub("-", str(text if text is not None else ""))
    body = _SPACE_RE.sub("", body).strip(" .-")
    if len(body) > max_len:
        body = body[:max_len].rstrip(" .-")
    return body


def sanitize(segment: str, *, max_len: int = MAX_SEGMENT_LEN) -> str:
    """清理**必须存在**的路径片段：空值回落为「未命名」。

    目录名不能为空，所以这里保留占位；文件名各段走 :func:`clean`，
    缺项直接不写，语义不同。

    >>> sanitize('京0105刑初123号')
    '京0105刑初123号'
    >>> sanitize('盗窃/抢夺')
    '盗窃-抢夺'
    >>> sanitize('   ')
    '未命名'
    """
    text = clean(segment, max_len=max_len)
    if text.upper() in _RESERVED:
        text = f"_{text}"
    return text or "未命名"


def party_label(names: Sequence[str]) -> str:
    """当事人标签：一人写姓名，多人写「张三等3人」。空名单返回空串。

    >>> party_label(['张三'])
    '张三'
    >>> party_label(['张三', '李四', '王五'])
    '张三等3人'
    """
    cleaned = [clean(name, max_len=PARTY_MAX) for name in names if str(name or "").strip()]
    cleaned = [name for name in cleaned if name]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return f"{cleaned[0]}等{len(cleaned)}人"


# ---------------------------------------------------------------------------
# 组装文件名
# ---------------------------------------------------------------------------


def canonical_stem(
    case_no: str,
    party: str,
    doc_type: str,
    when: Optional[date],
    *,
    version: int = 1,
) -> str:
    """不带扩展名的规范名称。缺项自动跳过。

    >>> canonical_stem('（2023）京0105刑初123号', '张三', '执行通知书', date(2023, 5, 1))
    '（2023）京0105刑初123号_张三_执行通知书_20230501'
    """
    parts = [
        clean(case_no, max_len=CASE_NO_MAX),
        clean(party, max_len=PARTY_MAX),
        clean(doc_type, max_len=DOC_TYPE_MAX),
        when.strftime("%Y%m%d") if when is not None else "",
    ]
    stem = SEPARATOR.join(part for part in parts if part) or "未命名"
    if version > 1:
        stem = f"{stem}{SEPARATOR}v{version}"
    return stem


def canonical_filename(
    case_no: str,
    party: str,
    doc_type: str,
    when: Optional[date],
    *,
    version: int = 1,
    suffix: str = ".docx",
) -> str:
    """带扩展名的规范文件名。

    >>> canonical_filename('（2023）京0105刑初123号', '张三', '执行通知书', date(2023, 5, 1))
    '（2023）京0105刑初123号_张三_执行通知书_20230501.docx'
    """
    if suffix and not suffix.startswith("."):
        suffix = f".{suffix}"
    return canonical_stem(case_no, party, doc_type, when, version=version) + suffix


# ---------------------------------------------------------------------------
# 源文件改名
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NamePlan:
    """一份源文件的命名方案。**只是方案**，落盘要再调 :func:`apply_rename`。"""

    source: Path
    target: Optional[Path] = None
    missing: tuple[str, ...] = ()
    reason: str = ""
    # 目标位置已有一份**内容相同**的文件。此时不改名：同一份判决书留两份，
    # 日后复查会分不清哪份是底稿。
    duplicate: bool = False

    @property
    def ok(self) -> bool:
        """名字是否定得下来。案号缺失时为 ``False``。"""
        return self.target is not None

    @property
    def suggested(self) -> str:
        """建议的文件名；定不下来时为空串。"""
        return self.target.name if self.target is not None else ""

    @property
    def needs_rename(self) -> bool:
        """当前文件名与规范名是否不一致，且值得改。"""
        return (
            self.target is not None
            and not self.duplicate
            and self.target.name != self.source.name
        )


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_content(source: Path, target: Path) -> bool:
    """两份文件内容是否相同。

    用哈希而不是大小：大小相同内容不同的情况在法院素材里真实存在
    （同一天扫的两份文书页数一样），据此判定「已改过名」会漏掉一次改名。
    """
    try:
        if source.stat().st_size != target.stat().st_size:
            return False
        return _digest(source) == _digest(target)
    except OSError:  # pragma: no cover - 读不了就当不同名处理，走加版本分支
        return False


def plan_rename(
    path: Path | str,
    *,
    case_no: str = "",
    party: str = "",
    doc_type: str = "",
    when: Optional[date] = None,
    suffix: Optional[str] = None,
    version: int = 1,
) -> NamePlan:
    """给出源文件的规范名方案。**不碰文件**。

    Args:
        path: 源文件路径。改名只在原目录内进行，不跨盘搬动。
        case_no: 从正文抽到的案号。缺失时直接返回不可改名的方案——
            案号是检索主键，宁可暂时不改，也不要先改出一个日后还要再改的名。
        party: 当事人标签（已由 :func:`party_label` 归并）。
        doc_type: 文书类型，如「刑事判决书」。
        when: 文书日期（源文件取判决日期，输出取落款日期）。
        suffix: 扩展名，默认沿用原文件；原文件没有扩展名时用 ``.pdf``。
        version: 版本号，撞名时由本函数自增。

    Raises:
        PathTooLongError: 拼出的路径超过 Windows 上限。
    """
    source = Path(path)
    missing: list[str] = []
    if not clean(case_no):
        missing.append("案号")
    if not clean(party):
        missing.append("当事人")
    if not clean(doc_type):
        missing.append("文书类型")
    if when is None:
        missing.append("判决日期")

    if not clean(case_no):
        return NamePlan(
            source=source,
            target=None,
            missing=tuple(missing),
            reason="案号没认出来，先在界面上补录案号，再改文件名。",
        )

    extension = suffix if suffix is not None else (source.suffix or ".pdf")
    number = max(version, 1)
    while True:
        name = canonical_filename(case_no, party, doc_type, when, version=number, suffix=extension)
        target = source.with_name(name)
        if len(str(target)) > MAX_PATH_LEN:
            raise PathTooLongError(
                f"改名后路径 {len(str(target))} 字符，超过 {MAX_PATH_LEN} 限制：{target}"
            )
        if target.name == source.name:
            return NamePlan(source=source, target=target, missing=tuple(missing))
        if not target.exists():
            return NamePlan(source=source, target=target, missing=tuple(missing))
        if _same_content(source, target):
            return NamePlan(
                source=source,
                target=target,
                missing=tuple(missing),
                reason=f"这个文件夹里已有一份内容相同的文件：{target.name}",
                duplicate=True,
            )
        number += 1
        if number > 99:  # pragma: no cover - 同一份判决书同名 99 次不现实
            return NamePlan(
                source=source,
                target=None,
                missing=tuple(missing),
                reason="同名文件太多，请先整理这个文件夹。",
            )


def apply_rename(plan: NamePlan) -> Path:
    """执行改名，返回改名后的路径。方案不可用或失败时抛 :class:`RenameError`。"""
    if plan.target is None:
        raise RenameError(plan.reason or "这个名字还定不下来。")
    if plan.duplicate:
        raise RenameError(plan.reason)

    source, target = plan.source, plan.target
    if target.name == source.name:
        return source
    if not source.exists():
        raise RenameError(f"找不到原文件：{source.name}")
    if target.exists():
        raise RenameError(f"这个文件夹里已经有同名文件：{target.name}")

    try:
        source.rename(target)
    except PermissionError as error:
        raise RenameError(
            "这份文件正被其他程序打开（例如 PDF 阅读器、资源管理器预览），"
            "请先关闭，再重试改名。"
        ) from error
    except OSError as error:
        raise RenameError(f"改名没成功：{error}") from error
    return target


__all__ = [
    "CASE_NO_MAX",
    "DOC_TYPE_MAX",
    "MAX_PATH_LEN",
    "MAX_SEGMENT_LEN",
    "PARTY_MAX",
    "SEPARATOR",
    "NamePlan",
    "PathTooLongError",
    "RenameError",
    "apply_rename",
    "canonical_filename",
    "canonical_stem",
    "clean",
    "party_label",
    "plan_rename",
    "sanitize",
]
