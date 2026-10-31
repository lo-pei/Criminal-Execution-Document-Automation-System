"""离线守门测试。

「全程离线」是本项目的硬约束，但它无法靠运行测试证明——漏掉的网络调用
只在断网机器上才报错，而目标机恰好就是断网机器，出了问题也拿不到堆栈。

因此改为在源码层面断言：运行端代码不得 import 任何网络能力。
这是**静态**检查，代价接近零，且能挡住「顺手加个 requests 查版本」这类改动。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "cedas"

# 运行端不得出现的模块。含标准库的底层网络设施，而不只是高层 HTTP 客户端——
# 只要能建 socket，就有绕过检查的可能。
FORBIDDEN_MODULES = {
    "socket",
    "ssl",
    "urllib",
    "urllib2",
    "urllib3",
    "http",
    "httplib",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "telnetlib",
    "xmlrpc",
    "requests",
    "httpx",
    "aiohttp",
    "websocket",
    "websockets",
    "webbrowser",
    "asyncio",
}

# 允许的例外。空集合是有意的：当前没有任何一处需要对网络的合理需求。
# 若确需加入，必须同时修改 ADR 并在 docs/07 说明为什么该能力在断网时仍可用。
ALLOWED: set[tuple[str, str]] = set()


def _iter_modules() -> list[Path]:
    return sorted(p for p in SRC_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_names(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, node.lineno))
    return found


def test_no_network_imports() -> None:
    violations: list[str] = []
    for path in _iter_modules():
        for module, lineno in _imported_names(path):
            root = module.split(".")[0]
            if root not in FORBIDDEN_MODULES:
                continue
            if (str(path.relative_to(SRC_DIR)), module) in ALLOWED:
                continue
            rel = path.relative_to(SRC_DIR.parent.parent)
            violations.append(f"{rel}:{lineno} import {module}")

    assert not violations, (
        "运行端代码出现网络相关导入，违反「全程离线」约束（ADR-001）：\n  "
        + "\n  ".join(violations)
    )


def test_source_tree_is_scanned() -> None:
    """确认扫描确实扫到了文件。

    ast 遍历写错、目录改名都会让上一个测试「零违规通过」——那是最危险的假绿。
    """
    files = _iter_modules()
    assert len(files) >= 10, f"只扫到 {len(files)} 个文件，路径可能不对：{SRC_DIR}"
    assert any("config" in p.name for p in files)


class TestPathModel:
    """只读资源与可写数据必须分离（ADR-011）。

    合在一起的后果是打包后台账与生成文书落入 `sys._MEIPASS`，退出即丢。
    见 docs/journal/2026-10-31-离线交付与打包.md §2。
    """

    def test_app_home_is_writable_location(self) -> None:
        from cedas import config

        # 开发环境下二者相同（都是项目根），故只断言「不与 _MEIPASS 绑定」。
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            assert Path(meipass) != config.APP_HOME, (
                "APP_HOME 不应指向 PyInstaller 解包目录，否则数据会在退出后丢失"
            )
        assert config.APP_HOME.is_absolute()

    def test_data_paths_live_under_app_home(self) -> None:
        from cedas import config

        for path in (config.DATA_DIR, config.DB_PATH, config.OUTPUT_DIR):
            assert str(path).startswith(str(config.APP_HOME)), (
                f"{path} 不在 APP_HOME 之下，可能被写进只读区"
            )

    def test_resources_live_under_bundle(self) -> None:
        from cedas import config

        assert str(config.RULES_DIR).startswith(str(config.BUNDLE_DIR))

    def test_rule_search_dirs_order(self) -> None:
        """基线在前、补丁在后——后者覆盖前者。顺序反了会让现场补丁失效。"""
        from cedas import config

        dirs = config.rule_search_dirs()
        assert dirs, "搜索路径不能为空"
        assert dirs[0] == config.RULES_DIR, "基线必须是低优先级（排在前面）"
        assert len(dirs) == len(set(dirs)), "搜索路径不得重复"

    def test_home_env_var_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CEDAS_HOME 必须能改写数据根，这是现场把数据放到指定盘符的唯一手段。"""
        from cedas import config

        monkeypatch.setenv(config.HOME_ENV_VAR, str(tmp_path))
        assert config._locate_app_home() == tmp_path.resolve()


def _rule(rule_id: str, pattern: str) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "field": "case_no",
        "layer": "head",
        "pattern": pattern,
        "version": 1,
        "evidence": "测试用",
        "updated_at": "2026-10-31",
    }


class TestRuleOverride:
    """规则按文件名两层覆盖（ADR-011）。

    这条链路是内网升级规则的唯一通道：现场放一个同名文件即替换基线。
    若覆盖失效，表现为「明明换了规则但结果没变」，且不会有任何报错。
    """

    def test_same_filename_replaces_baseline(self, tmp_path: Path) -> None:
        import json

        from cedas.extract.rule_loader import load_rules_from

        base = tmp_path / "base"
        patch = tmp_path / "patch"
        base.mkdir()
        patch.mkdir()

        (base / "head_case_no.v1.json").write_text(
            json.dumps(_rule("H-CASE-NO", r"旧模式")), encoding="utf-8"
        )
        (patch / "head_case_no.v1.json").write_text(
            json.dumps(_rule("H-CASE-NO", r"新模式")), encoding="utf-8"
        )

        rules = load_rules_from([base, patch])
        assert len(rules) == 1, "同名文件应整体替换，而不是叠加成两条"
        assert rules[0].pattern == "新模式", "补丁目录（后出现）必须覆盖基线"

    def test_patch_only_adds_without_touching_baseline(self, tmp_path: Path) -> None:
        import json

        from cedas.extract.rule_loader import load_rules_from

        base = tmp_path / "base"
        patch = tmp_path / "patch"
        base.mkdir()
        patch.mkdir()

        (base / "a.json").write_text(json.dumps(_rule("A", "a")), encoding="utf-8")
        (patch / "b.json").write_text(json.dumps(_rule("B", "b")), encoding="utf-8")

        assert {r.rule_id for r in load_rules_from([base, patch])} == {"A", "B"}

    def test_missing_patch_dir_is_skipped(self, tmp_path: Path) -> None:
        """现场可能只放基线、不放补丁，不得因此报错。"""
        import json

        from cedas.extract.rule_loader import load_rules_from

        base = tmp_path / "base"
        base.mkdir()
        (base / "a.json").write_text(json.dumps(_rule("A", "a")), encoding="utf-8")

        assert len(load_rules_from([base, tmp_path / "not-exist"])) == 1

    def test_duplicate_rule_id_across_files_is_error(self, tmp_path: Path) -> None:
        """文件名写错会让「覆盖」变成「新增」，此处必须显式失败而非静默通过。"""
        import json

        from cedas.extract.rule_loader import RuleSchemaError, load_rules_from

        base = tmp_path / "base"
        patch = tmp_path / "patch"
        base.mkdir()
        patch.mkdir()

        (base / "a.json").write_text(json.dumps(_rule("SAME", "a")), encoding="utf-8")
        (patch / "b.json").write_text(json.dumps(_rule("SAME", "b")), encoding="utf-8")

        with pytest.raises(RuleSchemaError, match="重复"):
            load_rules_from([base, patch])
