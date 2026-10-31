"""离线交付包构建脚本。

法院内网机器不能联网。产物必须做到「拷贝目录、双击即用」——目标机不需要
Python、不需要 pip、不需要任何运行库。本脚本在**有网的构建机**上完成全部
下载与编译，输出一个 zip。

用法::

    python tools/build_offline.py                # 完整构建
    python tools/build_offline.py --check-only   # 只做前置检查，不打包

构建机要求（缺一不可，脚本会逐项校验并说明缺哪个）：
  · Windows 64 位
  · 完整安装的 CPython（**必须含 tcl/tk**，精简版 Python 不含 tkinter，
    打出的 exe 界面无法启动）
  · 已安装项目运行依赖与 pyinstaller

产物布局::

    dist/release/CEDAS-<version>-win64.zip
      CEDAS/
        CEDAS.exe            界面版
        cedas-cli.exe        排障版（带控制台）
        _internal/           解释器与依赖（勿手工改动）
        数据说明.txt
      SHA256SUMS.txt
      离线安装说明.txt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_DIR / "dist"
RELEASE_DIR = DIST_DIR / "release"
APP_DIR_NAME = "CEDAS"

# 运行端依赖 → 许可证。交付审计要能回答「这个包里有什么、什么许可」。
DEPENDENCIES = {
    "pymupdf": "AGPL-3.0",
    "python-docx": "MIT",
    "docxtpl": "LGPL-3.0",
    "jinja2": "BSD-3-Clause",
    "markupsafe": "BSD-3-Clause",
    "lxml": "BSD-3-Clause",
    "typing_extensions": "PSF-2.0",
}

# 交付目标。二者功能完全一致，差别只在解释器与依赖版本。
#
# win7：一份包同时覆盖 Win7 与 Win10。CPython 3.9 起不再支持 Win7，故只能锁 3.8。
#        PyMuPDF 随之下压到 1.23（cp38 轮子的最后一版）。这是默认目标。
# win10：仅在 Win7 机器全部退役后使用，可换回 3.11/3.12 与较新依赖。
PROFILES = {
    "win7": {
        "label": "Win7 + Win10 通用",
        "python": ((3, 8), (3, 8)),
        "requirements": "requirements-build-win7.txt",
        "artifact_suffix": "win7-win64",
        "forbid": {
            "pymupdf": ("1.24", "cp38 轮子只到 1.23.x，1.24+ 需要 Python 3.9+"),
            "pyinstaller": ("6.11", "6.11+ 不再保证 cp38 支持"),
        },
    },
    "win10": {
        "label": "仅 Win10 及以上",
        "python": ((3, 9), (3, 12)),
        "requirements": "requirements-build-win10.txt",
        "artifact_suffix": "win10-win64",
        "forbid": {},
    },
}


def _detect_profile() -> str:
    """按当前解释器推断目标：3.8 → win7，其余 → win10。

    不要求人记参数。但用 3.8 解释器时绝不能默认成 win10——
    那会产出一个「自称支持 Win10、实际也支持 Win7」的包，命名与事实不符。
    """
    return "win7" if sys.version_info[:2] == (3, 8) else "win10"


class BuildError(RuntimeError):
    """前置检查不通过。不做降级尝试——打包环境不合格时必须立刻停下。"""


def log(message: str) -> None:
    print(f"[build] {message}", flush=True)


def _read_version() -> str:
    import re

    text = (PROJECT_DIR / "src" / "cedas" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise BuildError("无法从 src/cedas/__init__.py 读取 __version__")
    return match.group(1)


def _installed_version(package: str) -> str | None:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    try:
        return pkg_version(package)
    except PackageNotFoundError:
        return None


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def preflight(target: str, allow_unsupported: bool = False) -> dict[str, str]:
    """前置检查。返回环境信息快照，写入 SBOM。

    这里的每一项都对应一次真实翻车：
      · 32 位解释器 → 打出的包在 64 位机器上虽然能跑，但内存上限只有 2–4GB；
      · 精简版 Python 无 tkinter → exe 双击无界面，且报错在打包阶段完全看不出来；
      · 版本过新 → 目标机 Win10 缺 UCRT 补丁时启动失败；
      · 目标标成 win7 却用 3.11 构建 → 产出的包在 Win7 上根本起不来。
    """
    profile = PROFILES[target]
    (low_major, low_minor), (high_major, high_minor) = profile["python"]
    problems: list[str] = []

    log(f"交付目标：{profile['label']}（{target}）")

    if platform.system() != "Windows":
        problems.append(f"必须在 Windows 上构建（当前 {platform.system()}），PyInstaller 不支持交叉编译")

    is_64 = sys.maxsize > 2**32
    if not is_64:
        problems.append("必须是 64 位解释器（当前是 32 位）")

    major_minor = sys.version_info[:2]
    if not (low_major, low_minor) <= major_minor <= (high_major, high_minor):
        message = (
            f"目标 {target} 要求 Python {low_major}.{low_minor}"
            + (
                f"–{high_major}.{high_minor}"
                if (low_major, low_minor) != (high_major, high_minor)
                else ""
            )
            + f"，当前是 {major_minor[0]}.{major_minor[1]}。\n"
            f"        请用 {profile['requirements']} 对应的解释器重新建虚拟环境。"
        )
        if allow_unsupported:
            log("警告：解释器版本不在目标区间内，已按 --allow-unsupported-python 继续。")
            log("      该产物**不可交付**，仅用于验证构建链路。")
        else:
            problems.append(message)

    # 版本闸门：目标本身决定的硬约束，不满足时打出的包在目标机上必然失效
    for package, (ceiling, reason) in profile.get("forbid", {}).items():
        installed = _installed_version(package)
        if installed and _version_tuple(installed) >= _version_tuple(ceiling):
            problems.append(f"{package} 版本 {installed} 超出 {target} 目标上限（{reason}）")

    try:
        import tkinter

        log(f"tkinter 可用（Tcl/Tk {tkinter.TkVersion}）")
    except Exception as exc:
        problems.append(
            f"tkinter 不可用：{exc}\n"
            "        当前解释器很可能是精简版或安装时未勾选 tcl/tk。\n"
            "        界面版 exe 将无法启动，且打包阶段不会报错——必须在打包前拦下。\n"
            "        处理：改用 python.org 官方安装包重新安装（勾选 tcl/tk and IDLE）。"
        )

    for module, label in (
        ("pymupdf", "PyMuPDF"),
        ("docx", "python-docx"),
        ("docxtpl", "docxtpl"),
    ):
        try:
            __import__(module)
        except ImportError as exc:
            problems.append(f"缺少运行依赖 {label}（{module}）：{exc}")

    try:
        import PyInstaller

        log(f"PyInstaller {PyInstaller.__version__}")
    except ImportError:
        problems.append("缺少 pyinstaller：pip install pyinstaller")

    if problems:
        raise BuildError("前置检查未通过：\n" + "\n".join(f"  - {p}" for p in problems))

    _warn_polluted_environment()

    return {
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "architecture": "64bit" if is_64 else "32bit",
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


# 构建机上存在、但交付物绝不该包含的重型包。存在时不阻断构建（spec 里已 excludes），
# 但必须提示：它们会显著拖慢打包，且一旦某个 hook 顺着包名拉取，产物会突然膨胀数百 MB。
SUSPICIOUS_PACKAGES = (
    "pymupdf4llm",
    "pymupdf_layout",
    "onnxruntime",
    "torch",
    "transformers",
    "opencv-python",
    "numpy",
    "pandas",
    "PyQt5",
    "PySide6",
)


def _warn_polluted_environment() -> None:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    found = []
    for name in SUSPICIOUS_PACKAGES:
        try:
            found.append(f"{name}=={pkg_version(name)}")
        except PackageNotFoundError:
            continue
    if found:
        log(
            "注意：构建机装有交付物不需要的重型包，已在 spec 中排除，"
            "但建议用干净的虚拟环境构建：\n        " + "、".join(found)
        )


def collect_sbom(version: str, env: dict[str, str]) -> dict[str, object]:
    """生成软件物料清单。内网交付要过安全审计，必须先能说清「包里有什么」。"""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    components = []
    for name, license_name in DEPENDENCIES.items():
        try:
            installed = pkg_version(name)
        except PackageNotFoundError:
            log(f"警告：依赖 {name} 未安装，未写入 SBOM")
            continue

        record: dict[str, object] = {
            "name": name,
            "version": installed,
            "license": license_name,
        }
        if name == "pymupdf":
            record["compliance_note"] = (
                "AGPL-3.0。本院内自用不构成分发，不触发开源义务；"
                "向其他单位交付须开源本项目或购买 Artifex 商业许可（ADR-004）。"
            )
        components.append(record)

    return {
        "schema": 1,
        "application": "CEDAS",
        "version": version,
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "build_environment": env,
        "components": components,
    }


def write_windows_version_file(version: str, target: Path) -> Path:
    """生成 Windows 版本资源，使 exe 属性页显示版本与说明。

    内网软件登记通常要求可查看版本号；没有版本资源的 exe 会被当作来源不明的二进制。
    """
    parts = [*version.split("."), "0", "0", "0"][:4]
    numeric = ", ".join(str(int(p)) if p.isdigit() else "0" for p in parts)
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({numeric}), prodvers=({numeric}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('080404B0', [
      StringStruct('CompanyName', '人民法院'),
      StringStruct('FileDescription', '刑事执行文书自动制作系统'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'CEDAS'),
      StringStruct('OriginalFilename', 'CEDAS.exe'),
      StringStruct('ProductName', '刑事执行文书自动制作系统'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""
    target.write_text(content, encoding="utf-8")
    return target


def _quarantine_old_output(app_dir: Path) -> None:
    """把上一轮产物移开，而不是删除。

    两个原因：
    1. 部分构建环境（含本项目的开发机）启用了批量删除保护，PyInstaller 的
       ``--clean`` 会因「一次删除上千个文件」被拦下，表现为构建无理由失败。
    2. 构建保留上一版产物，便于比对体积变化——「这次包为什么大了 40MB」
       是打包环节最常见的问题，留着旧包才能回答。
    """
    if not app_dir.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    quarantine = app_dir.parent / f".prev-{stamp}"
    app_dir.rename(quarantine)
    log(f"上一轮产物已移至 {quarantine.name}（确认新包可用后可手工删除）")


def run_pyinstaller() -> Path:
    out = DIST_DIR / APP_DIR_NAME
    _quarantine_old_output(out)

    log("调用 PyInstaller（onedir）")
    # 不使用 --clean：它会强制删除整个缓存目录，既触发上述删除保护，
    # 也让每次构建都从零解析依赖（构建时间翻倍）。缓存按 spec 校验自动失效。
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(PROJECT_DIR / "packaging" / "cedas.spec"),
            "--noconfirm",
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(DIST_DIR / "build"),
        ],
        cwd=PROJECT_DIR,
        check=False,
    )
    if result.returncode != 0:
        raise BuildError(f"PyInstaller 失败，退出码 {result.returncode}")

    if not (out / "CEDAS.exe").exists():
        raise BuildError(f"未找到产物 {out / 'CEDAS.exe'}")
    return out


def _decode_output(raw: bytes) -> str:
    """解码子进程输出。

    **PyInstaller 产物在 Windows 上不受 ``PYTHONUTF8`` / ``PYTHONIOENCODING`` 控制**：
    bootloader 会重新初始化 stdio，管道输出恒为系统代码页（中文系统 GBK）。
    实测设置这两个环境变量后输出仍是 GBK。故此处按序尝试，而不是假设 UTF-8。
    """
    for encoding in ("utf-8", "gbk", "mbcs"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def smoke_test(app_dir: Path) -> None:
    """打包后立即自检。

    打包成功 ≠ 能运行。最常见的两类故障（tkinter 缺失、resources 未随包）
    都只在运行时暴露，必须在构建机上先跑一遍，不能等到内网现场才发现。
    """
    cli = app_dir / "cedas-cli.exe"
    if not cli.exists():
        raise BuildError("缺少 cedas-cli.exe，无法执行冒烟测试")

    log("冒烟测试：cedas-cli doctor --json")
    # 用临时 HOME，避免污染构建机的项目数据目录
    probe_home = DIST_DIR / "smoke-home"
    if probe_home.exists():
        shutil.rmtree(probe_home)
    probe_home.mkdir(parents=True)

    env = {**__import__("os").environ, "CEDAS_HOME": str(probe_home)}
    result = subprocess.run(
        [str(cli), "doctor", "--json"],
        capture_output=True,
        env=env,
        timeout=180,
        check=False,
    )
    output = _decode_output(result.stdout or b"") + _decode_output(result.stderr or b"")
    log("doctor 输出：\n" + output.strip())

    if result.returncode != 0:
        raise BuildError(f"冒烟测试失败（退出码 {result.returncode}），见上方输出")

    text = _decode_output(result.stdout or b"")
    try:
        payload = json.loads(text[text.index("{") :])
    except (ValueError, json.JSONDecodeError) as exc:
        raise BuildError(f"doctor 输出不是合法 JSON：{exc}") from exc

    if not payload.get("ok"):
        raise BuildError(f"doctor 报告存在问题：{payload.get('problems')}")

    for check in payload.get("checks", []):
        if not check.get("ok"):
            raise BuildError(f"自检项失败：{check.get('name')} {check.get('detail')}")

    # 只认 JSON 里的数值与布尔字段，不比对中文名称：
    # 若解码回退到 replace，中文会变成替换字符，字符串比较必然失败。
    if int(payload.get("rule_count", 0)) <= 0:
        raise BuildError("规则文件未随包分发或数量为零")

    if not payload.get("frozen"):
        raise BuildError("doctor 报告 frozen=False，说明运行的不是打包产物")

    # 校验只读资源确实进了包
    internal = app_dir / "_internal" / "resources" / "rules"
    if not internal.is_dir() or not any(internal.glob("*.json")):
        raise BuildError(f"规则文件未随包分发：{internal}")

    shutil.rmtree(probe_home, ignore_errors=True)
    log("冒烟测试通过")


def measure_size(app_dir: Path) -> tuple[int, int]:
    files = [p for p in app_dir.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def write_readme(target: Path, version: str) -> None:
    """交付包内的说明。读者是不懂技术的内勤人员，只写他们需要知道的。"""
    target.write_text(
        f"""刑事执行文书自动制作系统  v{version}
离线安装说明
==================================================

一、安装
  1. 把 CEDAS 整个文件夹复制到本机任意位置，建议 D:\\CEDAS。
  2. 进入该文件夹，双击 CEDAS.exe 即可使用。
  3. 如需桌面快捷方式：右键 CEDAS.exe → 发送到 → 桌面快捷方式。

二、数据存在哪里
  台账数据库、生成的文书、归档文件都在 CEDAS.exe 同级的 data 文件夹内。
  备份时请整体复制整个 CEDAS 文件夹。
  如需把数据放到其它盘符，可设置环境变量 CEDAS_HOME 指向目标目录。

三、运行环境
  支持 Windows 7 SP1 与 Windows 10（32 位系统不支持，必须是 64 位）。
  不需要安装 Python、不需要联网、不需要管理员权限。

  若本机是 Windows 7，请确认已安装以下系统补丁（多数机器已有）：
    · KB4490628（SHA-2 代码签名支持）——缺它连程序都起不来
    · KB2533623（DLL 加载安全策略）
  这两项由单位信息中心统一推送；无法自行安装时请联系技术人员。

四、打不开生成的文件怎么办
  本程序生成 .docx 格式文书。
  · Office 2010 及以上、WPS 2013 及以上：可直接打开。
  · Office 2007：需另装「Microsoft Office 兼容包」（免费），否则无法打开 .docx。
  · Office 2003 及更早：不支持 .docx，需先装兼容包或用 WPS 打开。

五、出了问题怎么办
  1. 双击 cedas-cli.exe（带控制台的那个），会显示自检信息：
     把屏幕上的内容截图或抄下来，交给技术人员，即可定位大部分问题。
  2. 不要手工修改 _internal 文件夹内的任何内容。

六、升级规则（不必重新安装程序）
  把新的规则文件（.json）放到 CEDAS\\resources\\rules\\ 目录下，
  文件名与内置规则同名即覆盖。修改前的文件请先备份。
  该目录不存在时自行新建即可。

七、版本
  当前版本：{version}
  查看方式：右键 CEDAS.exe → 属性 → 详细信息。
""",
        encoding="utf-8",
    )


def write_data_note(app_dir: Path) -> None:
    (app_dir / "数据说明.txt").write_text(
        "本文件夹存放运行数据，请勿删除。\n"
        "备份 = 复制整个 CEDAS 文件夹。\n"
        "台账数据库：data\\cedas.db\n"
        "生成文书：data\\output\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_release(
    app_dir: Path, version: str, sbom: dict[str, object], target: str, unsafe: bool = False
) -> Path:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = PROFILES[target]["artifact_suffix"] + ("-unsupported" if unsafe else "")
    stage = RELEASE_DIR / f"CEDAS-{version}-{suffix}"
    if stage.exists():
        _quarantine_old_output(stage)
    stage.mkdir(parents=True)

    shutil.copytree(app_dir, stage / APP_DIR_NAME)
    write_readme(stage / "离线安装说明.txt", version)
    write_data_note(stage / APP_DIR_NAME)

    (stage / "sbom.json").write_text(
        json.dumps(sbom, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 校验和清单：交付介质在院内流转时用于确认文件未被篡改或损坏
    sums = []
    for path in sorted(stage.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            sums.append(f"{sha256(path)}  {path.relative_to(stage).as_posix()}")
    (stage / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")

    zip_path = RELEASE_DIR / f"CEDAS-{version}-{suffix}.zip"
    if zip_path.exists():
        zip_path.unlink()

    # 压缩包内必须带一层版本目录：否则用户解压到桌面时，上千个文件会直接铺在桌面。
    # 这属于交付体验缺陷，不是美观问题——现场因此找不到 CEDAS.exe 是常见的支持工单。
    archive_root = stage.name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, (Path(archive_root) / path.relative_to(stage)).as_posix())

    log(f"交付包：{zip_path}  ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return zip_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 CEDAS 离线交付包")
    parser.add_argument("--check-only", action="store_true", help="只做前置检查")
    parser.add_argument(
        "--target",
        choices=["auto", *PROFILES],
        default="auto",
        help="交付目标。auto = 按当前解释器推断（3.8 → win7，其余 → win10）",
    )
    parser.add_argument(
        "--allow-unsupported-python",
        action="store_true",
        help="解释器版本超出目标区间仍继续（产物名会标 -unsupported，仅用于验证构建链路）",
    )
    args = parser.parse_args(argv)

    target = _detect_profile() if args.target == "auto" else args.target
    version = _read_version()
    log(f"CEDAS v{version}  构建机 Python {platform.python_version()}")

    try:
        env = preflight(target, allow_unsupported=args.allow_unsupported_python)
        if args.check_only:
            log("前置检查通过。")
            return 0

        sbom = collect_sbom(version, env)
        sbom["target"] = target
        if args.allow_unsupported_python:
            sbom["deliverable"] = False
            sbom["build_note"] = "解释器版本超出目标区间，本产物不可交付，仅用于验证构建链路"
        write_windows_version_file(version, PROJECT_DIR / "packaging" / "version_info.txt")

        app_dir = run_pyinstaller()
        smoke_test(app_dir)

        file_count, total = measure_size(app_dir)
        log(f"产物：{file_count} 个文件，{total / 1024 / 1024:.1f} MB")

        make_release(app_dir, version, sbom, target, unsafe=args.allow_unsupported_python)

        (RELEASE_DIR / "build-report.json").write_text(
            json.dumps(
                {
                    "version": version,
                    "target": target,
                    "target_label": PROFILES[target]["label"],
                    "built_at": sbom["built_at"],
                    "file_count": file_count,
                    "uncompressed_bytes": total,
                    "python": env["python"],
                    "smoke_test": "passed",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        log("完成。")
        return 0
    except BuildError as exc:
        print(f"\n[build] 失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
