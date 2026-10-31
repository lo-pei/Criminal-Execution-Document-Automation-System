"""Tkinter 主窗口。

选 Tkinter 而非 Qt 的理由见 ADR-003：体积与内存占用是首要约束。

界面只有一件事：**把「判决书里读到了什么」摆到人眼前，让人确认后出文书。**
因此三块内容固定：左边待办（有几名被告人、哪份还差东西）、右边字段（值从哪来）、
下方原文（这个值是不是从这句话来的）。

不做的三件事，都有原因：

- **不做拖拽导入。** Tk 实现拖拽要装 ``tkdnd``，Win7 下版本兼容差，内网补装不了。
- **不做多文件批次。** 一次一份判决书，与现场「审完一份出一份」的节奏一致；
  多文件队列要想清楚「跨案批量补录」，v1 不做，留到界面二期。
- **不在界面里重算业务规则。** 阻断判定在 ``pipeline.check_blockers``，界面只翻译成
  人话；两边各算一次，迟早会不一致。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from ..config import APP_TITLE, OUTPUT_DIR
from ..pipeline import DocumentItem, PipelineReport, PreparedDocument, generate, plan, prepare
from . import review
from .theme import (
    BG,
    BORDER,
    PRIMARY,
    PRIMARY_ACTIVE,
    PRIMARY_TEXT,
    SIZE_BODY,
    SIZE_HEADLINE,
    SIZE_SMALL,
    SIZE_TITLE,
    SURFACE,
    TEXT,
    TEXT_FAINT,
    TEXT_MUTED,
    WARN_FG,
    apply_style,
    family,
    font,
    init_theme,
)


class MainWindow:
    """主窗口。构造后调用 :meth:`run` 进入事件循环。"""

    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1000x690")
        self.root.minsize(880, 600)

        init_theme(self.root)
        apply_style(self.root, ttk)

        self._document: Optional[PreparedDocument] = None
        self._report: Optional[PipelineReport] = None
        self._seq: Optional[int] = None
        self._start_dates: dict[int, date] = {}

        self._build_menu()
        self._build_header()
        self._build_summary()
        self._build_body()
        self._build_status()
        self._show_empty()

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        tk = self._tk
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="选择判决书…", command=self.import_judgment)
        file_menu.add_command(label="重新读取当前判决书", command=self._reload)
        file_menu.add_command(
            label="按识别结果重命名判决书…", command=self.rename_source
        )
        file_menu.add_separator()
        file_menu.add_command(label="打开输出文件夹", command=self._open_output)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.destroy)
        menubar.add_cascade(label="文件", menu=file_menu)
        self.root.configure(menu=menubar)

    def _build_header(self) -> None:
        tk = self._tk
        from tkinter import ttk

        self.root.grid_rowconfigure(3, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        header = tk.Frame(self.root, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))

        left = tk.Frame(header, bg=BG)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(
            left, text=APP_TITLE, bg=BG, fg=TEXT, font=font(SIZE_TITLE, "bold")
        ).pack(anchor="w")
        self._subtitle = tk.Label(
            left, text="还没有导入判决书", bg=BG, fg=TEXT_MUTED, font=font(SIZE_BODY)
        )
        self._subtitle.pack(anchor="w", pady=(2, 0))

        ttk.Button(header, text="选择判决书", command=self.import_judgment).pack(side="right")

    def _build_summary(self) -> None:
        tk = self._tk

        bar = tk.Frame(self.root, bg=BG)
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(2, 8))
        bar.grid_columnconfigure(1, weight=1)

        self._headline = tk.Label(
            bar, text="", bg=BG, fg=TEXT, font=font(SIZE_HEADLINE, "bold"), anchor="w", justify="left"
        )
        self._headline.grid(row=0, column=0, sticky="w")
        self._detail = tk.Label(
            bar, text="", bg=BG, fg=TEXT_MUTED, font=font(SIZE_BODY), anchor="w", justify="left"
        )
        self._detail.grid(row=1, column=0, sticky="w", pady=(3, 0))

        self._action = tk.Button(
            bar,
            text="生成执行通知书",
            command=self.generate_document,
            bg=PRIMARY,
            fg=PRIMARY_TEXT,
            activebackground=PRIMARY_ACTIVE,
            activeforeground=PRIMARY_TEXT,
            disabledforeground="#9aa0a6",
            relief="flat",
            bd=0,
            padx=18,
            pady=7,
            font=font(SIZE_BODY),
            cursor="hand2",
        )
        self._action.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))

        # 分隔线单独占一行，不叠在结论条上——叠着放时它会盖住第二行文字。
        tk.Frame(self.root, bg=BORDER, height=1).grid(row=2, column=0, sticky="ew")

    def _build_body(self) -> None:
        tk = self._tk
        from .widgets import EmptyState, FieldTable, QueuePanel, SourcePanel

        body = tk.Frame(self.root, bg=BG)
        body.grid(row=3, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        self._main = tk.Frame(body, bg=BG)
        self._main.grid(row=0, column=0, sticky="nsew")
        self._main.grid_rowconfigure(0, weight=1)
        self._main.grid_columnconfigure(1, weight=1)

        self._queue = QueuePanel(self._main, on_select=self._select)
        self._queue.configure(width=252)
        self._queue.grid(row=0, column=0, sticky="ns")
        # 左栏内部用 pack 排布，所以要用 pack_propagate；
        # 用错开关时左栏会被卡片内容撑到 460px，把右侧滚动条挤出窗口。
        self._queue.pack_propagate(False)
        self._main.grid_columnconfigure(0, minsize=252)

        right = tk.Frame(self._main, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self._fields = FieldTable(
            right,
            on_edit=self._apply_edit,
            on_locate=self._locate,
            edit_text=self._edit_text,
        )
        self._fields.grid(row=0, column=0, sticky="nsew")

        tk.Frame(right, bg=BORDER, height=1).grid(row=1, column=0, sticky="ew", padx=16)
        self._source = SourcePanel(right)
        self._source.grid(row=2, column=0, sticky="ew")

        self._empty = EmptyState(body, on_import=self.import_judgment)
        self._empty.grid(row=0, column=0, sticky="nsew")

    def _build_status(self) -> None:
        tk = self._tk

        bar = tk.Frame(self.root, bg=SURFACE)
        bar.grid(row=4, column=0, sticky="ew")
        tk.Frame(bar, bg=BORDER, height=1).pack(fill="x")

        inner = tk.Frame(bar, bg=SURFACE)
        inner.pack(fill="x", padx=20, pady=6)

        self._status = tk.Label(
            inner, text="就绪", bg=SURFACE, fg=TEXT_FAINT, font=font(SIZE_SMALL), anchor="w"
        )
        self._status.pack(side="left")

        # 文件名不规范时出现的入口。不常驻：名字已经规范时多一个按钮只是噪音。
        # 用统一的小字号与琥珀色（与「待补充」同色，不新增第二种强调色）。
        self._rename_link = tk.Label(
            inner,
            text="规范文件名",
            bg=SURFACE,
            fg=WARN_FG,
            # 加下划线：只有颜色的话，它看起来像一句提示而不像能点的东西。
            font=(family(), SIZE_SMALL, "underline"),
            cursor="hand2",
        )
        self._rename_link.bind("<Button-1>", lambda _event: self.rename_source())

        self._open_button = tk.Label(
            inner,
            text="打开输出文件夹",
            bg=SURFACE,
            fg=TEXT_MUTED,
            font=font(SIZE_SMALL),
            cursor="hand2",
        )
        self._open_button.pack(side="right")
        self._open_button.bind("<Button-1>", lambda _event: self._open_output())

        # pack(side="right") 先摆的更靠右，所以这个入口落在「打开输出文件夹」左边。
        self._rename_link.pack(side="right", padx=(0, 14))
        self._rename_link.pack_forget()

    # ------------------------------------------------------------------
    # 状态切换
    # ------------------------------------------------------------------

    def _show_empty(self) -> None:
        # 用 grid_remove 而不是 tkraise：留在布局里的滚动条会在空状态右侧露出一条灰边。
        self._main.grid_remove()
        self._empty.grid()
        self._set_summary(
            "从一份刑事判决书开始。",
            "读取、核对、出文书，三步都在本机完成，不联网。",
            action="生成执行通知书",
            enabled=False,
        )
        self._subtitle.configure(text="")
        self._status.configure(text="就绪")
        self._rename_link.pack_forget()

    def _show_main(self) -> None:
        self._empty.grid_remove()
        self._main.grid()

    def _set_summary(
        self, headline: str, detail: str, *, action: str, enabled: bool
    ) -> None:
        self._headline.configure(text=headline)
        self._detail.configure(text=detail)
        self._action.configure(
            text=action,
            state="normal" if enabled else "disabled",
            bg=PRIMARY if enabled else "#f1f2f3",
            activebackground=PRIMARY_ACTIVE if enabled else "#f1f2f3",
            cursor="hand2" if enabled else "arrow",
        )

    def _render(self) -> None:
        """把 report 铺到界面上。界面不参与任何业务判断。"""
        if self._document is None or self._report is None:
            return

        report = self._report
        self._show_main()

        summary = review.summarize(report, missing_template=review.missing_template(report))
        self._set_summary(
            summary.headline, summary.detail, action=summary.action, enabled=summary.can_generate
        )

        self._subtitle.configure(text=self._header_text())
        self._queue.render(review.build_queue(report))

        defendant = self._current()
        if defendant is not None:
            item = self._item()
            fields = review.build_fields(
                self._document, defendant, start_source=item.start_source if item else ""
            )
            self._fields.render(fields)
            self._source.render(review.source_lines(self._document))

        self._status.configure(text=self._status_text())
        self._sync_rename_link()

    def _header_text(self) -> str:
        report = self._report
        if report is None:
            return "还没有导入判决书"
        parts = [report.case_no or "案号未识别", report.court_name or "法院未识别"]
        if len(report.items) > 1:
            parts.append(f"{len(report.items)} 名被告人")
        return " · ".join(parts)

    def _status_text(self) -> str:
        document = self._document
        if document is None:
            return "就绪"
        channel = "PDF 通道" if document.channel == "pdf" else "TXT 通道"
        # 文件名打头：现场一个文件夹里躺着七八份「扫描件00x.pdf」，
        # 不写清楚现在读的是哪一份，复核完才发现看错了文件。
        parts = [_short_name(document.source.name), f"已读到 {len(document.paragraphs)} 段", channel]
        if self._report is not None and self._report.rendered:
            parts.append(f"已生成 {len(self._report.rendered)} 份")
        if self._report is not None and review.missing_template(self._report):
            parts.append("文书模板未就位")
        return " · ".join(parts)

    def _sync_rename_link(self) -> None:
        """按当前命名方案决定「文件名待规范」这个入口要不要露出来。"""
        plan = self._document.name_plan() if self._document is not None else None
        if plan is not None and plan.needs_rename:
            self._rename_link.pack(side="right", padx=(0, 14))
        else:
            self._rename_link.pack_forget()

    # ------------------------------------------------------------------
    # 数据流
    # ------------------------------------------------------------------

    def _current(self):
        if self._document is None or self._seq is None:
            return None
        return self._document.defendant(self._seq)

    def _item(self) -> Optional[DocumentItem]:
        if self._report is None or self._seq is None:
            return None
        return next((item for item in self._report.items if item.seq == self._seq), None)

    def _select(self, seq: int) -> None:
        self._seq = seq
        self._queue.select(seq)
        defendant = self._current()
        if defendant is None or self._document is None:
            return
        item = self._item()
        self._fields.render(
            review.build_fields(
                self._document, defendant, start_source=item.start_source if item else ""
            )
        )

    def _replan(self) -> None:
        """只重算，不重读文件。改一个日期不必重新解析一遍 PDF。"""
        if self._document is None:
            return
        self._report = plan(
            self._document,
            start_dates=self._start_dates or None,
            require_template=False,
        )
        if self._seq is None and self._document.defendants:
            self._seq = self._document.defendants[0].seq
        self._render()
        if self._seq is not None:
            self._queue.select(self._seq)

    def _load(self, path: Path) -> None:
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            document = prepare(path)
        except Exception as error:  # 界面必须把任何失败都说成人话
            self._alert("打不开这份文件", str(error))
            return
        finally:
            self.root.configure(cursor="")

        self._document = document
        self._start_dates.clear()
        self._seq = document.defendants[0].seq if document.defendants else None
        self._replan()

        if not document.defendants:
            self._alert(
                "没有认出被告人",
                "这份文件里没有找到「被告人某某」这样的句子。\n"
                "请确认选的是判决书正文，而不是封面、送达回证或扫描件。",
            )

    def _reload(self) -> None:
        if self._document is None:
            self.import_judgment()
            return
        self._load(self._document.source)

    def rename_source(self) -> None:
        """按识别结果把源文件改成规范名。

        改名动的是用户自己的文件（常放在桌面或下载目录），所以一律**先给人看，
        再动文件**：确认框里并排列出原名与新名，缺项也一并说清。
        """
        from tkinter import messagebox

        from ..storage.naming import RenameError

        if self._document is None:
            self.import_judgment()
            return

        prompt = review.rename_prompt(self._document.name_plan())
        if not prompt.can_apply:
            messagebox.showinfo(prompt.title, prompt.body, parent=self.root)
            return
        if not messagebox.askyesno(
            prompt.title, f"{prompt.body}\n\n确定改名吗？", parent=self.root
        ):
            return

        try:
            path = self._document.rename_source()
        except RenameError as error:
            self._alert("改名没成功", str(error))
            return

        self._replan()
        self._alert(
            "已改名",
            f"这份判决书现在的名字是：\n{path.name}\n\n"
            "以后按案号排序就能直接找到它；同名文件不会被覆盖。",
        )

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    def import_judgment(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="选择判决书",
            filetypes=[("判决书", "*.pdf *.txt"), ("PDF 文件", "*.pdf"), ("文本文件", "*.txt")],
        )
        if path:
            self._load(Path(path))

    def generate_document(self) -> None:
        if self._document is None:
            return
        if self._report is not None and review.missing_template(self._report):
            self._alert(
                "还差文书模板",
                "程序目录下的 resources/templates 文件夹里没有找到执行通知书模板。\n\n"
                "请把法院的 .docx 模板放进去，再点一次生成。",
            )
            return

        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            report = generate(
                prepared=self._document,
                start_dates=self._start_dates or None,
                record=True,
            )
        except Exception as error:
            self._alert("没能生成", str(error))
            self._replan()
            return
        finally:
            self.root.configure(cursor="")

        self._report = report
        self._render()

        if report.rendered:
            first = report.rendered[0].path
            where = first.parent if first is not None else OUTPUT_DIR
            self._alert(
                "已生成",
                f"共 {len(report.rendered)} 份，已按「案号_被告人_文书类型_日期」命名，"
                f"存到：\n{where}\n\n"
                "重新生成不会覆盖上一份，文件名末尾会带 v2、v3。",
            )
        elif report.blocked:
            self._alert("没有生成", "还有必填项没补齐，补齐后再点一次。")

    def _apply_edit(self, row: review.FieldRow, text: str) -> None:
        if self._document is None or self._seq is None:
            return
        try:
            if row.path == "defendant.span.start":
                if text.strip():
                    self._start_dates[self._seq] = review.parse_date(text)
                else:
                    self._start_dates.pop(self._seq, None)
            else:
                review.apply_edit(self._document, self._seq, row.path, text)
        except review.EditError as error:
            self._alert("这个值不能用", str(error))
            return
        self._replan()

    def _edit_text(self, row: review.FieldRow) -> str:
        defendant = self._current()
        if defendant is None or self._document is None:
            return ""
        if row.path == "defendant.span.start":
            return self._start_dates.get(self._seq).isoformat() if self._seq in self._start_dates else (
                defendant.span.start.isoformat() if defendant.span.start else ""
            )
        return review.edit_text(self._document, defendant, row.path)

    def _locate(self, row: review.FieldRow) -> None:
        self._source.highlight(row.paragraph_index + 1 if row.paragraph_index is not None else None)

    def _open_output(self) -> None:
        import os

        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(OUTPUT_DIR))  # type: ignore[attr-defined]
        except Exception as error:
            self._alert("打不开文件夹", f"{OUTPUT_DIR}\n\n{error}")

    def _alert(self, title: str, message: str) -> None:
        from tkinter import messagebox

        messagebox.showwarning(title, message, parent=self.root)

    # ------------------------------------------------------------------

    def run(self) -> int:
        self.root.mainloop()
        return 0


def _short_name(name: str, limit: int = 30) -> str:
    """状态栏里的文件名。超长截尾——「微信图片_20230510103045.pdf」这种尾巴没有信息量。"""
    return name if len(name) <= limit else f"{name[: limit - 1]}…"


def launch() -> int:
    """启动图形界面。"""
    return MainWindow().run()


__all__ = ["MainWindow", "launch"]
