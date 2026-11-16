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
import webbrowser
from datetime import date, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

from ..config import ConfigError, events_path, load_config, state_path
from ..core.waitlist import build_plan
from ..models import Candidate, Event, Task, utc_now_iso
from ..rail.adapter import PlaywrightAdapter, validate_presale_date
from ..security.doctor import run_checks
from ..security.files import app_data_dir
from ..store.events import EventStore
from ..store.state import StateStore
from .theme import COLORS, FONT_H1, FONT_H2, FONT_LABEL, FONT_MONO, FONT_SMALL, FONT_TITLE, FONT_UI, apply_theme
from .widgets import Card, FlatButton, NavButton, Pill, StatCard, divider

NAV_ITEMS = [
    ("dashboard", "总览"),
    ("query", "查票"),
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
            text="先把时间和候选算准，再把提交和候补交给系统；真实下单默认关闭。",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_LABEL,
            anchor="w",
        ).pack(fill="x", pady=(4, 16))

        stats = tk.Frame(self, bg=COLORS["bg"])
        stats.pack(fill="x")
        self.stat_tasks = StatCard(stats, "任务", "0", "配置中的行程")
        self.stat_candidates = StatCard(stats, "候选", "0", "日期 × 车次 × 席别")
        self.stat_cancel = StatCard(stats, "今日取消", "0/2", "留一次余量")
        self.stat_security = StatCard(stats, "安全", "DPAPI", "会话默认不落盘")
        for index, card in enumerate(
            (self.stat_tasks, self.stat_candidates, self.stat_cancel, self.stat_security)
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
            ("order", "真实下单未启用"),
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
        max_cancel = int(self.app.cfg.get("safety", {}).get("max_manual_cancels_per_day", 2))
        self.stat_cancel.set(f"0/{max_cancel}", "留一次余量")
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
            text="官方余票页，真实浏览器；只读查询，不登录、不下单。",
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
            text="无头浏览器",
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
        FlatButton(toolbar, "打开官方页", variant="ghost", command=self.open_official).pack(side="right", padx=(0, 8))

        columns = ("date", "train", "dep", "arr", "second", "first", "business", "status")
        headings = ("日期", "车次", "出发", "到达", "二等座", "一等座", "商务座", "状态")
        widths = (96, 80, 64, 64, 80, 80, 80, 110)
        self.tree = ttk.Treeview(results.body, columns=columns, show="headings", height=14)
        for column, heading, width in zip(columns, headings, widths):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, anchor="center")
        self.tree.tag_configure("odd", background=COLORS["card2"])
        scroll = ttk.Scrollbar(results.body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.results: list[Candidate] = []

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
        self.query_button.configure(state="disabled", text="查询中…")
        self.status.configure(text="正在打开官方页面并查询，请稍候…", fg=COLORS["amber"])
        self.app.record("QUERY_START", f"{date_text} {from_text}->{to_text}")

        def work():
            adapter = PlaywrightAdapter(headless=headless)
            try:
                adapter.open()
                return adapter.query(date_text, from_text, to_text), list(adapter.blocked_hosts)
            finally:
                adapter.close()

        def done(payload):
            results, blocked = payload
            self.results = results
            self.populate()
            note = f"共 {len(results)} 条" + (f"；已拦截 {', '.join(blocked)}" if blocked else "")
            self.status.configure(text=note, fg=COLORS["success"] if results else COLORS["amber"])
            self.app.record("QUERY_OK", note)

        def failed(exc):
            self.status.configure(text=f"查询失败：{exc}", fg=COLORS["danger"])
            self.app.record("QUERY_ERROR", str(exc))

        self.app.run_async(work, done, failed, on_done=lambda: self.query_button.configure(state="normal", text="查询余票"))

    def populate(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        needle = self.filter_var.get().strip().lower()
        grouped: dict[tuple[str, str, str, str], dict[str, str]] = {}
        for candidate in self.results:
            if needle and needle not in candidate.train_no.lower():
                continue
            key = (candidate.date, candidate.train_no, candidate.depart_time, candidate.arrive_time)
            row = grouped.setdefault(key, {})
            row[candidate.seat] = candidate.available
        shown = 0
        for key, seats in sorted(grouped.items()):
            date_text, train_no, depart, arrive = key
            status = "可订" if any(value and value not in ("无", "候补") for value in seats.values()) else "—"
            self.tree.insert(
                "",
                "end",
                values=(
                    date_text,
                    train_no,
                    depart,
                    arrive,
                    seats.get("二等座", "—") or "—",
                    seats.get("一等座", "—") or "—",
                    seats.get("商务座", "—") or "—",
                    status,
                ),
                tags=("odd",) if shown % 2 else (),
            )
            shown += 1
        self.count_label.configure(text=f"显示 {shown} 趟 / {len(self.results)} 条席别")

    def to_waitlist(self) -> None:
        if not self.results:
            messagebox.showinfo("候补", "先查询余票，或直接在候补页使用配置任务。")
            return
        self.app.query_results = list(self.results)
        self.app.show_page("waitlist")
        self.app.waitlist_page.set_source("query")

    def open_official(self) -> None:
        webbrowser.open("https://kyfw.12306.cn/otn/leftTicket/init")


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
        self.cutoff_var = tk.StringVar()
        _field(row, "截止兑现（ISO8601）", self.cutoff_var, width=30).pack(side="left", padx=(0, 12))
        FlatButton(row, "生成候补计划", command=self.generate).pack(side="left", pady=(18, 0))
        FlatButton(row, "打开官方页", variant="ghost", command=self.open_official).pack(side="left", padx=(8, 0), pady=(18, 0))
        self.source_label = tk.Label(
            form.body,
            text="数据源：配置任务",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
        )
        self.source_label.pack(fill="x", pady=(10, 0))
        self.status = tk.Label(
            form.body,
            text="官方候补提交仍需你在 12306 页面确认；预付款 30 分钟内完成。",
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
        self._source = "config"

    def set_source(self, source: str) -> None:
        self._source = source
        self.source_label.configure(text="数据源：当前查票结果" if source == "query" else "数据源：配置任务")
        self.refresh()

    def refresh(self) -> None:
        names = [task.name for task in self.app.tasks]
        self.task_combo.configure(values=names)
        if names and not self.task_var.get():
            self.task_var.set(names[0])
            self._set_default_cutoff(0)

    def _set_default_cutoff(self, index: int) -> None:
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
        candidates = self.app.query_results if self._source == "query" and self.app.query_results else task.candidates
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
            text=f"已生成 {len(plan.orders)} 单 / {plan.total_combos} 组合；请到官方页确认提交。",
            fg=COLORS["success"],
        )
        self.app.record("WAITLIST_PLAN", f"orders={len(plan.orders)} combos={plan.total_combos}")

    def open_official(self) -> None:
        webbrowser.open("https://kyfw.12306.cn/otn/leftTicket/init")


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
            adapter = PlaywrightAdapter(headless=False)
            try:
                adapter.open()
                ok = adapter.login_qr(timeout_s=180)
                probe = adapter.login_probe()
                return ok, list(adapter.blocked_hosts), probe
            finally:
                adapter.close()

        def done(payload):
            ok, blocked, probe = payload
            # checkUser is the authoritative signal; a timeout boundary can
            # otherwise leave ok=False while the official session is valid.
            if not ok and probe and probe.get("check_user") is True:
                ok = True
            if ok:
                self.status.configure(text="已登录（会话仅内存，退出即失效）。", fg=COLORS["success"])
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
            self.status.configure(text=f"登录失败：{exc}", fg=COLORS["danger"])
            self.app.record("LOGIN_ERROR", str(exc))

        self.app.run_async(
            work,
            done,
            failed,
            on_done=lambda: self.button.configure(state="normal", text="打开扫码登录"),
        )


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
        path = str(app_data_dir())
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
        FlatButton(row, "清理登录状态", variant="danger", command=self.clear_state).pack(side="left", padx=(8, 0))

        boundary = Card(self, title="不做")
        boundary.pack(fill="x", pady=(14, 0))
        tk.Label(
            boundary.body,
            text="不保存密码 · 不自动支付 · 不多账号 · 不代理 · 不验证码识别 · 不自动下单",
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
        if not messagebox.askyesno("清理登录状态", "删除本机 DPAPI 状态文件？这会清除任务/会话，不会影响 12306 账号。", parent=self):
            return
        StateStore(state_path(self.app.cfg)).clear()
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
        self.title("RailPilot · 12306 私人购票助手")
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
        self.query_results: list[Candidate] = []
        self._load_config()
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self.busy = False
        self._build_shell()
        self._show_page("dashboard")
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
        tk.Label(bottom, text="v0.1 · 真实下单未启用", bg=COLORS["sidebar"], fg=COLORS["muted"], font=FONT_SMALL, anchor="w").pack(
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
        self.after(1000, self._tick)

    def run_async(self, fn, on_success, on_error, on_done=None) -> None:
        if self.busy:
            messagebox.showinfo("RailPilot", "已有任务在运行，请稍候。")
            return
        self.busy = True

        def runner():
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("error", exc, on_error, on_done))
            else:
                self._queue.put(("ok", result, on_success, on_done))

        self._worker = threading.Thread(target=runner, daemon=True)
        self._worker.start()

    def _poll_queue(self) -> None:
        try:
            kind, payload, callback, on_done = self._queue.get_nowait()
        except queue.Empty:
            self.after(80, self._poll_queue)
            return
        try:
            if kind == "ok":
                callback(payload)
            else:
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

        self.record("CLOCK_START", "calibrating")
        try:
            state = calibrate(
                url=str(self.cfg.get("timing", {}).get("sync_url", "https://www.12306.cn")),
                samples=int(self.cfg.get("timing", {}).get("sync_samples", 5)),
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("校时", f"失败：{exc}")
            return
        self.record("CLOCK_OK", f"offset={state.offset_ms}ms rtt={state.min_rtt_ms}ms")
        messagebox.showinfo("校时", f"偏移 {state.offset_ms} ms\n最小 RTT {state.min_rtt_ms} ms")

    def open_data_dir(self) -> None:
        try:
            os.startfile(str(app_data_dir()))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打开目录", str(exc))

    def _on_close(self) -> None:
        if self.busy and not messagebox.askyesno("退出", "还有任务在运行，确定退出？"):
            return
        self.destroy()


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
