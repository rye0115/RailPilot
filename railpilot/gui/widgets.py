"""Reusable Tkinter widgets for the RailPilot desktop UI."""

from __future__ import annotations

import tkinter as tk

from .theme import COLORS, FONT_H2, FONT_LABEL, FONT_NUM, FONT_SMALL, FONT_UI


class Card(tk.Frame):
    def __init__(self, master, title: str = "", pad: int = 16, **kwargs):
        super().__init__(
            master,
            bg=COLORS["card"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            bd=0,
            **kwargs,
        )
        self.body = tk.Frame(self, bg=COLORS["card"])
        if title:
            header = tk.Frame(self, bg=COLORS["card"])
            header.pack(fill="x", padx=pad, pady=(pad, 0))
            tk.Label(
                header,
                text=title,
                bg=COLORS["card"],
                fg=COLORS["text"],
                font=FONT_H2,
                anchor="w",
            ).pack(side="left")
            self.body.pack(fill="both", expand=True, padx=pad, pady=(8, pad))
        else:
            self.body.pack(fill="both", expand=True, padx=pad, pady=pad)


class StatCard(Card):
    def __init__(self, master, label: str, value: str = "—", sub: str = ""):
        super().__init__(master, pad=14)
        self.label = tk.Label(
            self.body,
            text=label,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=FONT_LABEL,
            anchor="w",
        )
        self.label.pack(fill="x")
        self.value = tk.Label(
            self.body,
            text=value,
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=FONT_NUM,
            anchor="w",
        )
        self.value.pack(fill="x", pady=(2, 0))
        self.sub = tk.Label(
            self.body,
            text=sub,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
        )
        self.sub.pack(fill="x")

    def set(self, value: str, sub: str = "") -> None:
        self.value.configure(text=value)
        if sub:
            self.sub.configure(text=sub)


class FlatButton(tk.Button):
    def __init__(self, master, text: str, command=None, variant: str = "primary", **kwargs):
        palette = {
            "primary": (COLORS["accent"], "#07110F", COLORS["accent_dark"]),
            "secondary": (COLORS["card2"], COLORS["text"], COLORS["border"]),
            "danger": (COLORS["danger"], "#1A0B0B", "#9B2C2C"),
            "ghost": (COLORS["card"], COLORS["muted"], COLORS["card2"]),
        }
        bg, fg, active = palette.get(variant, palette["primary"])
        super().__init__(
            master,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=active,
            activeforeground=fg,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=14,
            pady=7,
            cursor="hand2",
            font=FONT_UI,
            **kwargs,
        )
        self._bg = bg
        self._hover = active
        self.bind("<Enter>", lambda _e: self.configure(bg=self._hover))
        self.bind("<Leave>", lambda _e: self.configure(bg=self._bg))


class NavButton(tk.Button):
    def __init__(self, master, text: str, command=None, **kwargs):
        super().__init__(
            master,
            text=text,
            command=command,
            bg=COLORS["sidebar"],
            fg=COLORS["muted"],
            activebackground=COLORS["card"],
            activeforeground=COLORS["text"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            anchor="w",
            padx=18,
            pady=10,
            cursor="hand2",
            font=FONT_UI,
            **kwargs,
        )
        self._active = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _on_enter(self, _event) -> None:
        if not self._active:
            self.configure(bg=COLORS["card"])

    def _on_leave(self, _event) -> None:
        if not self._active:
            self.configure(bg=COLORS["sidebar"])

    def set_active(self, active: bool) -> None:
        self._active = active
        self.configure(
            bg=COLORS["card"] if active else COLORS["sidebar"],
            fg=COLORS["accent"] if active else COLORS["muted"],
        )


class Pill(tk.Label):
    def __init__(self, master, text: str, tone: str = "accent", **kwargs):
        fg = COLORS.get(tone, COLORS["accent"])
        super().__init__(
            master,
            text=text,
            bg=COLORS["card2"],
            fg=fg,
            font=FONT_SMALL,
            padx=10,
            pady=4,
            bd=0,
            **kwargs,
        )


def divider(master) -> tk.Frame:
    frame = tk.Frame(master, bg=COLORS["border"], height=1)
    frame.pack(fill="x", pady=10)
    return frame
