# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

用法（在项目根目录执行）::

    pyinstaller packaging/cedas.spec --noconfirm --clean

**为什么是 onedir 而不是 onefile**（见 ADR-010）：

1. 法院终端普遍装有准入 / 桌面管控软件，其策略常禁止从 ``%TEMP%`` 执行二进制。
   onefile 每次启动都会把全部内容解压到 ``%TEMP%/_MEIxxxxxx`` 再执行，
   在这类机器上表现为「双击无反应」或被安全软件终止。
2. onefile 在老机械硬盘上冷启动需 5–15 秒（本项目解压量约 100MB+，
   其中 PyMuPDF 的 pymupdf 扩展即占 20MB 以上），onedir 直接从磁盘加载。
3. 单文件更容易被杀毒软件启发式判定为「自解压投放器」而误报。

代价是产物是一个目录而非单个文件，故交付物打成 zip，并附「创建快捷方式」脚本。
"""

from pathlib import Path

SPEC_DIR = Path(SPECPATH)  # noqa: F821 - PyInstaller 注入
PROJECT_DIR = SPEC_DIR.parent
SRC_DIR = PROJECT_DIR / "src"
RESOURCES_DIR = PROJECT_DIR / "resources"

# 只读资源随包分发。PyInstaller 6.x 在 onedir 模式下把它们放进 ``_internal/``，
# 该目录即运行期的 sys._MEIPASS，与 config.BUNDLE_DIR 的语义一致。
datas = [
    (str(RESOURCES_DIR), "resources"),
]

# 运行端绝不使用的重型包。显式排除，避免依赖图的偶然引入把体积和内存拖上去。
# 一旦发现某依赖确实需要，先确认它是否与「4GB 内存 + 旧 CPU」的目标冲突，再放入。
#
# 特别注意 pymupdf 的周边包：PyMuPDF 2.x 会顺带安装 pymupdf4llm / pymupdf_layout，
# 二者依赖 onnxruntime（数百 MB）。即便本项目只用 1.x，构建机的 site-packages
# 里仍可能残留这些包，PyInstaller 的 hook 会顺着包名把它们拉进产物。
excludes = [
    "numpy",
    "pandas",
    "scipy",
    "matplotlib",
    "PIL",
    "IPython",
    "pytest",
    "mypy",
    "ruff",
    "setuptools",
    "pip",
    "wheel",
    "pydoc_data",
    "tkinter.test",
    "test",
    "unittest.test",
    # PyMuPDF 周边 / 模型运行时：本项目一个都不用
    "pymupdf4llm",
    "pymupdf_layout",
    "pymupdf_rag",
    "onnxruntime",
    "torch",
    "transformers",
    "cv2",
]

hiddenimports = [
    "pymupdf",
    "fitz",
    "docx",
    "docxtpl",
    "jinja2",
    "lxml",
    "lxml.etree",
    "lxml._elementpath",
    "sqlite3",
]

a = Analysis(  # noqa: F821
    [str(SPEC_DIR / "launcher.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

# 界面版：不弹控制台窗口。生产用户双击的就是这个。
exe_ui = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CEDAS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX 压缩会显著提高被杀毒软件误报的概率，法院环境禁用
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# 排障版：带控制台。构建脚本用它做打包后冒烟测试（doctor 子命令），
# 交付现场也用它自查——界面起不来时，这是唯一能看到报错的入口。
exe_cli = EXE(  # noqa: F821
    PYZ(a.pure),  # noqa: F821
    a.scripts,
    [],
    exclude_binaries=True,
    name="cedas-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe_ui,
    exe_cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CEDAS",
)
