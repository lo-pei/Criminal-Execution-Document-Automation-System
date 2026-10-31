"""规则加载器。

规则以 JSON 外置，现场可替换（ADR-002）。加载时强校验：
缺 ``evidence`` 的规则一律拒绝——没有依据的规则无法审查，也无法定位失败。
"""

from __future__ import annotations

import json
import re
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
    "rule_versions",
]
