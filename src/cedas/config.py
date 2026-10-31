"""路径与全局配置。

单一来源：所有资源路径都从这里取，禁止在业务代码里拼相对路径。

**只读资源与可写数据必须分开**（见 ADR-010）：

- ``BUNDLE_DIR``：只读资源根（规则 / 模板 / 映射）。打包后指向 PyInstaller 的
  解包目录（onefile 为 ``%TEMP%\\_MEIxxxx``，onedir 6.x 为 ``_internal``）。
  **该目录内容在程序退出后可能被删除，绝不可写入。**
- ``APP_HOME``：可写数据根（台账库 / 生成文书 / 归档）。打包后指向 exe 所在目录，
  可用环境变量 ``CEDAS_HOME`` 覆盖。

早期版本将两者合为 ``ROOT_DIR``，导致打包运行时数据库与生成文书落入临时解包目录、
程序退出即丢失。该行为已由 ADR-010 修正，切勿回退。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

PACKAGE_DIR = Path(__file__).resolve().parent

APP_NAME = "CEDAS"
APP_TITLE = "刑事执行文书自动制作系统"

# 覆盖可写数据根的环境变量（现场把数据放到指定盘符时使用）
HOME_ENV_VAR = "CEDAS_HOME"

# 核心字段：缺失即阻断该份文书的生成（见 docs/06 §3）
REQUIRED_FIELDS = (
    "case_no",
    "court_name",
    "defendant.name",
    "defendant.gender",
    "defendant.birth_date",
    "defendant.id_number",
    "defendant.crimes",
)

# 支持的判决书扩展名
SUPPORTED_EXTENSIONS = (".pdf", ".txt")


def is_frozen() -> bool:
    """是否运行在 PyInstaller 产物中。"""
    return bool(getattr(sys, "frozen", False))


def _locate_bundle_dir() -> Path:
    """定位只读资源根目录。

    打包后取 ``sys._MEIPASS``；开发态向上寻找含 ``resources/`` 的目录；
    都找不到时退回包目录（此时资源缺失，由调用方报错）。
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle)

    for candidate in (PACKAGE_DIR, *PACKAGE_DIR.parents):
        if (candidate / "resources").is_dir():
            return candidate
    return PACKAGE_DIR


def _locate_app_home() -> Path:
    """定位可写数据根目录。

    优先级：``CEDAS_HOME`` 环境变量 → exe 所在目录（打包态）→ 项目根（开发态）。
    不写 ``%LOCALAPPDATA%``：法院要求归档文件落在固定盘符内，可被同事找到与备份。
    """
    override = os.environ.get(HOME_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()

    if is_frozen():
        return Path(sys.executable).resolve().parent

    # 开发态：与只读资源根一致，即项目根
    return _locate_bundle_dir()


BUNDLE_DIR = _locate_bundle_dir()
APP_HOME = _locate_app_home()

RESOURCES_DIR = BUNDLE_DIR / "resources"
RULES_DIR = RESOURCES_DIR / "rules"
TEMPLATES_DIR = RESOURCES_DIR / "templates"
MAPPINGS_DIR = RESOURCES_DIR / "mappings"
SCHEMA_DIR = RESOURCES_DIR / "schema"

DATA_DIR = APP_HOME / "data"
SAMPLES_DIR = DATA_DIR / "samples"
TRUTH_DIR = DATA_DIR / "truth"
OUTPUT_DIR = DATA_DIR / "output"
DB_PATH = DATA_DIR / "cedas.db"

# 兼容旧名：指向只读资源根，仅用于读取
ROOT_DIR = BUNDLE_DIR


def ensure_data_dirs(base: Optional[Path] = None) -> Path:
    """确保运行期可写目录存在。返回数据根目录。

    在打包产地上首次运行时，数据目录并不存在，必须在打开数据库前调用。
    """
    root = base or DATA_DIR
    for sub in ("samples", "truth", "output"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def rule_search_dirs() -> list[Path]:
    """规则搜索路径，**低优先级在前**。

    内网无法在线更新程序，规则修订必须能在不重新打包的前提下送达现场
    （ADR-002 的落地方式）：

    1. ``<只读>/resources/rules``：随包分发的基线规则。
    2. ``<可写>/resources/rules``：现场覆盖目录。同名文件整体替换基线规则。

    之所以不直接改 ``_internal/resources``：那个目录属于安装产物，
    用户手工改动既无记录也易被下一次覆盖抹掉；而 exe 旁的 ``resources/``
    是显式的「本地补丁区」，备份与回滚都直观。

    开发态下 ``APP_HOME`` 与 ``BUNDLE_DIR`` 是同一个目录，两路会指向同一处，
    故此处去重——否则加载器会把同一批文件读两遍，且「两层覆盖」的语义含糊。
    """
    candidates = [RULES_DIR, APP_HOME / "resources" / "rules"]
    ordered: list[Path] = []
    for path in candidates:
        if path not in ordered:
            ordered.append(path)
    return ordered


def describe_paths() -> dict[str, str]:
    """返回路径诊断信息。

    内网现场无法远程排查，排障第一句话永远是「你的程序装在哪、数据落在哪」，
    故提供此函数供 ``cedas doctor`` 直接打印。
    """
    return {
        "frozen": str(is_frozen()),
        "executable": sys.executable,
        "bundle_dir(只读)": str(BUNDLE_DIR),
        "app_home(可写)": str(APP_HOME),
        "resources(只读)": str(RESOURCES_DIR),
        "data(可写)": str(DATA_DIR),
        "db": str(DB_PATH),
        "output": str(OUTPUT_DIR),
        "home_env_var": f"{HOME_ENV_VAR}={os.environ.get(HOME_ENV_VAR, '')}",
    }


__all__ = [
    "APP_HOME",
    "APP_NAME",
    "APP_TITLE",
    "BUNDLE_DIR",
    "DATA_DIR",
    "DB_PATH",
    "HOME_ENV_VAR",
    "MAPPINGS_DIR",
    "OUTPUT_DIR",
    "PACKAGE_DIR",
    "REQUIRED_FIELDS",
    "RESOURCES_DIR",
    "ROOT_DIR",
    "RULES_DIR",
    "SAMPLES_DIR",
    "SCHEMA_DIR",
    "SUPPORTED_EXTENSIONS",
    "TEMPLATES_DIR",
    "TRUTH_DIR",
    "describe_paths",
    "ensure_data_dirs",
    "is_frozen",
    "rule_search_dirs",
]
