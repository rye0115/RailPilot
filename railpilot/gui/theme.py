"""Dark, low-noise theme for the Tkinter client."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

COLORS = {
    "bg": "#0B1218",
    "sidebar": "#101B24",
    "card": "#15222D",
    "card2": "#1A2A37",
    "border": "#263A4A",
    "text": "#E8F0F7",
    "muted": "#8FA6B8",
    "accent": "#4FD1C5",
    "accent_dark": "#1F6F68",
    "amber": "#F6AD55",
    "danger": "#F56565",
    "success": "#48BB78",
    "info": "#63B3ED",
    "selection": "#23404A",
}

FONT_FAMILY = "Microsoft YaHei UI"
FONT_UI = (FONT_FAMILY, 10)
FONT_SMALL = (FONT_FAMILY, 9)
FONT_LABEL = (FONT_FAMILY, 9)
FONT_TITLE = (FONT_FAMILY, 20, "bold")
FONT_H1 = (FONT_FAMILY, 15, "bold")
FONT_H2 = (FONT_FAMILY, 12, "bold")
FONT_NUM = (FONT_FAMILY, 24, "bold")
FONT_MONO = ("Consolas", 9)


def apply_theme(root: tk.Misc) -> None:
    root.configure(bg=COLORS["bg"])
    try:
        root.option_add("*Font", FONT_UI)
    except tk.TclError:
        pass

    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
        try:
            tkfont.nametofont(name).configure(family=FONT_FAMILY, size=10)
        except tk.TclError:
            pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Card.TFrame", background=COLORS["card"])
    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
    style.configure("Card.TLabel", background=COLORS["card"], foreground=COLORS["text"])
    style.configure("CardMuted.TLabel", background=COLORS["card"], foreground=COLORS["muted"])
    style.configure("CardTitle.TLabel", background=COLORS["card"], foreground=COLORS["text"], font=FONT_H2)

    style.configure(
        "Treeview",
        background=COLORS["card"],
        fieldbackground=COLORS["card"],
        foreground=COLORS["text"],
        borderwidth=0,
        rowheight=30,
    )
    style.configure(
        "Treeview.Heading",
        background=COLORS["card2"],
        foreground=COLORS["muted"],
        borderwidth=0,
        font=FONT_SMALL,
        padding=(8, 6),
    )
    style.map("Treeview", background=[("selected", COLORS["selection"])])
    style.map("Treeview.Heading", background=[("active", COLORS["card2"])])

    style.configure(
        "TCombobox",
        fieldbackground=COLORS["card2"],
        background=COLORS["card2"],
        foreground=COLORS["text"],
        arrowcolor=COLORS["muted"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["border"],
        darkcolor=COLORS["border"],
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", COLORS["card2"])],
        foreground=[("readonly", COLORS["text"])],
    )
    style.configure(
        "Vertical.TScrollbar",
        background=COLORS["card2"],
        troughcolor=COLORS["bg"],
        bordercolor=COLORS["bg"],
        arrowcolor=COLORS["muted"],
    )
    style.configure("TCheckbutton", background=COLORS["card"], foreground=COLORS["text"])
    style.map("TCheckbutton", background=[("active", COLORS["card"])])
