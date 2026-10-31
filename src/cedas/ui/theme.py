"""界面配色与字体。

三条来自实操的约束：

1. **只用系统自带字体。** 内网装不了字体，微软雅黑在 Win7 起就是标配；
   宋体在 Tk 里渲染发虚，不用。
2. **字号阶梯压到最窄。** 12/13/15/17px 四档，靠字重和颜色分层。
   品牌页那种 1.5 倍跳档放到办公软件里只会制造噪音。
3. **一个对象最多一处强调色。** 界面上唯一的彩色是「待补充」的琥珀；
   主按钮用近黑，不用政务蓝——蓝色在 Win7 的 Aero 主题下会和系统高亮色打架。
"""

from __future__ import annotations

# ---- 颜色 -----------------------------------------------------------------

BG = "#ffffff"
SURFACE = "#fafafa"
BORDER = "#e4e5e7"
BORDER_SOFT = "#eeeff0"
TEXT = "#1d1f20"
TEXT_MUTED = "#6b7076"
TEXT_FAINT = "#9aa0a6"
SELECT = "#eeeeee"
SELECT_BORDER = "#d9dbde"

PRIMARY = "#1d1f20"
PRIMARY_ACTIVE = "#000000"
PRIMARY_TEXT = "#ffffff"
PRIMARY_DISABLED = "#c9ccd0"

WARN_BG = "#fffbeb"
WARN_FG = "#92400e"
OK_BG = "#eef5f0"
OK_FG = "#1f6f43"
DONE_BG = "#eef2f8"
DONE_FG = "#1f4b7a"

HIGHLIGHT = "#fff3c4"

# ---- 字体 -----------------------------------------------------------------

SIZE_TITLE = 13  # 约 17px，窗口标题
SIZE_HEADLINE = 11  # 约 15px，结论那句话
SIZE_SECTION = 10  # 约 13px，分组标题
SIZE_BODY = 9  # 约 12px，正文
SIZE_SMALL = 8  # 约 11px，依据与脚注

PREFERRED_FAMILIES = ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "SimSun")

_family: str = "TkDefaultFont"


def init_theme(root) -> str:
    """挑一个系统里真实存在的字体族，并记住它。"""
    global _family
    from tkinter import font as tkfont

    available = set(tkfont.families(root))
    for name in PREFERRED_FAMILIES:
        if name in available:
            _family = name
            break
    else:
        _family = "TkDefaultFont"
    return _family


def family() -> str:
    return _family


def font(size: int = SIZE_BODY, weight: str = "normal"):
    """Tk 字体元组。字号单位是 pt，与设计稿的 px 有 1px 级偏差，需真机校一次。"""
    return (_family, size, weight)


def apply_style(root, ttk) -> None:
    """统一 ttk 控件样式。优先 vista 主题，Win7 与 Win10 都有。"""
    style = ttk.Style(root)
    for candidate in ("vista", "winnative", "clam"):
        if candidate in style.theme_names():
            style.theme_use(candidate)
            break

    root.configure(background=BG)
    style.configure(".", background=BG, foreground=TEXT, font=font(SIZE_BODY))
    style.configure("TFrame", background=BG)
    style.configure("Surface.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", background=BG, foreground=TEXT_MUTED)
    style.configure(
        "TButton", font=font(SIZE_BODY), padding=(10, 5)
    )
    style.configure("Vertical.TScrollbar", background=BG, troughcolor=BG, borderwidth=0)


__all__ = [
    "BG",
    "BORDER",
    "BORDER_SOFT",
    "DONE_BG",
    "DONE_FG",
    "HIGHLIGHT",
    "OK_BG",
    "OK_FG",
    "PRIMARY",
    "PRIMARY_ACTIVE",
    "PRIMARY_DISABLED",
    "PRIMARY_TEXT",
    "SELECT",
    "SELECT_BORDER",
    "SIZE_BODY",
    "SIZE_HEADLINE",
    "SIZE_SECTION",
    "SIZE_SMALL",
    "SIZE_TITLE",
    "SURFACE",
    "TEXT",
    "TEXT_FAINT",
    "TEXT_MUTED",
    "WARN_BG",
    "WARN_FG",
    "apply_style",
    "family",
    "font",
    "init_theme",
]
