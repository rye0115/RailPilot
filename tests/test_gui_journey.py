from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from unittest.mock import Mock

import pytest

from railpilot.core.journey import CN
from railpilot.gui.app import RailPilotApp
from railpilot.gui.widgets import FlatButton
from railpilot.models import Candidate


def test_choose_seat_save_restart_and_cancel_consent(tmp_path, monkeypatch):
    monkeypatch.setenv("RAILPILOT_DATA_DIR", str(tmp_path))
    sample = Path(__file__).resolve().parents[1] / "design" / "config.example.yaml"
    monkeypatch.setattr("railpilot.gui.app._find_config", lambda: sample)
    try:
        app = RailPilotApp()
    except tk.TclError as exc:
        if "display" in str(exc).lower():
            pytest.skip("Tk display unavailable")
        raise
    try:
        app.withdraw()
        query = app.pages["query"]
        day = (datetime.now(CN).date() + timedelta(days=1)).isoformat()
        query.results = [Candidate(day, "G1", "北京南", "上海虹桥", seat, available="2")
                         for seat in ("一等座", "二等座")]
        query.populate()
        query.tree.selection_set(query.tree.get_children()[0])
        query.add_to_journey()
        dialog = next(w for w in query.winfo_children() if isinstance(w, tk.Toplevel))
        next(w for w in dialog.winfo_children() if isinstance(w, ttk.Checkbutton) and "二等座" in w["text"]).invoke()
        next(w for w in dialog.winfo_children() if isinstance(w, FlatButton)).invoke()
        journey = app.pages["journey"]
        assert [c.seat for c in journey.trip.choices] == ["二等座"]
        journey.names.set("乘客甲，乘客乙")
        journey.save()
        assert journey.trip.passengers == ["乘客甲", "乘客乙"]
        assert "需 2 张" in journey.tree.set("0", "availability")
        start = Mock()
        monkeypatch.setattr(journey, "_run", start)
        monkeypatch.setattr("railpilot.gui.journey.messagebox.askokcancel", lambda *a, **k: False)
        journey.watch()
        start.assert_not_called()
        browser = Mock()
        browser.read_orders.return_value = []
        monkeypatch.setattr(app.session, "visible_browser", lambda: browser)
        journey.service.manual(journey.trip.choices[0], journey.trip)
        browser.open_booking.assert_called_once_with(journey.trip.choices[0])
    finally:
        app.destroy()

    reopened = RailPilotApp()
    try:
        journey = reopened.pages["journey"]
        assert journey.grid_info()
        assert journey.names.get() == "乘客甲，乘客乙"
        assert "待核实" in journey.order.get() and journey.active
    finally:
        reopened.destroy()
