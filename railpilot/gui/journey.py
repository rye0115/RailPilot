"""Saved alternatives, booking consent and recoverable order status."""

from copy import deepcopy
from datetime import datetime
import queue
from threading import Event
import tkinter as tk
from tkinter import messagebox, ttk

from ..config import data_dir
from ..core.booking import BookingService
from ..core.journey import CN, Journey, passenger_names
from ..core.planner import parse_hhmm
from ..security.redact import safe_exception
from ..store.journeys import JourneyStore
from .theme import COLORS, FONT_H1, FONT_LABEL, FONT_SMALL
from .widgets import Card, FlatButton


class JourneyPage(tk.Frame):
    def __init__(self, app):
        super().__init__(app.content_inner, bg=COLORS["bg"])
        self.app = app
        self.store = JourneyStore(data_dir(app.cfg) / "journeys.db")
        self.service = BookingService(app.session, self.store)
        self.trip = Journey()
        self.revision = 0
        self.live = {}
        self.checked = ""
        self.stop = Event()
        self.progress = queue.SimpleQueue()
        self.deadline = None
        self.active = None
        self.next_check = 0
        self.names = tk.StringVar()
        self.latest = tk.StringVar()
        self.minutes = tk.StringVar(value="10")
        self.note = tk.StringVar()
        self.order = tk.StringVar()
        self.countdown = tk.StringVar()
        self._build()
        self.reload()

    def _build(self):
        tk.Label(self, text="我的行程", font=FONT_H1, fg=COLORS["text"], bg=COLORS["bg"]).pack(anchor="w")
        card = Card(self, title="可接受的车次和席别")
        card.pack(fill="x", pady=(8, 0))
        bar = tk.Frame(card.body, bg=COLORS["card"])
        bar.pack(fill="x")
        for label, cmd in (("去查票添加", lambda: self.app.show_page("query")), ("上移", lambda: self.move(-1)),
                           ("下移", lambda: self.move(1)), ("移除", self.remove), ("刷新余票", self.inventory)):
            FlatButton(bar, label, command=cmd, variant="secondary").pack(side="left", padx=(0, 5))
        self.tree = ttk.Treeview(card.body, columns=("route", "seat", "availability"), show="headings",
                                 height=5, selectmode="browse")
        for name, title, width in (("route", "按优先顺序的行程", 275), ("seat", "席别", 65), ("availability", "余票与条件", 190)):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, minwidth=55)
        self.tree.pack(fill="x", pady=8)
        fields = tk.Frame(card.body, bg=COLORS["card"])
        fields.pack(fill="x")
        for label, var, width in (("乘车人（逗号分隔）", self.names, 23), ("当日最晚到达", self.latest, 7)):
            tk.Label(fields, text=label, bg=COLORS["card"], fg=COLORS["text"], font=FONT_SMALL).pack(side="left")
            ttk.Entry(fields, textvariable=var, width=width).pack(side="left", padx=(3, 9))
        FlatButton(fields, "保存", command=self.save, variant="secondary").pack(side="left")
        FlatButton(fields, "重新载入", command=self.reload, variant="ghost").pack(side="left")
        for var in (self.names, self.latest):
            var.trace_add("write", lambda *_: self.draw())
        choices = tk.Frame(card.body, bg=COLORS["card"])
        choices.pack(fill="x", pady=(8, 0))
        FlatButton(choices, "跟踪手动预订", command=self.manual).pack(side="left")
        tk.Label(choices, text="等待（分钟）", bg=COLORS["card"], fg=COLORS["text"], font=FONT_SMALL).pack(side="left", padx=(10, 0))
        ttk.Entry(choices, textvariable=self.minutes, width=4).pack(side="left")
        FlatButton(choices, "有票时提交一次", command=self.watch, variant="secondary").pack(side="left", padx=5)
        self.stop_button = FlatButton(choices, "停止等待", command=self.stop.set, variant="ghost")
        self.stop_button.pack(side="left")
        self.stop_button.configure(state="disabled")
        self._label(card.body, self.note)
        orders = Card(self, title="订单与支付期限")
        orders.pack(fill="x", pady=10)
        self._label(orders.body, self.order)
        self._label(orders.body, self.countdown)
        actions = tk.Frame(orders.body, bg=COLORS["card"])
        actions.pack(fill="x")
        FlatButton(actions, "核对订单", command=self.check).pack(side="left")
        FlatButton(actions, "打开官方订单", variant="secondary", command=self.official_orders).pack(side="left", padx=5)
        FlatButton(actions, "结束跟踪", variant="ghost", command=self.finish).pack(side="left")

    @staticmethod
    def _label(parent, variable):
        tk.Label(parent, textvariable=variable, anchor="w", justify="left", wraplength=680,
                 bg=COLORS["card"], fg=COLORS["text"], font=FONT_LABEL).pack(fill="x", pady=4)

    def refresh(self):
        self.sync_active()
        self.draw()

    def sync_active(self):
        try:
            current = self.store.current()
        except Exception as exc:
            self.order.set(safe_exception(exc))
            self.deadline = None
            self.active = None
            return
        if current == self.active and self.order.get():
            return
        self.active = current
        self.deadline = None
        self.countdown.set("")
        if current:
            _, intent = current
            c = intent.candidate
            self.order.set(f"待核实 · {c.date} {c.train_no} {c.from_station} → {c.to_station} · {c.seat} · {len(intent.passengers)} 人")
        else:
            self.order.set("尚无待核实的预订。")

    def reload(self):
        try:
            self.revision, self.trip = self.store.load()
        except Exception as exc:
            self.note.set(safe_exception(exc))
            return
        self.names.set("，".join(self.trip.passengers))
        self.latest.set(self.trip.latest)
        self.note.set("从查票结果中添加可接受的席别，再填写乘车人。" if not self.trip.choices else "已载入本机保存的行程。")
        self.refresh()

    def _fields(self, trip, require_people=False):
        text = self.names.get().strip()
        trip.passengers = passenger_names(text) if text or require_people else []
        trip.latest = self.latest.get().strip()
        if trip.latest and parse_hhmm(trip.latest) is None:
            raise ValueError("最晚到达时间请按 HH:MM 填写，例如 20:30。")
        return trip

    def persist(self, trip):
        self.revision = self.store.save(trip, self.revision)
        self.trip = trip
        self.draw()

    def save(self):
        try:
            self.persist(self._fields(deepcopy(self.trip)))
            self.note.set("行程已加密保存在本机。")
        except Exception as exc:
            self.note.set(safe_exception(exc))

    def add_choices(self, candidates):
        trip = self._fields(deepcopy(self.trip))
        for c in candidates:
            trip.add(c)
        self.persist(trip)
        self.live.update((c.key, c) for c in candidates)
        self.checked = "来自查票结果"
        self.note.set("已保存所选席别。列表从上到下依次尝试。")
        self.draw()

    def draw(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        try:
            trip = self._fields(deepcopy(self.trip))
            field_error = ""
        except ValueError as exc:
            trip, field_error = self.trip, str(exc)
        for i, c in enumerate(trip.choices):
            _, reason = trip.availability(c, self.live.get(c.key))
            self.tree.insert("", "end", iid=str(i),
                             values=(f"{c.date}  {c.train_no}  {c.from_station} → {c.to_station}", c.seat,
                                     field_error or (reason if self.checked else "待刷新")))
        if trip.choices:
            self.tree.selection_set(selected[0] if selected and self.tree.exists(selected[0]) else "0")

    def _edit(self, delta):
        selection = self.tree.selection()
        if not selection:
            return
        index = int(selection[0])
        try:
            trip = self._fields(deepcopy(self.trip))
            if delta is None:
                trip.choices.pop(index)
            else:
                target = index + delta
                if not 0 <= target < len(trip.choices):
                    return
                trip.choices[index], trip.choices[target] = trip.choices[target], trip.choices[index]
            self.persist(trip)
            if delta is not None:
                self.tree.selection_set(str(target))
        except Exception as exc:
            self.note.set(safe_exception(exc))

    def move(self, delta):
        self._edit(delta)

    def remove(self):
        self._edit(None)

    def _snapshot(self, require_people=True):
        if not self.trip.choices:
            raise ValueError("请先在查票页添加至少一个车次和席别。")
        trip = self._fields(deepcopy(self.trip), require_people)
        self.persist(trip)
        return trip

    def _run(self, work, done, wait=False):
        def failed(exc):
            self.note.set(safe_exception(exc))
            self.sync_active()
        if not self.app.busy:
            self.stop.clear()
        if self.app.run_async(work, done, failed, on_done=lambda: self.stop_button.configure(state="disabled")):
            self.note.set("正在办理，请稍候。")
            if wait:
                self.stop_button.configure(state="normal")

    def inventory(self):
        try:
            trip = self._snapshot(False)
        except Exception as exc:
            self.note.set(safe_exception(exc))
            return
        def done(live):
            self.live = live
            self.checked = datetime.now(CN).strftime("%H:%M:%S")
            self.note.set(f"余票更新于 {self.checked}。")
            self.draw()
        self._run(lambda: self.service.inventory(trip, self.stop), done, True)

    def _after_booking(self, message):
        self.sync_active()
        self.note.set(message)
        self.next_check = 0

    def manual(self):
        try:
            trip = self._snapshot()
            candidate = trip.choices[int(self.tree.selection()[0])]
        except Exception as exc:
            self.note.set(safe_exception(exc))
            return
        self._run(lambda: self.service.manual(candidate, trip), self._after_booking)

    def watch(self):
        if self.app.busy:
            return
        try:
            trip = self._snapshot()
            minutes = int(self.minutes.get())
            if not 1 <= minutes <= 180:
                raise ValueError("等待时长须为 1 至 180 分钟。")
        except Exception as exc:
            self.note.set(safe_exception(exc))
            return
        summary = "\n".join(f"{c.date} · {c.train_no} · {c.from_station} → {c.to_station} · {c.seat}" for c in trip.choices)
        confirm = (f"乘车人：{'、'.join(trip.passengers)}（成人票）\n最晚到达：{trip.latest or '不限'}\n"
                   f"{summary}\n\n在接下来的 {minutes} 分钟内按以上顺序查询。有票时尝试提交一笔真实订单，付款由你完成。")
        if messagebox.askokcancel("确认本次单次占座", confirm, parent=self, default="cancel"):
            self._run(lambda: self.service.watch(trip, minutes, self.stop, self.progress.put), self._after_booking, True)

    def check(self):
        self.sync_active()
        if not self.active:
            return
        def done(result):
            message, deadline = result
            if message.startswith("已占座"):
                message += f" · 核对于 {datetime.now(CN):%H:%M:%S}"
                if not deadline:
                    message += " · 支付期限请查看官方订单"
            self.order.set(message)
            self.deadline = datetime.fromisoformat(deadline) if deadline else None
        def failed(exc):
            self.deadline = None
            self.order.set(f"待核实 · {safe_exception(exc)}")
        self.app.run_async(self.service.reconcile, done, failed)

    def tick(self, now):
        while not self.progress.empty():
            self.note.set(self.progress.get_nowait())
        if self.deadline:
            seconds = max(0, int((self.deadline - datetime.now(CN)).total_seconds()))
            remain = f"剩余 {seconds // 60} 分 {seconds % 60:02d} 秒" if seconds else "已到截止时间，请核对官方订单"
            self.countdown.set(f"官方支付期限 {self.deadline:%m-%d %H:%M} · {remain}")
            if seconds == 0:
                self.order.set("支付期限已到，请在 12306 核对最新状态。")
        else:
            self.countdown.set("")
        if self.active and self.app.session.has_session and not self.app.busy and now >= self.next_check:
            self.next_check = now + 45
            self.check()

    def official_orders(self):
        self._run(lambda: self.app.session.visible_browser().open_orders(),
                  lambda _: self.note.set("已打开官方订单页面。"))

    def finish(self):
        if self.app.busy:
            self.note.set("请等当前操作结束后再结束跟踪。")
            return
        if self.active and messagebox.askyesno("结束跟踪", "已在 12306 核对结果？结束本机跟踪不会取消官方订单。", parent=self):
            try:
                self.store.finish(self.active[0])
                self.sync_active()
            except Exception as exc:
                self.note.set(safe_exception(exc))
