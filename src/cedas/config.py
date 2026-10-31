"""路径与全局配置。

单一来源：所有资源路径都从这里取，禁止在业务代码里拼相对路径。
打包后（PyInstaller）资源位于 ``sys._MEIPASS``，本模块已处理该分支。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

PACKAGE_DIR = Path(__file__).resolve().parent

APP_NAME = "CEDAS"
APP_TITLE = "刑事执行文书自动制作系统"

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


def _locate_root() -> Path:
    """定位资源根目录。

    优先取打包目录（``sys._MEIPASS``），其次向上找含 ``resources/`` 的目录，
    最后退回包目录本身（此时资源缺失，由调用方报错）。
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle)

    for candidate in (PACKAGE_DIR, *PACKAGE_DIR.parents):
        if (candidate / "resources").is_dir():
            return candidate
    return PACKAGE_DIR


ROOT_DIR = _locate_root()
RESOURCES_DIR = ROOT_DIR / "resources"
RULES_DIR = RESOURCES_DIR / "rules"
TEMPLATES_DIR = RESOURCES_DIR / "templates"
MAPPINGS_DIR = RESOURCES_DIR / "mappings"
SCHEMA_DIR = RESOURCES_DIR / "schema"

DATA_DIR = ROOT_DIR / "data"
SAMPLES_DIR = DATA_DIR / "samples"
TRUTH_DIR = DATA_DIR / "truth"
OUTPUT_DIR = DATA_DIR / "output"
DB_PATH = DATA_DIR / "cedas.db"


def ensure_data_dirs(base: Optional[Path] = None) -> Path:
    """确保运行期目录存在。返回数据根目录。"""
    root = base or DATA_DIR
    for sub in ("samples", "truth", "output"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


__all__ = [
    "APP_NAME",
    "APP_TITLE",
    "DATA_DIR",
    "DB_PATH",
    "MAPPINGS_DIR",
    "OUTPUT_DIR",
    "PACKAGE_DIR",
    "REQUIRED_FIELDS",
    "RESOURCES_DIR",
    "ROOT_DIR",
    "RULES_DIR",
    "SAMPLES_DIR",
    "SUPPORTED_EXTENSIONS",
    "TEMPLATES_DIR",
    "TRUTH_DIR",
    "ensure_data_dirs",
]
