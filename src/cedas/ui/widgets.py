"""自绘的小部件：滚动区、待办列表、字段表、原文面板。

为什么不用 ``ttk.Treeview`` 做这些都一樣：

- 待办每行要两行文字（姓名 + 刑期起止）加一个状态标签，Treeview 一行只能一行字。
- 字段表要「名称 / 值 / 依据」三列混排，且值要能双击变输入框；
  Treeview 不支持原地编辑，浮 Entry 也得自己算坐标，不如直接用 Frame 拼。

代价是要自己处理滚动与选中态，收益是版式完全可控，且老机器上的开销比 Treeview 更小
（行数是个位数到十几，Frame 足够）。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional, Sequence

from . import review
from .theme import (
    BG,
    BORDER,
    BORDER_SOFT,
    DONE_BG,
    DONE_FG,
    HIGHLIGHT,
    OK_BG,
    OK_FG,
    SELECT,
    SELECT_BORDER,
    SIZE_BODY,
    SIZE_HEADLINE,
    SIZE_SECTION,
    SIZE_SMALL,
    SURFACE,
    TEXT,
    TEXT_FAINT,
    TEXT_MUTED,
    WARN_BG,
    WARN_FG,
    font,
)

_BADGE = {
    review.STATUS_BLOCKED: (WARN_BG, WARN_FG),
    review.STATUS_READY: (OK_BG, OK_FG),
    review.STATUS_DONE: (DONE_BG, DONE_FG),
}


class SlimScrollbar(tk.Canvas):
    """自绘的细滚动条。

    为什么不用 ``tk.Scrollbar``：Windows 的 classic 滚动条会忽略 ``bg``/``troughcolor``，
    统一画成系统按钮面的浅灰（#f0f0f0），与窗口边框同色，肉眼分不出那里有滚动条；
    ``ttk.Scrollbar`` 在 vista 主题下只有 6px 且同样淡。两条路都看不见，只能自己画。
    """

    WIDTH = 10

    def __init__(self, master, command):
        super().__init__(
            master, width=self.WIDTH, bg=BG, highlightthickness=0, bd=0, cursor="arrow"
        )
        self._command = command
        self._first = 0.0
        self._last = 1.0
        self._drag: Optional[tuple[int, float]] = None

        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda _event: setattr(self, "_drag", None))

    def set(self, first: str, last: str) -> None:
        self._first, self._last = float(first), float(last)
        visible = self._first <= 0.0 and self._last >= 1.0
        if visible:
            self.grid_remove()
        else:
            self.grid()
        self._draw()

    def _geometry(self) -> tuple[int, int, int]:
        height = max(self.winfo_height(), 1)
        top = int(self._first * height)
        bottom = int(self._last * height)
        return height, top, max(top + 28, bottom)

    def _draw(self) -> None:
        self.delete("all")
        if self._first <= 0.0 and self._last >= 1.0:
            return
        height, top, bottom = self._geometry()
        width = self.winfo_width() or self.WIDTH
        self.create_rectangle(
            3, top, width - 3, min(bottom, height), fill="#c2c6cb", outline=""
        )

    def _press(self, event) -> None:
        height, top, bottom = self._geometry()
        if top <= event.y <= bottom:
            self._drag = (event.y, self._first)
            return
        span = self._last - self._first
        self._command("moveto", max(0.0, min(1.0, event.y / height - span / 2)))

    def _move(self, event) -> None:
        if self._drag is None:
            return
        origin, first = self._drag
        height = max(self.winfo_height(), 1)
        self._command("moveto", max(0.0, min(1.0, first + (event.y - origin) / height)))


class ScrollArea(tk.Frame):
    """竖向滚动容器。内容放进 ``.body``。"""

    def __init__(self, master, background: str = BG, **kwargs):
        super().__init__(master, bg=background, **kwargs)
        self._background = background
        self.canvas = tk.Canvas(self, bg=background, highlightthickness=0, bd=0)
        self.scrollbar = SlimScrollbar(self, command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=background)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scroll)
        # 用 grid 而不是 pack：canvas 先 pack(expand=True) 会占满整行，
        # 之后再 pack 的滚动条会被挤到看不见（宽度算成 0）。
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.scrollbar.grid_remove()

        self.body.bind("<Configure>", self._sync)
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    # ---- 内部 ----

    def _on_scroll(self, first: str, last: str) -> None:
        self.scrollbar.set(first, last)

    def _sync(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self, _event=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._wheel)

    def _unbind_wheel(self, _event=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _wheel(self, event) -> None:
        if self.body.winfo_height() <= self.canvas.winfo_height():
            return
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)


class QueuePanel(tk.Frame):
    """左栏待办。待补充的排在最前——漏看一份阻断的，代价最高。"""

    def __init__(self, master, on_select: Callable[[int], None]):
        super().__init__(master, bg=SURFACE)
        self._on_select = on_select
        self._cards: dict[int, list[tk.Widget]] = {}
        self._selected: Optional[int] = None

        head = tk.Frame(self, bg=SURFACE)
        head.pack(fill="x", padx=12, pady=(12, 6))
        self._count = tk.Label(
            head, text="待办", bg=SURFACE, fg=TEXT, font=font(SIZE_SECTION, "bold"), anchor="w"
        )
        self._count.pack(side="left")

        self.area = ScrollArea(self, background=SURFACE)
        self.area.pack(fill="both", expand=True, padx=(6, 0), pady=(0, 8))

    def render(self, rows: Sequence[review.QueueRow]) -> None:
        self.area.clear()
        self._cards.clear()
        self._count.configure(text="待办" if not rows else f"待办（{len(rows)}）")

        if not rows:
            tk.Label(
                self.area.body,
                text="还没有待办。\n导入判决书后，\n被告人的名字会出现在这里。",
                bg=SURFACE,
                fg=TEXT_FAINT,
                font=font(SIZE_BODY),
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=14, pady=12)
            return

        for row in rows:
            self._make_row(row)
        if self._selected is not None and self._selected in self._cards:
            self._paint(self._selected, selected=True)

    def _make_row(self, row: review.QueueRow) -> None:
        holder = tk.Frame(self.area.body, bg=SURFACE)
        holder.pack(fill="x", padx=6, pady=2)

        bg, fg = _BADGE.get(row.status, (OK_BG, OK_FG))
        card = tk.Frame(holder, bg=BG, highlightthickness=1, highlightbackground=BORDER_SOFT)
        card.pack(fill="x")

        inner = tk.Frame(card, bg=BG)
        inner.pack(fill="x", padx=10, pady=7)

        left = tk.Frame(inner, bg=BG)
        left.pack(side="left", fill="x", expand=True)
        title = tk.Label(
            left, text=row.title, bg=BG, fg=TEXT, font=font(SIZE_BODY, "bold"), anchor="w"
        )
        title.pack(fill="x")
        subtitle = tk.Label(
            left, text=row.subtitle, bg=BG, fg=TEXT_MUTED, font=font(SIZE_SMALL), anchor="w"
        )
        subtitle.pack(fill="x", pady=(2, 0))

        badge = tk.Label(
            inner,
            text=row.status,
            bg=bg,
            fg=fg,
            font=font(SIZE_SMALL),
            padx=6,
            pady=1,
        )
        badge.pack(side="right", padx=(8, 0))

        widgets = [card, inner, left, title, subtitle]
        self._cards[row.seq] = widgets
        for widget in widgets:
            widget.bind("<Button-1>", lambda _event, seq=row.seq: self._on_select(seq))
        badge.bind("<Button-1>", lambda _event, seq=row.seq: self._on_select(seq))

    def _paint(self, seq: int, *, selected: bool) -> None:
        widgets = self._cards.get(seq)
        if not widgets:
            return
        card = widgets[0]
        background = SELECT if selected else BG
        for widget in widgets:
            widget.configure(bg=background)
        card.configure(
            highlightbackground=SELECT_BORDER if selected else BORDER_SOFT,
            highlightthickness=1,
        )
        self._selected = seq if selected else None

    def select(self, seq: int) -> None:
        for other in list(self._cards):
            if other != seq:
                self._paint(other, selected=False)
        self._paint(seq, selected=True)


class FieldTable(tk.Frame):
    """右侧字段表。点行跳到原文，双击值可补录。"""

    def __init__(
        self,
        master,
        *,
        on_edit: Callable[[review.FieldRow, str], None],
        on_locate: Callable[[review.FieldRow], None],
        edit_text: Callable[[review.FieldRow], str],
    ):
        super().__init__(master, bg=BG)
        self._on_edit = on_edit
        self._on_locate = on_locate
        self._edit_text = edit_text
        self._editor: Optional[tk.Entry] = None
        self._editing: Optional[review.FieldRow] = None
        self._rows: dict[str, review.FieldRow] = {}

        self.area = ScrollArea(self, background=BG)
        self.area.pack(fill="both", expand=True)

    # ---- 渲染 ----

    def render(self, rows: Sequence[review.FieldRow]) -> None:
        self._close_editor()
        self.area.clear()
        self._rows = {row.label: row for row in rows}

        for index, (group, items) in enumerate(review.grouped(rows)):
            self._section(group, top=14 if index else 8)
            for row in items:
                self._line(row)
        tk.Frame(self.area.body, bg=BG, height=8).pack(fill="x")

    def _section(self, title: str, *, top: int) -> None:
        label = tk.Label(
            self.area.body,
            text=title,
            bg=BG,
            fg=TEXT,
            font=font(SIZE_SECTION, "bold"),
            anchor="w",
        )
        label.pack(fill="x", padx=16, pady=(top, 4))

    def _line(self, row: review.FieldRow) -> None:
        line = tk.Frame(self.area.body, bg=BG)
        line.pack(fill="x", padx=16)
        line.grid_columnconfigure(0, minsize=96)
        line.grid_columnconfigure(1, weight=1)
        line.grid_columnconfigure(2, minsize=110)

        name = tk.Label(
            line, text=row.label, bg=BG, fg=TEXT_MUTED, font=font(SIZE_BODY), anchor="w"
        )
        name.grid(row=0, column=0, sticky="w", pady=2)

        value = review.display_value(row)
        missing = row.required and not row.value
        color = WARN_FG if missing else TEXT

        # 只有「要你填」的字段才画成输入框。已填的字段一旦都套上边框，
        # 整屏就像一张待填的表单，反而看不出哪里真的缺东西。
        needs_input = row.editable and not row.value
        if row.editable:
            fill = "#fcfcfc" if needs_input else BG
            box = tk.Frame(
                line,
                bg=fill,
                highlightthickness=1 if needs_input else 0,
                highlightbackground=BORDER,
            )
            box.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=1)
            field = tk.Label(
                box,
                text=value,
                bg=fill,
                fg=color,
                font=font(SIZE_BODY),
                anchor="w",
                padx=6,
                pady=2,
                cursor="hand2",
            )
            field.pack(fill="x")
            self._bind_editable(row, box, field, hover=not needs_input)
        else:
            field = tk.Label(
                line, text=value, bg=BG, fg=color, font=font(SIZE_BODY), anchor="w", pady=2
            )
            field.grid(row=0, column=1, sticky="ew", padx=(0, 10))
            field.bind("<Button-1>", lambda _event, r=row: self._on_locate(r))

        note = row.provenance or row.note
        hint = tk.Label(
            line, text=note, bg=BG, fg=TEXT_FAINT, font=font(SIZE_SMALL), anchor="w"
        )
        hint.grid(row=0, column=2, sticky="w")

        name.bind("<Button-1>", lambda _event, r=row: self._on_locate(r))

    def _bind_editable(self, row: review.FieldRow, box: tk.Frame, field: tk.Label, *, hover: bool) -> None:
        field.bind("<Button-1>", lambda _event, r=row: self._on_locate(r))
        field.bind("<Double-Button-1>", lambda _event, r=row: self._begin_edit(r, box))
        if not hover:
            return

        def enter(_event=None):
            box.configure(bg="#f6f6f6")
            field.configure(bg="#f6f6f6")

        def leave(_event=None):
            box.configure(bg=BG)
            field.configure(bg=BG)

        field.bind("<Enter>", enter)
        field.bind("<Leave>", leave)

    # ---- 原地编辑 ----

    def _begin_edit(self, row: review.FieldRow, box: tk.Frame) -> None:
        self._close_editor()
        entry = tk.Entry(
            box,
            relief="flat",
            bd=0,
            bg=BG,
            fg=TEXT,
            font=font(SIZE_BODY),
            highlightthickness=0,
        )
        entry.insert(0, self._edit_text(row))
        entry.select_range(0, "end")
        entry.place(x=1, y=1, relwidth=1.0, relheight=1.0)
        entry.focus_set()

        self._editor = entry
        self._editing = row
        entry.bind("<Return>", lambda _event: self._commit())
        entry.bind("<Escape>", lambda _event: self._close_editor())
        entry.bind("<FocusOut>", lambda _event: self._commit())

    def _commit(self) -> None:
        editor, row = self._editor, self._editing
        if editor is None or row is None:
            return
        text = editor.get()
        self._editor, self._editing = None, None
        editor.destroy()
        if text != self._edit_text(row):
            self._on_edit(row, text)

    def _close_editor(self) -> None:
        if self._editor is not None:
            editor, self._editor, self._editing = self._editor, None, None
            editor.destroy()


class SourcePanel(tk.Frame):
    """原文面板。点字段即滚到对应段落并高亮，不启动任何外部程序。"""

    def __init__(self, master, *, height: int = 3):
        super().__init__(master, bg=BG)

        self._expanded = False

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=16, pady=(6, 2))
        tk.Label(
            head, text="原文", bg=BG, fg=TEXT, font=font(SIZE_SECTION, "bold"), anchor="w"
        ).pack(side="left")
        self._hint = tk.Label(
            head, text="点上面的字段可看这句话的出处", bg=BG, fg=TEXT_FAINT, font=font(SIZE_SMALL)
        )
        self._hint.pack(side="left", padx=(8, 0))

        self._toggle = tk.Label(
            head, text="展开", bg=BG, fg=TEXT_MUTED, font=font(SIZE_SMALL), cursor="hand2"
        )
        self._toggle.pack(side="right")
        self._toggle.bind("<Button-1>", lambda _event: self.toggle())

        self._body = tk.Frame(self, bg=BG)
        body = self._body

        self.text = tk.Text(
            body,
            height=height,
            wrap="word",
            bg="#fcfcfc",
            fg=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER_SOFT,
            font=font(SIZE_BODY),
            padx=10,
            pady=8,
            spacing1=1,
            spacing3=6,
            cursor="arrow",
        )
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.text.tag_configure("hit", background=HIGHLIGHT)
        self.text.tag_configure("num", foreground=TEXT_FAINT)
        self.text.configure(state="disabled")

    def expand(self) -> None:
        if self._expanded:
            return
        self._expanded = True
        self._body.pack(fill="both", expand=False, padx=(16, 0), pady=(0, 8))
        self._toggle.configure(text="收起")

    def collapse(self) -> None:
        if not self._expanded:
            return
        self._expanded = False
        self._body.pack_forget()
        self._toggle.configure(text="展开")

    def toggle(self) -> None:
        self.collapse() if self._expanded else self.expand()

    def render(self, lines: Sequence[tuple[int, str]]) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for number, body in lines:
            start = self.text.index("insert")
            self.text.insert("end", f"{number}  ", ("num",))
            self.text.insert("end", f"{body}\n")
            self.text.tag_add(f"p{number}", start, self.text.index("insert"))
        self.text.configure(state="disabled")

    def highlight(self, number: Optional[int]) -> None:
        self.expand()
        self.text.configure(state="normal")
        self.text.tag_remove("hit", "1.0", "end")
        if number is None:
            self.text.configure(state="disabled")
            self._hint.configure(text="这一项是系统算出来的，原文里没有对应句子")
            return
        tag = f"p{number}"
        ranges = self.text.tag_ranges(tag)
        if ranges:
            self.text.tag_add("hit", ranges[0], ranges[1])
            self.text.see(ranges[0])
            self.text.configure(state="disabled")
            self._hint.configure(text=f"已定位到第 {number} 段")
            return
        self.text.configure(state="disabled")
        self._hint.configure(text="这一项在原文里找不到对应段落")


class EmptyState(tk.Frame):
    """首次打开时的空状态：不写「暂无数据」，写清楚下一步做什么。"""

    def __init__(self, master, *, on_import: Callable[[], None]):
        super().__init__(master, bg=BG)

        box = tk.Frame(self, bg=BG)
        box.place(relx=0.5, rely=0.42, anchor="center")

        tk.Label(
            box, text="导入后在这里核对", bg=BG, fg=TEXT, font=font(SIZE_HEADLINE, "bold")
        ).pack()
        tk.Label(
            box,
            text="读到什么、缺什么、从原文哪一句来的，都会列出来。",
            bg=BG,
            fg=TEXT_MUTED,
            font=font(SIZE_BODY),
            justify="center",
        ).pack(pady=(10, 16))

        button = tk.Button(
            box,
            text="选择判决书文件",
            command=on_import,
            bg="#1d1f20",
            fg="#ffffff",
            activebackground="#000000",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=20,
            pady=8,
            font=font(SIZE_BODY),
            cursor="hand2",
        )
        button.pack()

        tk.Label(
            box,
            text="支持 PDF 与 TXT，一次一份；原文件只读，不会被改动",
            bg=BG,
            fg=TEXT_FAINT,
            font=font(SIZE_SMALL),
        ).pack(pady=(10, 0))


__all__ = ["EmptyState", "FieldTable", "QueuePanel", "ScrollArea", "SourcePanel"]
