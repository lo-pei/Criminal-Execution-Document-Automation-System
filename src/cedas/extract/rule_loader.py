"""规则加载器。

规则以 JSON 外置，现场可替换（ADR-002）。加载时强校验：
缺 ``evidence`` 的规则一律拒绝——没有依据的规则无法审查，也无法定位失败。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class RuleSchemaError(ValueError):
    """规则文件不符合 schema。不做静默跳过。"""


@dataclass(frozen=True)
class Rule:
    rule_id: str
    field: str
    layer: str
    pattern: str
    version: int
    evidence: str
    updated_at: str
    group: int = 0
    flags: int = 0
    schema: int = SCHEMA_VERSION

    def compile(self) -> re.Pattern[str]:
        return re.compile(self.pattern, self.flags)

    def __post_init__(self) -> None:
        if not self.evidence.strip():
            raise RuleSchemaError(f"规则 {self.rule_id} 缺少 evidence（依据片段）")
        if self.layer not in {"head", "body", "tail"}:
            raise RuleSchemaError(f"规则 {self.rule_id} 的 layer 非法：{self.layer}")
        try:
            re.compile(self.pattern, self.flags)
        except re.error as exc:
            raise RuleSchemaError(f"规则 {self.rule_id} 的正则无法编译：{exc}") from exc


def _from_dict(data: dict[str, Any], source: Path) -> Rule:
    required = ("rule_id", "field", "layer", "pattern", "version", "evidence", "updated_at")
    missing = [key for key in required if key not in data]
    if missing:
        raise RuleSchemaError(f"{source.name} 缺少字段：{', '.join(missing)}")

    schema = int(data.get("schema", SCHEMA_VERSION))
    if schema != SCHEMA_VERSION:
        raise RuleSchemaError(
            f"{source.name} 的 schema 版本为 {schema}，当前程序支持 {SCHEMA_VERSION}。"
            "请升级规则或程序，不得降级运行。"
        )

    return Rule(
        rule_id=str(data["rule_id"]),
        field=str(data["field"]),
        layer=str(data["layer"]),
        pattern=str(data["pattern"]),
        version=int(data["version"]),
        evidence=str(data["evidence"]),
        updated_at=str(data["updated_at"]),
        group=int(data.get("group", 0)),
        flags=int(data.get("flags", 0)),
        schema=schema,
    )


def load_rules(directory: Path | str) -> list[Rule]:
    """加载目录下全部 ``*.json`` 规则，按 ``rule_id`` 排序。"""
    rules_dir = Path(directory)
    if not rules_dir.exists():
        raise FileNotFoundError(f"规则目录不存在：{rules_dir}")

    rules: list[Rule] = []
    for path in sorted(rules_dir.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        rules.append(_from_dict(data, path))

    duplicated = {rule.rule_id for rule in rules if [r.rule_id for r in rules].count(rule.rule_id) > 1}
    if duplicated:
        raise RuleSchemaError(f"rule_id 重复：{', '.join(sorted(duplicated))}")

    return rules


def load_rules_from(directories: Sequence[Path | str]) -> list[Rule]:
    """按目录优先级加载规则，**后出现的目录覆盖前面的同名文件**。

    覆盖粒度是「文件」而非「rule_id」：现场修订某条规则时，把整个规则文件的
    新版本放到覆盖目录即可，不必逐条摘除基线中的旧条目——逐条覆盖会让
    「基线有、补丁删」这种情形无法表达，规则库最终变成无法推理的叠加层。

    不存在的目录静默跳过（现场可能只放补丁、不放完整规则集）。
    """
    merged: dict[str, Path] = {}
    for directory in directories:
        path = Path(directory)
        if not path.is_dir():
            continue
        for file in sorted(path.glob("*.json")):
            merged[file.name] = file  # 后写覆盖前写

    if not merged:
        searched = "、".join(str(Path(d)) for d in directories)
        raise FileNotFoundError(f"未找到任何规则文件，已搜索：{searched}")

    rules: list[Rule] = []
    seen: dict[str, Path] = {}
    for _, path in sorted(merged.items()):
        rule = _from_dict(json.loads(path.read_text(encoding="utf-8")), path)
        if rule.rule_id in seen:
            raise RuleSchemaError(
                f"rule_id {rule.rule_id} 在 {seen[rule.rule_id].name} 与 {path.name} 中重复。"
                "覆盖只能按文件名进行，改 rule_id 请同时调整文件名。"
            )
        seen[rule.rule_id] = path
        rules.append(rule)

    return rules


def by_layer(rules: list[Rule], layer: str) -> list[Rule]:
    return [rule for rule in rules if rule.layer == layer]


def rule_versions(rules: list[Rule]) -> dict[str, int]:
    return {rule.rule_id: rule.version for rule in rules}


__all__ = [
    "SCHEMA_VERSION",
    "Rule",
    "RuleSchemaError",
    "by_layer",
    "load_rules",
    "load_rules_from",
    "rule_versions",
]
