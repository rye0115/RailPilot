"""RailPilot desktop client (Tkinter).

A local-only dashboard around the existing CLI/core modules:
- query official 12306 pages in a real browser
- build a waitlist plan (never auto-submits)
- show security checks and local data location
- QR login page (never pays, never places an order)
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

from ..config import ConfigError, data_dir, events_path, load_config, state_path
from .. import __version__
from ..core.waitlist import build_plan
from ..models import Candidate, Event, Task, utc_now_iso
from ..rail.adapter import validate_presale_date
from ..rail.selectors import SEAT_NAMES
from ..security.doctor import run_checks
from ..security.redact import safe_exception
from ..store.events import EventStore
from ..store.state import StateStore
from .theme import COLORS, FONT_H1, FONT_H2, FONT_LABEL, FONT_MONO, FONT_SMALL, FONT_TITLE, FONT_UI, apply_theme
from .session import DesktopSession
from .journey import JourneyPage
from .widgets import Card, FlatButton, NavButton, Pill, StatCard, divider

NAV_ITEMS = [
    ("dashboard", "总览"),
    ("query", "查票"),
    ("journey", "我的行程"),
    ("waitlist", "候补"),
    ("login", "登录"),
    ("security", "安全"),
    ("logs", "日志"),
]


def _now_text() -> str:
    return time.strftime("%H:%M:%S")


def _find_config() -> Path:
    roots = [Path.cwd()]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parents[2])
    for root in roots:
        for candidate in (root / "config.yaml", root / "design" / "config.example.yaml"):
            if candidate.exists():
                return candidate
    return Path("config.yaml")


def _entry(master, textvariable, width: int = 16) -> tk.Entry:
    return tk.Entry(
        master,
        textvariable=textvariable,
        width=width,
        bg=COLORS["card2"],
        fg=COLORS["text"],
        insertbackground=COLORS["text"],
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=COLORS["border"],
        highlightcolor=COLORS["accent"],
        font=FONT_UI,
    )


def _field(master, label: str, variable, width: int = 16) -> tk.Frame:
    box = tk.Frame(master, bg=COLORS["card"])
    tk.Label(box, text=label, bg=COLORS["card"], fg=COLORS["muted"], font=FONT_SMALL, anchor="w").pack(fill="x")
    entry = _entry(box, variable, width=width)
    entry.pack(fill="x", pady=(4, 0))
    return box


class BasePage(tk.Frame):
    def __init__(self, app: "RailPilotApp"):
        super().__init__(app.content_inner, bg=COLORS["bg"])
        self.app = app
        self.build()

    def build(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        return None


class DashboardPage(BasePage):
    def build(self) -> None:
        tk.Label(self, text="总览", bg=COLORS["bg"], fg=COLORS["text"], font=FONT_H1, anchor="w").pack(fill="x")
        tk.Label(
            self,
            text="在查票页添加备选车次，再到「我的行程」确认乘车人、预订并核对订单。",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_LABEL,
            anchor="w",
        ).pack(fill="x", pady=(4, 16))

        stats = tk.Frame(self, bg=COLORS["bg"])
        stats.pack(fill="x")
        self.stat_tasks = StatCard(stats, "任务", "0", "配置中的行程")
        self.stat_candidates = StatCard(stats, "候选", "0", "日期 × 车次 × 席别")
        self.stat_booking = StatCard(stats, "我的备选", "0", "已保存的车次和席别")
        self.stat_security = StatCard(stats, "安全", "DPAPI", "会话默认不落盘")
        for index, card in enumerate(
            (self.stat_tasks, self.stat_candidates, self.stat_booking, self.stat_security)
        ):
            card.grid(row=0, column=index, padx=(0, 12) if index < 3 else 0, sticky="nsew")
            stats.grid_columnconfigure(index, weight=1)

        row = tk.Frame(self, bg=COLORS["bg"])
        row.pack(fill="both", expand=True, pady=(14, 0))

        task_card = Card(row, title="当前任务")
        task_card.pack(side="left", fill="both", expand=True, padx=(0, 12))
        self.task_name = tk.Label(
            task_card.body,
            text="—",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=FONT_H2,
            anchor="w",
        )
        self.task_name.pack(fill="x")
        self.task_meta = tk.Label(
            task_card.body,
            text="还没有任务。编辑 config.yaml 或使用 CLI 的 waitlist 命令。",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=480,
        )
        self.task_meta.pack(fill="x", pady=(8, 12))
        self.task_detail = tk.Label(
            task_card.body,
            text="",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=480,
        )
        self.task_detail.pack(fill="x")
        buttons = tk.Frame(task_card.body, bg=COLORS["card"])
        buttons.pack(fill="x", pady=(16, 0))
        FlatButton(buttons, "去查票", command=lambda: self.app.show_page("query")).pack(side="left")
        FlatButton(buttons, "生成候补", variant="secondary", command=lambda: self.app.show_page("waitlist")).pack(
            side="left", padx=(8, 0)
        )

        security_card = Card(row, title="安全状态")
        security_card.pack(side="left", fill="both", expand=True)
        self.security_rows = {}
        for key, label in (
            ("login", "扫码登录（不存密码）"),
            ("session", "会话默认只存内存"),
            ("allowlist", "只访问 *.12306.cn"),
            ("order", "单次提交须先确认具体行程"),
        ):
            line = tk.Frame(security_card.body, bg=COLORS["card"])
            line.pack(fill="x", pady=5)
            dot = tk.Label(line, text="●", bg=COLORS["card"], fg=COLORS["success"], font=FONT_SMALL)
            dot.pack(side="left")
            tk.Label(line, text=label, bg=COLORS["card"], fg=COLORS["text"], font=FONT_SMALL, anchor="w").pack(
                side="left", padx=(8, 0), fill="x", expand=True
            )
            self.security_rows[key] = dot

        quick = Card(self, title="快速操作")
        quick.pack(fill="x", pady=(14, 0))
        FlatButton(quick.body, "运行安全自检", command=self.app.run_doctor).pack(side="left")
        FlatButton(quick.body, "校时", variant="secondary", command=self.app.run_clock).pack(side="left", padx=(8, 0))
        FlatButton(quick.body, "打开数据目录", variant="secondary", command=self.app.open_data_dir).pack(
            side="left", padx=(8, 0)
        )
        FlatButton(quick.body, "日志", variant="ghost", command=lambda: self.app.show_page("logs")).pack(
            side="left", padx=(8, 0)
        )

    def refresh(self) -> None:
        tasks = self.app.tasks
        self.stat_tasks.set(str(len(tasks)), "配置中的行程")
        self.stat_candidates.set(str(sum(len(t.candidates) for t in tasks)), "日期 × 车次 × 席别")
        self.stat_booking.set(str(len(self.app.pages["journey"].trip.choices)), "已保存的车次和席别")
        self.stat_security.set("DPAPI", "会话默认不落盘")
        if not tasks:
            self.task_name.configure(text="—")
            self.task_meta.configure(text="还没有任务。编辑 config.yaml 后重启客户端。")
            self.task_detail.configure(text="")
            return
        task = tasks[0]
        self.task_name.configure(text=task.name)
        self.task_meta.configure(
            text=f"{task.from_station} → {task.to_station} · {', '.join(task.dates)}\n"
            f"乘客：{', '.join(p.name for p in task.passengers)} · 候选：{len(task.candidates)}"
        )
        self.task_detail.configure(
            text="\n".join(
                f"{c.date}  {c.train_no:>5}  {c.seat}  {'★' * max(0, 3 - c.priority)}"
                for c in sorted(task.candidates, key=lambda x: (x.priority, x.seat_priority, x.date))[:6]
            )
        )


class QueryPage(BasePage):
    def build(self) -> None:
        tk.Label(self, text="查票", bg=COLORS["bg"], fg=COLORS["text"], font=FONT_H1, anchor="w").pack(fill="x")
        tk.Label(
            self,
            text="查询官方余票，选中车次后可前往 12306 确认乘车人、席别和订单。",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_LABEL,
            anchor="w",
        ).pack(fill="x", pady=(4, 16))

        form = Card(self, title="查询条件")
        form.pack(fill="x")
        row = tk.Frame(form.body, bg=COLORS["card"])
        row.pack(fill="x")
        task = self.app.tasks[0] if self.app.tasks else None
        default_date = (date.today() + timedelta(days=1)).isoformat()
        self.date_var = tk.StringVar(value=default_date)
        self.from_var = tk.StringVar(value=task.from_station if task else "北京南")
        self.to_var = tk.StringVar(value=task.to_station if task else "上海虹桥")
        self.headless_var = tk.BooleanVar(value=True)
        for label, var, width in (
            ("日期（YYYY-MM-DD）", self.date_var, 16),
            ("出发站", self.from_var, 14),
            ("到达站", self.to_var, 14),
        ):
            _field(row, label, var, width=width).pack(side="left", padx=(0, 12))
        check = tk.Checkbutton(
            row,
            text="独立查票时隐藏浏览器",
            variable=self.headless_var,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            activebackground=COLORS["card"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["card2"],
            font=FONT_SMALL,
            bd=0,
            highlightthickness=0,
        )
        check.pack(side="left", padx=(4, 12), pady=(18, 0))
        self.query_button = FlatButton(row, "查询余票", command=self.query)
        self.query_button.pack(side="left", pady=(18, 0))
        self.status = tk.Label(
            form.body,
            text="查询日期必须在 15 天预售期内；默认无头运行。",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
        )
        self.status.pack(fill="x", pady=(10, 0))

        results = Card(self, title="余票结果")
        results.pack(fill="both", expand=True, pady=(14, 0))
        toolbar = tk.Frame(results.body, bg=COLORS["card"])
        toolbar.pack(fill="x", pady=(0, 8))
        tk.Label(toolbar, text="筛选车次", bg=COLORS["card"], fg=COLORS["muted"], font=FONT_SMALL).pack(side="left")
        self.filter_var = tk.StringVar()
        filter_entry = _entry(toolbar, self.filter_var, width=12)
        filter_entry.pack(side="left", padx=(8, 0))
        self.filter_var.trace_add("write", lambda *_: self.populate())
        self.count_label = tk.Label(
            toolbar,
            text="尚未查询",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
        )
        self.count_label.pack(side="left", padx=(12, 0))
        FlatButton(toolbar, "生成候补", variant="secondary", command=self.to_waitlist).pack(side="right")
        FlatButton(toolbar, "加入行程", command=self.add_to_journey).pack(side="right", padx=(0, 5))
        FlatButton(toolbar, "打开官网预订", command=self.open_official).pack(side="right", padx=(0, 8))

        self._seats = tuple(dict.fromkeys(("二等座", "一等座", "商务座", *SEAT_NAMES)))
        columns = ("date", "train", "dep", "arr", *self._seats, "status")
        headings = ("日期", "车次", "出发", "到达", *self._seats, "状态")
        widths = (96, 80, 64, 64, *(76 for _ in self._seats), 90)
        self.tree = ttk.Treeview(results.body, columns=columns, show="headings", height=14, selectmode="browse")
        for column, heading, width in zip(columns, headings, widths):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, minwidth=width, anchor="center")
        self.tree.tag_configure("odd", background=COLORS["card2"])
        scroll = ttk.Scrollbar(results.body, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(results.body, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        horizontal.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.results: list[Candidate] = []
        self._row_candidates: dict[str, list[Candidate]] = {}
        self.populate()

    def query(self) -> None:
        date_text = self.date_var.get().strip()
        from_text = self.from_var.get().strip()
        to_text = self.to_var.get().strip()
        if not from_text or not to_text:
            messagebox.showwarning("查票", "出发站和到达站不能为空。")
            return
        try:
            validate_presale_date(date_text)
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("查票", str(exc))
            return
        headless = bool(self.headless_var.get())

        def work():
            return self.app.session.query(date_text, from_text, to_text, headless)

        def done(payload):
            results, blocked = payload
            self.results = results
            self.populate()
            note = f"共 {len(results)} 条" + (f"；已拦截 {', '.join(blocked)}" if blocked else "")
            self.status.configure(text=note, fg=COLORS["success"] if results else COLORS["amber"])
            self.app.record("QUERY_OK", note)

        def failed(exc):
            self.status.configure(text=f"查询失败：{safe_exception(exc)}", fg=COLORS["danger"])
            self.app.record("QUERY_ERROR", str(exc))

        if self.app.run_async(work, done, failed, on_done=lambda: self.query_button.configure(state="normal", text="查询余票")):
            self.results = []
            self.populate()
            self.query_button.configure(state="disabled", text="查询中…")
            self.status.configure(text="正在查询官方余票…", fg=COLORS["amber"])
            self.app.record("QUERY_START", f"{date_text} {from_text}->{to_text}")

    def populate(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._row_candidates.clear()
        needle = self.filter_var.get().strip().lower()
        grouped: dict[tuple[str, str, str, str], list[Candidate]] = {}
        seat_types: set[str] = set()
        for candidate in self.results:
            if needle and needle not in candidate.train_no.lower():
                continue
            key = (candidate.date, candidate.depart_time, candidate.train_no, candidate.arrive_time)
            grouped.setdefault(key, []).append(candidate)
            seat_types.add(candidate.seat)
        visible_seats = [seat for seat in self._seats if seat in seat_types] or self._seats[:3]
        self.tree.configure(displaycolumns=("date", "train", "dep", "arr", *visible_seats, "status"))
        for index, ((date_text, depart, train_no, arrive), candidates) in enumerate(sorted(grouped.items())):
            seats = {c.seat: c.available for c in candidates}
            status = "待核对"
            if any(v == "有" or (v.isdecimal() and int(v) > 0) for v in seats.values()):
                status = "可订"
            elif "候补" in seats.values():
                status = "候补"
            elif all(v in ("0", "无", "--", "*", "") for v in seats.values()):
                status = "暂无余票"
            row = self.tree.insert(
                "",
                "end",
                values=(date_text, train_no, depart, arrive, *(seats.get(s, "—") for s in self._seats), status),
                tags=("odd",) if index % 2 else (),
            )
            self._row_candidates[row] = candidates
        shown_seats = sum(len(group) for group in grouped.values())
        self.count_label.configure(text=f"显示 {len(grouped)} 趟 / {shown_seats} 条席别")

    def to_waitlist(self) -> None:
        rows = self.tree.selection() or self.tree.get_children()
        if not rows:
            messagebox.showinfo("候补", "当前没有可导入的车次，请先查询或调整筛选。")
            return
        candidates = [c for row in rows for c in self._row_candidates[row]]
        self.app.waitlist_page.import_candidates(candidates)
        self.app.show_page("waitlist")

    def add_to_journey(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("加入行程", "先在结果中选中一趟车。", parent=self)
            return
        choices = self._row_candidates[selection[0]]
        first = choices[0]
        window = tk.Toplevel(self)
        window.title("选择可接受的席别")
        window.transient(self.app)
        window.configure(bg=COLORS["card"])
        tk.Label(window, text=f"{first.date}  {first.train_no}  {first.from_station} → {first.to_station}",
                 bg=COLORS["card"], fg=COLORS["text"], font=FONT_LABEL).pack(padx=18, pady=12)
        options = []
        for c in choices:
            value = tk.BooleanVar(window)
            ttk.Checkbutton(window, text=f"{c.seat} · {c.available}", variable=value).pack(anchor="w", padx=18)
            options.append((value, c))
        def add():
            selected = [c for value, c in options if value.get()]
            if not selected:
                messagebox.showinfo("席别", "请选择至少一个席别。", parent=window)
                return
            try:
                self.app.pages["journey"].add_choices(selected)
            except Exception as exc:
                messagebox.showerror("加入行程", safe_exception(exc), parent=window)
                return
            window.destroy()
            self.app.show_page("journey")
        FlatButton(window, "保存到行程", command=add).pack(padx=18, pady=12)
        window.grab_set()

    def open_official(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("预订", "请先在查询结果中选中一趟车。")
            return
        candidate = self._row_candidates[selection[0]][0]

        def open_booking():
            return self.app.session.visible_browser().open_booking(candidate)

        def done(found):
            note = (
                f"已定位 {candidate.train_no}。请在 12306 确认席别和乘车人，提交后到登录页核对待支付订单。"
                if found else f"请在 12306 复查 {candidate.train_no}，当前页面尚未找到该车次。"
            )
            self.status.configure(text=note, fg=COLORS["text"] if found else COLORS["amber"])

        self.app.run_async(
            open_booking,
            done,
            lambda exc: messagebox.showerror("预订", safe_exception(exc)),
        )


class WaitlistPage(BasePage):
    def build(self) -> None:
        tk.Label(self, text="候补", bg=COLORS["bg"], fg=COLORS["text"], font=FONT_H1, anchor="w").pack(fill="x")
        tk.Label(
            self,
            text="生成 6 单 × 3 日期 × ≤60 组合的计划；默认只生成，不自动提交。",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_LABEL,
            anchor="w",
        ).pack(fill="x", pady=(4, 16))

        form = Card(self, title="候补条件")
        form.pack(fill="x")
        row = tk.Frame(form.body, bg=COLORS["card"])
        row.pack(fill="x")
        self.task_var = tk.StringVar()
        box = tk.Frame(row, bg=COLORS["card"])
        box.pack(side="left", padx=(0, 12))
        tk.Label(box, text="任务", bg=COLORS["card"], fg=COLORS["muted"], font=FONT_SMALL, anchor="w").pack(fill="x")
        self.task_combo = ttk.Combobox(box, textvariable=self.task_var, state="readonly", width=28)
        self.task_combo.pack(fill="x", pady=(4, 0))
        self.task_combo.bind("<<ComboboxSelected>>", lambda _: self._set_default_cutoff(self.task_combo.current()))
        self.cutoff_var = tk.StringVar()
        _field(row, "截止兑现（ISO8601）", self.cutoff_var, width=30).pack(side="left", padx=(0, 12))
        FlatButton(row, "生成候补计划", command=self.generate).pack(side="left", pady=(18, 0))
        FlatButton(row, "打开官方页", variant="ghost", command=self.open_official).pack(side="left", padx=(8, 0), pady=(18, 0))
        self.source = tk.StringVar(value="config")
        sources = tk.Frame(form.body, bg=COLORS["card"])
        sources.pack(fill="x", pady=(10, 0))
        for label, value in (("配置任务", "config"), ("已导入的查票结果", "query")):
            button = tk.Radiobutton(
                sources, text=label, variable=self.source, value=value,
                command=lambda: self._set_default_cutoff(self.task_combo.current()),
                bg=COLORS["card"], fg=COLORS["text"], selectcolor=COLORS["card2"],
                activebackground=COLORS["card"], activeforeground=COLORS["text"],
            )
            button.pack(side="left")
            if value == "query":
                self.imported_button = button
                button.configure(state="disabled")
        self.status = tk.Label(
            form.body,
            text="生成计划后，请在 12306 确认候补需求和预付款期限。",
            bg=COLORS["card"],
            fg=COLORS["amber"],
            font=FONT_SMALL,
            anchor="w",
        )
        self.status.pack(fill="x", pady=(4, 0))

        results = Card(self, title="计划")
        results.pack(fill="both", expand=True, pady=(14, 0))
        columns = ("index", "dates", "combos", "passengers")
        headings = ("单号", "日期", "组合数", "乘客")
        widths = (60, 240, 80, 260)
        self.tree = ttk.Treeview(results.body, columns=columns, show="headings", height=5)
        for column, heading, width in zip(columns, headings, widths):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, anchor="center")
        self.tree.pack(fill="both", expand=True)
        detail_shell = tk.Frame(results.body, bg=COLORS["card"])
        detail_shell.pack(fill="both", expand=True, pady=(10, 0))
        self.detail = tk.Text(
            detail_shell,
            height=7,
            bg=COLORS["card2"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            font=FONT_MONO,
            padx=10,
            pady=8,
        )
        detail_scroll = ttk.Scrollbar(detail_shell, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        self.detail.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        self.detail.insert("1.0", "生成计划后，这里显示可直接核对的 JSON。")
        self.detail.configure(state="disabled")
        self.plan = None
        self.imported_candidates: list[Candidate] = []
        self.cutoff_var.trace_add("write", lambda *_: self._clear_plan())

    def import_candidates(self, candidates: list[Candidate]) -> None:
        self.imported_candidates = candidates
        self.source.set("query")
        first = candidates[0]
        trains = len({c.train_no for c in candidates})
        self.imported_button.configure(
            state="normal", text=f"查票结果 · {first.date} {first.from_station} → {first.to_station}（{trains} 趟）"
        )
        self._set_default_cutoff(self.task_combo.current())

    def _clear_plan(self) -> None:
        if self.plan is None:
            return
        self.plan = None
        self.tree.delete(*self.tree.get_children())
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.configure(state="disabled")
        self.status.configure(text="条件已更改，请重新生成候补计划。", fg=COLORS["amber"])

    def refresh(self) -> None:
        names = [task.name for task in self.app.tasks]
        self.task_combo.configure(values=names)
        if names and not self.task_var.get():
            self.task_var.set(names[0])
            self._set_default_cutoff(0)

    def _set_default_cutoff(self, index: int) -> None:
        self._clear_plan()
        if self.source.get() == "query" and self.imported_candidates:
            try:
                earliest = min(
                    datetime.fromisoformat(f"{c.date}T{c.depart_time}+08:00")
                    for c in self.imported_candidates if c.depart_time
                )
                hours = float(self.app.cfg.get("waitlist", {}).get("cutoff_hours_before_departure", 2))
                if hours < 0:
                    raise ValueError("negative cutoff interval")
                self.cutoff_var.set((earliest - timedelta(hours=hours)).isoformat())
            except (ValueError, TypeError, OverflowError):
                self.cutoff_var.set("")
                self.status.configure(text="无法推算截止时间，请手动填写。", fg=COLORS["amber"])
            return
        if 0 <= index < len(self.app.tasks):
            first = sorted(self.app.tasks[index].dates)[0]
            self.cutoff_var.set(f"{first}T18:00:00+08:00")

    def generate(self) -> None:
        if not self.app.tasks:
            messagebox.showinfo("候补", "配置里没有任务。")
            return
        try:
            index = max(0, self.task_combo.current())
        except tk.TclError:
            index = 0
        task = self.app.tasks[index]
        candidates = self.imported_candidates if self.source.get() == "query" else task.candidates
        if not candidates:
            messagebox.showinfo("候补", "没有可用候选。")
            return
        cutoff = self.cutoff_var.get().strip()
        if not cutoff:
            messagebox.showwarning("候补", "请填写截止兑现时间。")
            return
        try:
            plan = build_plan(
                candidates,
                task.passengers,
                cutoff,
                accept_new_trains=bool(self.app.cfg.get("waitlist", {}).get("accept_new_trains", True)),
                latest_arrival=str(self.app.cfg.get("waitlist", {}).get("latest_arrival", "")),
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("候补", f"生成失败：{exc}")
            return
        self.plan = plan
        for row in self.tree.get_children():
            self.tree.delete(row)
        for order in plan.orders:
            self.tree.insert(
                "",
                "end",
                values=(order.index, ", ".join(order.dates), len(order.combos), ", ".join(order.passengers)),
            )
        text = json.dumps(plan.to_dict(), ensure_ascii=False, indent=2)
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")
        self.status.configure(
            text=f"已生成 {len(plan.orders)} 单 / {plan.total_combos} 组合。" + " ".join(plan.warnings),
            fg=COLORS["amber"] if plan.warnings else COLORS["success"],
        )
        self.app.record("WAITLIST_PLAN", f"orders={len(plan.orders)} combos={plan.total_combos}")

    def open_official(self) -> None:
        self.app.run_async(
            lambda: self.app.session.visible_browser().open_waitlist(),
            lambda _: self.status.configure(text="请在 12306 页面选择车次和席别，确认候补需求。", fg=COLORS["text"]),
            lambda exc: messagebox.showerror("候补", safe_exception(exc)),
        )


class LoginPage(BasePage):
    def build(self) -> None:
        tk.Label(self, text="登录", bg=COLORS["bg"], fg=COLORS["text"], font=FONT_H1, anchor="w").pack(fill="x")
        tk.Label(
            self,
            text="只支持 12306 App 扫码；会话默认只存内存，不保存密码。",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_LABEL,
            anchor="w",
        ).pack(fill="x", pady=(4, 16))

        card = Card(self, title="扫码登录")
        card.pack(fill="x")
        self.status = tk.Label(
            card.body,
            text="尚未登录。点击下方按钮打开官方登录页。",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=FONT_UI,
            anchor="w",
        )
        self.status.pack(fill="x")
        self.detail = tk.Label(
            card.body,
            text=(
                "RailPilot 会打开一个独立的 Edge 窗口；你在普通 Edge 里的登录不会传进来。"
                "请在那个新窗口里用 12306 App 扫码，并在手机上点“确认登录”（可能需要人脸/短信验证）。"
                "程序只等待登录态，不支付、不下单。"
            ),
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=720,
        )
        self.detail.pack(fill="x", pady=(8, 14))
        self.button = FlatButton(card.body, "打开扫码登录", command=self.login)
        self.button.pack(anchor="w")
        actions = tk.Frame(card.body, bg=COLORS["card"])
        actions.pack(anchor="w", pady=(6, 0))
        FlatButton(actions, "核对官方待支付订单", variant="secondary", command=self.open_orders).pack(side="left")
        FlatButton(actions, "结束浏览器会话", variant="ghost", command=self.logout).pack(side="left", padx=6)
        self.blocked = tk.Label(
            card.body,
            text="",
            bg=COLORS["card"],
            fg=COLORS["amber"],
            font=FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=760,
        )
        self.blocked.pack(fill="x", pady=(10, 0))

        note = Card(self, title="边界")
        note.pack(fill="x", pady=(14, 0))
        for line in (
            "不保存账号密码；扫码登录。",
            "不自动支付；支付始终在官方页面完成。",
            "登录会话只在本机内存；退出客户端即失效。",
            "登录页可能加载第三方滑块脚本，当前白名单会拦截并在下方提示。",
        ):
            tk.Label(
                note.body,
                text="•  " + line,
                bg=COLORS["card"],
                fg=COLORS["muted"],
                font=FONT_SMALL,
                anchor="w",
            ).pack(fill="x", pady=2)

    def login(self) -> None:
        if self.app.busy:
            messagebox.showinfo("登录", "已有任务在运行，请稍候。")
            return
        self.button.configure(state="disabled", text="等待扫码…")
        self.status.configure(
            text="已打开独立浏览器窗口；请在那个窗口扫码（不是你当前的普通 Edge 窗口）。",
            fg=COLORS["amber"],
        )
        self.app.record("LOGIN_START", "QR login window opened")

        def work():
            return self.app.session.login(self.app.stopping.is_set)

        def done(payload):
            ok, blocked, probe = payload
            if ok:
                self.status.configure(text="扫码已确认，预订和核对订单可沿用此窗口。关闭浏览器会结束会话。", fg=COLORS["success"])
                self.app.record("LOGIN_OK", "QR login success")
            else:
                self.status.configure(text="未完成扫码或已超时。", fg=COLORS["amber"])
                self.app.record("LOGIN_TIMEOUT", "QR login timeout")
            detail = ""
            if probe:
                detail = f"检测：checkUser={probe.get('check_user')}；cookie={','.join(probe.get('cookies') or []) or '无'}"
            self.blocked.configure(
                text=(("已拦截：" + ", ".join(blocked)) if blocked else "") + (("\n" + detail) if detail else "")
            )

        def failed(exc):
            self.status.configure(text=f"登录失败：{safe_exception(exc)}", fg=COLORS["danger"])
            self.app.record("LOGIN_ERROR", str(exc))

        self.app.run_async(
            work,
            done,
            failed,
            on_done=lambda: self.button.configure(state="normal", text="打开扫码登录"),
        )

    def open_orders(self) -> None:
        self.app.run_async(
            lambda: self.app.session.visible_browser().open_orders(),
            lambda _: self.status.configure(text="请在官方页面核对待支付订单和支付截止时间。", fg=COLORS["text"]),
            lambda exc: messagebox.showerror("订单", safe_exception(exc)),
        )

    def logout(self) -> None:
        def done(_):
            self.status.configure(text="本机浏览器会话已结束。", fg=COLORS["text"])
            self.blocked.configure(text="")

        self.app.run_async(self.app.session.close, done, lambda exc: messagebox.showerror("结束会话", safe_exception(exc)))


CHECK_LABELS = {
    "config": "配置文件可读",
    "dpapi_available": "DPAPI 可用",
    "dpapi_roundtrip": "DPAPI 加解密",
    "dpapi_no_plaintext": "密文不含明文",
    "state_roundtrip": "状态文件往返",
    "state_ciphertext": "状态文件为密文",
    "state_delete": "安全删除",
    "allowlist_12306": "允许 12306",
    "allowlist_block_http": "拦截 http://",
    "allowlist_block_third_party": "拦截第三方域名",
}


class SecurityPage(BasePage):
    def build(self) -> None:
        tk.Label(self, text="安全", bg=COLORS["bg"], fg=COLORS["text"], font=FONT_H1, anchor="w").pack(fill="x")
        tk.Label(
            self,
            text="本机自检；不联网、不上传。",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_LABEL,
            anchor="w",
        ).pack(fill="x", pady=(4, 16))

        card = Card(self, title="安全自检")
        card.pack(fill="x")
        FlatButton(card.body, "运行自检", command=self.run).pack(anchor="w")
        self.status = tk.Label(card.body, text="尚未运行。", bg=COLORS["card"], fg=COLORS["muted"], font=FONT_SMALL, anchor="w")
        self.status.pack(fill="x", pady=(8, 8))
        self.rows = tk.Frame(card.body, bg=COLORS["card"])
        self.rows.pack(fill="x")

        data = Card(self, title="本地数据")
        data.pack(fill="x", pady=(14, 0))
        path = str(data_dir(self.app.cfg))
        tk.Label(data.body, text=path, bg=COLORS["card"], fg=COLORS["text"], font=FONT_MONO, anchor="w").pack(fill="x")
        tk.Label(
            data.body,
            text="状态文件是 DPAPI 密文；身份证/手机号不落盘；日志/通知脱敏。",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
        ).pack(fill="x", pady=(6, 12))
        row = tk.Frame(data.body, bg=COLORS["card"])
        row.pack(fill="x")
        FlatButton(row, "打开目录", variant="secondary", command=self.app.open_data_dir).pack(side="left")
        FlatButton(row, "清理加密状态文件", variant="danger", command=self.clear_state).pack(side="left", padx=(8, 0))

        boundary = Card(self, title="购票与支付")
        boundary.pack(fill="x", pady=(14, 0))
        tk.Label(
            boundary.body,
            text="本人扫码登录 · 单次提交需要确认 · 付款在官方页面完成",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=760,
        ).pack(fill="x")

    def run(self) -> None:
        result = run_checks(self.app.cfg_path)
        for child in self.rows.winfo_children():
            child.destroy()
        for key, value in result["checks"].items():
            line = tk.Frame(self.rows, bg=COLORS["card"])
            line.pack(fill="x", pady=3)
            tk.Label(
                line,
                text="●",
                bg=COLORS["card"],
                fg=COLORS["success"] if value else COLORS["danger"],
                font=FONT_SMALL,
            ).pack(side="left")
            tk.Label(
                line,
                text=CHECK_LABELS.get(key, key),
                bg=COLORS["card"],
                fg=COLORS["text"],
                font=FONT_SMALL,
                anchor="w",
            ).pack(side="left", padx=(8, 0), fill="x", expand=True)
            tk.Label(
                line,
                text="PASS" if value else "FAIL",
                bg=COLORS["card"],
                fg=COLORS["success"] if value else COLORS["danger"],
                font=FONT_SMALL,
            ).pack(side="right")
        self.status.configure(
            text="全部通过。" if result["ok"] else "有检查未通过，见上方。",
            fg=COLORS["success"] if result["ok"] else COLORS["danger"],
        )
        self.app.record("SECURITY_CHECK", "PASS" if result["ok"] else "FAIL")

    def clear_state(self) -> None:
        if not messagebox.askyesno("清理加密状态文件", "确定删除保存在本机加密状态文件中的数据？", parent=self):
            return
        try:
            StateStore(state_path(self.app.cfg)).clear()
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("清理本机状态文件", safe_exception(exc), parent=self)
            return
        self.app.record("STATE_CLEAR", "local state deleted")
        messagebox.showinfo("安全", "本地状态已删除。")


class LogsPage(BasePage):
    def build(self) -> None:
        tk.Label(self, text="日志", bg=COLORS["bg"], fg=COLORS["text"], font=FONT_H1, anchor="w").pack(fill="x")
        tk.Label(
            self,
            text="本机事件时间线；只记录状态/延迟，不记录 Cookie、证件号、完整订单号。",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_LABEL,
            anchor="w",
        ).pack(fill="x", pady=(4, 16))

        card = Card(self, title="最近事件")
        card.pack(fill="both", expand=True)
        toolbar = tk.Frame(card.body, bg=COLORS["card"])
        toolbar.pack(fill="x", pady=(0, 8))
        FlatButton(toolbar, "刷新", variant="secondary", command=self.refresh).pack(side="left")
        self.status = tk.Label(toolbar, text="", bg=COLORS["card"], fg=COLORS["muted"], font=FONT_SMALL)
        self.status.pack(side="left", padx=(10, 0))

        columns = ("ts", "code", "message", "latency")
        headings = ("时间", "事件", "说明", "延迟")
        widths = (170, 150, 560, 80)
        self.tree = ttk.Treeview(card.body, columns=columns, show="headings", height=16)
        for column, heading, width in zip(columns, headings, widths):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)

    def refresh(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        try:
            store = EventStore(events_path(self.app.cfg))
            events = store.timeline("ui", limit=100)
            store.close()
        except Exception as exc:  # noqa: BLE001
            self.status.configure(text=f"读取失败：{exc}", fg=COLORS["danger"])
            return
        for event in events:
            self.tree.insert("", "end", values=(event.ts, event.code, event.message, event.latency_ms or ""))
        self.status.configure(text=f"{len(events)} 条" if events else "暂无事件；操作查票/登录/自检后会写入。")


class RailPilotApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"RailPilot {__version__} · 12306 购票助手")
        self.minsize(980, 680)
        apply_theme(self)
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(1180, max(980, screen_w - 80))
        height = min(900, max(680, screen_h - 80))
        left = max(0, (screen_w - width) // 2)
        top = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{left}+{top}")
        self.cfg_path = _find_config()
        self.cfg: dict = {}
        self.cfg_error = ""
        self.tasks: list[Task] = []
        self._load_config()
        self._queue: queue.Queue = queue.Queue()
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="railpilot-browser")
        self.session = DesktopSession()
        self.stopping = threading.Event()
        self.busy = False
        self._build_shell()
        self._show_page("journey" if self.pages["journey"].active else "query")
        self._tick()
        self.after(80, self._poll_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_config(self) -> None:
        try:
            self.cfg = load_config(self.cfg_path)
        except (ConfigError, OSError) as exc:
            self.cfg = {"tasks": [], "safety": {"max_manual_cancels_per_day": 2}}
            self.cfg_error = str(exc)
            return
        for index, raw in enumerate(self.cfg.get("tasks", [])):
            if isinstance(raw, dict):
                try:
                    self.tasks.append(Task.from_config(raw, index))
                except Exception as exc:  # noqa: BLE001
                    self.cfg_error = f"task {index}: {exc}"

    def _build_shell(self) -> None:
        sidebar = tk.Frame(self, bg=COLORS["sidebar"], width=220)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg=COLORS["sidebar"])
        brand.pack(fill="x", padx=20, pady=(24, 24))
        tk.Label(brand, text="RailPilot", bg=COLORS["sidebar"], fg=COLORS["text"], font=FONT_H1, anchor="w").pack(fill="x")
        tk.Label(
            brand,
            text="12306 私人助手",
            bg=COLORS["sidebar"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
        ).pack(fill="x", pady=(2, 10))
        Pill(brand, "LOCAL ONLY", "accent").pack(anchor="w")

        self.nav_buttons: dict[str, NavButton] = {}
        for key, label in NAV_ITEMS:
            button = NavButton(sidebar, label, command=lambda k=key: self.show_page(k))
            button.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = button

        bottom = tk.Frame(sidebar, bg=COLORS["sidebar"])
        bottom.pack(side="bottom", fill="x", padx=20, pady=18)
        tk.Label(bottom, text="●  数据仅本机", bg=COLORS["sidebar"], fg=COLORS["success"], font=FONT_SMALL, anchor="w").pack(fill="x")
        tk.Label(bottom, text=f"v{__version__} · 人工支付", bg=COLORS["sidebar"], fg=COLORS["muted"], font=FONT_SMALL, anchor="w").pack(
            fill="x", pady=(4, 0)
        )

        main = tk.Frame(self, bg=COLORS["bg"])
        main.pack(side="left", fill="both", expand=True)

        header = tk.Frame(main, bg=COLORS["bg"])
        header.pack(fill="x", padx=28, pady=(22, 10))
        left = tk.Frame(header, bg=COLORS["bg"])
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text="控制台", bg=COLORS["bg"], fg=COLORS["text"], font=FONT_TITLE, anchor="w").pack(fill="x")
        tk.Label(
            left,
            text="查票、候补、会话与安全，一处完成。",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
        ).pack(fill="x", pady=(2, 0))
        right = tk.Frame(header, bg=COLORS["bg"])
        right.pack(side="right")
        self.clock_label = tk.Label(right, text="", bg=COLORS["bg"], fg=COLORS["muted"], font=FONT_SMALL)
        self.clock_label.pack(side="right", padx=(12, 0))
        Pill(right, "●  数据仅本机", "success").pack(side="right")

        content_shell = tk.Frame(main, bg=COLORS["bg"])
        content_shell.pack(fill="both", expand=True, padx=28, pady=(4, 18))
        self.content_canvas = tk.Canvas(content_shell, bg=COLORS["bg"], highlightthickness=0)
        self.content_canvas.pack(side="left", fill="both", expand=True)
        content_scroll = ttk.Scrollbar(content_shell, orient="vertical", command=self.content_canvas.yview)
        content_scroll.pack(side="right", fill="y")
        self.content_canvas.configure(yscrollcommand=content_scroll.set)
        self.content_inner = tk.Frame(self.content_canvas, bg=COLORS["bg"])
        self._content_window = self.content_canvas.create_window((0, 0), window=self.content_inner, anchor="nw")
        self.content_inner.grid_rowconfigure(0, weight=1)
        self.content_inner.grid_columnconfigure(0, weight=1)
        self.content_inner.bind(
            "<Configure>",
            lambda _e: self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all")),
        )
        self.content_canvas.bind(
            "<Configure>",
            lambda e: self.content_canvas.itemconfigure(self._content_window, width=e.width),
        )
        self.content_canvas.bind(
            "<MouseWheel>",
            lambda e: self.content_canvas.yview_scroll(int(-e.delta / 120), "units"),
        )

        self.pages: dict[str, BasePage] = {
            "dashboard": DashboardPage(self),
            "query": QueryPage(self),
            "journey": JourneyPage(self),
            "waitlist": WaitlistPage(self),
            "login": LoginPage(self),
            "security": SecurityPage(self),
            "logs": LogsPage(self),
        }
        self.waitlist_page = self.pages["waitlist"]  # type: ignore[assignment]
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()

        footer = tk.Frame(main, bg=COLORS["bg"])
        footer.pack(fill="x", padx=28, pady=(0, 12))
        tk.Label(
            footer,
            text=f"配置：{self.cfg_path}",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
        ).pack(side="left")
        if self.cfg_error:
            tk.Label(footer, text=self.cfg_error, bg=COLORS["bg"], fg=COLORS["danger"], font=FONT_SMALL, anchor="e").pack(
                side="right"
            )

    def _show_page(self, name: str) -> None:
        try:
            self.content_canvas.yview_moveto(0)
        except Exception:
            pass
        for key, page in self.pages.items():
            if key == name:
                page.grid()
                page.tkraise()
                page.refresh()
                self.content_canvas.yview_moveto(0)
                self.nav_buttons[key].set_active(True)
            else:
                page.grid_remove()
                self.nav_buttons[key].set_active(False)

    def show_page(self, name: str) -> None:
        self._show_page(name)

    def _on_wheel(self, event) -> None:
        try:
            self.content_canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    def _tick(self) -> None:
        self.clock_label.configure(text=time.strftime("%Y-%m-%d  %H:%M:%S"))
        self.pages["journey"].tick(time.monotonic())
        self.after(1000, self._tick)

    def run_async(self, fn, on_success, on_error, on_done=None) -> bool:
        if self.busy:
            messagebox.showinfo("RailPilot", "已有任务在运行，请稍候。")
            return False
        self.busy = True

        def runner():
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                self._queue.put((exc, on_error, on_done))
            else:
                self._queue.put((result, on_success, on_done))

        self._worker.submit(runner)
        return True

    def _poll_queue(self) -> None:
        try:
            payload, callback, on_done = self._queue.get_nowait()
        except queue.Empty:
            self.after(80, self._poll_queue)
            return
        try:
            callback(payload)
        finally:
            self.busy = False
            if on_done is not None:
                try:
                    on_done()
                except Exception:
                    pass
            self.after(80, self._poll_queue)

    def record(self, code: str, message: str) -> None:
        try:
            store = EventStore(events_path(self.cfg))
            store.append(Event(ts=utc_now_iso(), task_id="ui", code=code, message=message))
            store.close()
        except Exception:
            pass

    def run_doctor(self) -> None:
        self.show_page("security")
        self.pages["security"].run()  # type: ignore[attr-defined]

    def run_clock(self) -> None:
        from ..core.clock import calibrate

        def work():
            return calibrate(
                url=str(self.cfg.get("timing", {}).get("sync_url", "https://www.12306.cn")),
                samples=int(self.cfg.get("timing", {}).get("sync_samples", 5)),
            )

        def done(state):
            self.record("CLOCK_OK", f"offset={state.offset_ms}ms rtt={state.min_rtt_ms}ms")
            messagebox.showinfo("校时", f"偏移 {state.offset_ms} ms\n最小 RTT {state.min_rtt_ms} ms")

        if self.run_async(work, done, lambda exc: messagebox.showerror("校时", safe_exception(exc))):
            self.record("CLOCK_START", "calibrating")

    def open_data_dir(self) -> None:
        try:
            path = data_dir(self.cfg)
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打开目录", str(exc))

    def _on_close(self) -> None:
        if self.busy and not messagebox.askyesno("退出", "还有任务在运行，确定退出？"):
            return
        self.destroy()

    def destroy(self) -> None:
        self.stopping.set()
        self.pages["journey"].stop.set()
        if self.busy or self.session.has_session:
            self._worker.submit(self.session.close)
        self._worker.shutdown(wait=False)
        super().destroy()


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    enable_dpi_awareness()
    app = RailPilotApp()
    if "--smoke" in args:
        app.withdraw()
        app.after(700, app.destroy)
        app.mainloop()
        return 0
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
