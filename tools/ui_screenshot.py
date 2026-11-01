"""界面截图工具：把主窗口渲染出来存成 PNG，用于人工核对版式。

为什么需要它：Tk 的版式问题（滚动条被挤出去、文字被面板切一半、栏宽被内容撑开）
只有渲染出来才看得见，单元测试测不到像素。

用法（需要一个带 tkinter 的解释器，本项目工作机上是 3.12）::

    py -3.12 tools/ui_screenshot.py empty     # 首次打开的空状态
    py -3.12 tools/ui_screenshot.py loaded    # 导入合成判决书后的主界面
    py -3.12 tools/ui_screenshot.py blocked   # 人为抹掉身份证号，看「待补充」
    py -3.12 tools/ui_screenshot.py locate    # 点字段后原文展开与高亮
    py -3.12 tools/ui_screenshot.py rename    # 文件名不规范时状态栏的「文件名待规范」
    py -3.12 tools/ui_screenshot.py combined  # 数罪并罚：决定执行刑期与「并罚方式」

输出到 ``build/ui-<stage>.png``。需要 Pillow；``build/`` 已被 gitignore。
截图前把进程设为 DPI 感知并把 tk scaling 固定为 96dpi，否则 125% 缩放的机器上
抓出来的图会被裁掉右半边。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cedas.ui import review
from cedas.ui.main_window import MainWindow


def shot(window: MainWindow, out: Path) -> None:
    import ctypes
    from ctypes import wintypes

    from PIL import ImageGrab

    root = window.root
    root.update_idletasks()
    root.update()

    # 用 wm_frame() 拿外框句柄：winfo_rootx/y 给的是客户区原点（会截掉菜单栏），
    # 而 GetParent(winfo_id()) 有时会返回桌面窗口，把桌面也截进来。
    frame = root.wm_frame()
    hwnd = int(frame, 16) if frame else ctypes.windll.user32.GetParent(root.winfo_id())
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))

    # 外框矩形含 DWM 阴影，左右各比客户区宽出十来个像素。
    # 先按外框整抓，再左右各裁掉这段，避免把桌面内容截进来。
    image = ImageGrab.grab((rect.left, rect.top, rect.right, rect.bottom))
    pad = max((image.width - root.winfo_width()) // 2, 0)
    image = image.crop((pad, 0, pad + root.winfo_width(), image.height - pad))
    image.save(out)
    w, h = image.size
    print("saved", out, w, h)


def enable_dpi_awareness() -> None:
    """截图坐标要和窗口像素 1:1，否则抓出来的图会被 DPI 缩放裁掉右边。"""
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()


def _input(stage: str) -> Path:
    """这一阶段要载入哪份文件。

    ``rename`` 阶段用一份**内容相同、名字不规范**的副本：真实场景里判决书就是
    「微信图片_20230510103045.pdf」这种名字，用它才看得出状态栏的入口对不对。
    """
    if stage == "combined":
        # 数罪并罚样本：一份判决里两个罪，「刑期」显示的是决定执行刑期，
        # 「并罚方式」显示「数罪并罚」——这两处在普通案件里是看不出来的。
        #
        # 用真实判决（史某伟：盗窃 + 危险驾驶，决定执行有期徒刑六个月）。
        # 不再用自编样本演示：`examples/` 现在只留合成基线，多罪结构全部由
        # `data/samples/` 的真实判决承担。该目录不入库（SECURITY.md），
        # 新克隆的仓库里没有它——此时明确报错，不要静默回落到基线，
        # 否则截出来的图里没有「数罪并罚」，看图的人会以为功能没做。
        real = Path("data/samples/S001__冀0131刑初371号.txt")
        if not real.exists():
            raise SystemExit(
                f"缺少 {real}。该目录不入库，需先按 data/samples/README.md 采集真实样本；"
                "此阶段不能用合成基线替代。"
            )
        return real

    source = Path("examples/S000__京0105刑初123号.txt")
    if stage != "rename":
        return source
    import shutil

    messy = Path("build/ui-input") / "微信图片_20230510103045.txt"
    messy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, messy)
    return messy


def main() -> None:
    enable_dpi_awareness()
    stage = sys.argv[1] if len(sys.argv) > 1 else "empty"
    out = Path("build") / f"ui-{stage}.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    window = MainWindow()
    # 法院老机器多为 100% 缩放，这里按 96dpi 渲染，截出来的就是那个样子
    window.root.tk.call("tk", "scaling", 96.0 / 72.0)
    window.root.attributes("-topmost", True)
    window.root.lift()

    if stage != "empty":
        window._load(_input(stage))
    if stage == "combined":
        # 数罪并罚样本的字段多出「并罚方式」一行，默认 690 高会把「量刑与刑期」
        # 压到折线以下；这一张图是给人核对字段排布的，拉高到能一屏看全。
        window.root.geometry("1000x1000")
    if stage == "locate":
        rows = review.build_fields(
            window._document,
            window._document.defendants[0],
            start_source=window._report.items[0].start_source,
        )
        window._locate(next(row for row in rows if row.label == "公民身份号码"))
    if stage == "blocked":
        # 抹掉身份证号，制造「待补充」
        defendant = window._document.defendants[0]
        defendant.id_number.value = None
        window._replan()

    shot(window, out)
    window.root.destroy()


if __name__ == "__main__":
    main()
