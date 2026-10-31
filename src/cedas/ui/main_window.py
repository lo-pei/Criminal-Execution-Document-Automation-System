"""Tkinter 主窗口。

选 Tkinter 而非 Qt 的理由见 ADR-003：体积与内存占用是首要约束。

复核面板的原文定位用 ``Text`` + ``tag`` 实现（ADR-003、ADR-007）：
点击字段即在原文中高亮对应段落，**不启动任何外部程序**。
当前为骨架，数据流尚未接通。
"""

from __future__ import annotations

from ..config import APP_TITLE, DB_PATH

COLUMNS = (
    ("case_no", "案号", 220),
    ("court_name", "法院", 200),
    ("defendant_count", "被告人数", 90),
    ("judgment_date", "判决日期", 110),
)


class MainWindow:
    """主窗口。构造后调用 :meth:`run` 进入事件循环。"""

    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1080x680")
        self.root.minsize(880, 560)

        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")

        self._build_toolbar(ttk)
        self._build_table(ttk)
        self._build_status(ttk)

    def _build_toolbar(self, ttk) -> None:
        bar = ttk.Frame(self.root, padding=(12, 10))
        bar.pack(side="top", fill="x")

        ttk.Label(bar, text="检索").pack(side="left")
        entry = ttk.Entry(bar, textvariable=self.search_var, width=32)
        entry.pack(side="left", padx=(6, 12))
        entry.bind("<Return>", lambda _event: self.refresh())

        ttk.Button(bar, text="刷新", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="导入判决书", command=self.import_judgment).pack(side="left", padx=6)
        ttk.Button(bar, text="生成文书", command=self.generate_document).pack(side="left")

    def _build_table(self, ttk) -> None:
        container = ttk.Frame(self.root)
        container.pack(side="top", fill="both", expand=True, padx=12, pady=(0, 8))

        self.tree = ttk.Treeview(
            container,
            columns=[column[0] for column in COLUMNS],
            show="headings",
            selectmode="browse",
        )
        for key, title, width in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w")

        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def _build_status(self, ttk) -> None:
        ttk.Label(self.root, textvariable=self.status_var, anchor="w", padding=(12, 4)).pack(
            side="bottom", fill="x"
        )

    # ---- 事件 ----

    def _on_select(self, _event) -> None:
        selection = self.tree.selection()
        if selection:
            self.status_var.set(f"已选中：{self.tree.item(selection[0], 'values')[0]}")
            # TODO(阶段 4): 这里接复核面板，加载字段与原文段落高亮

    def refresh(self) -> None:
        """从台账重新拉取列表。"""
        from ..storage.db import Database

        for item in self.tree.get_children():
            self.tree.delete(item)

        if not DB_PATH.exists():
            self.status_var.set("台账为空：尚未导入任何案件")
            return

        with Database(DB_PATH) as db:
            db.init_schema()
            rows = db.list_cases(self.search_var.get().strip() or None)

        for row in rows:
            self.tree.insert(
                "",
                "end",
                values=(
                    row.get("case_no", ""),
                    row.get("court_name", "") or "",
                    row.get("defendant_count", 0),
                    row.get("judgment_date", "") or "",
                ),
            )
        self.status_var.set(f"共 {len(rows)} 条记录")

    def import_judgment(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "尚未实现",
            "导入通道将在阶段 2 接入。\n\n"
            "当前可用命令行完成同样操作：\n"
            "python -m cedas extract <判决书路径>",
        )

    def generate_document(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "尚未实现",
            "文书生成将在阶段 3 接入（模板映射 + 占位符校验）。",
        )

    def run(self) -> int:
        self.refresh()
        self.root.mainloop()
        return 0


def launch() -> int:
    """启动图形界面。"""
    return MainWindow().run()


__all__ = ["MainWindow", "launch"]
