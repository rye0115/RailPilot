import tkinter as tk
from pathlib import Path

import pytest

from railpilot.gui.app import RailPilotApp
from railpilot.models import Candidate


def test_waitlist_import_respects_visible_and_selected_trains(tmp_path, monkeypatch):
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
        plan = app.pages["waitlist"]

        def candidate(train, seat, time, available):
            return Candidate("2027-02-10", train, "北京", "上海", seat, depart_time=time, available=available)

        query.results = [
            candidate("K2", "硬座", "12:00", "候补"), candidate("K1", "硬卧", "08:00", "有"),
            candidate("G1", "二等座", "07:00", "0"), candidate("K1", "硬座", "08:00", "2"),
        ]
        query.populate()
        rows = query.tree.get_children()
        assert [query.tree.set(row, "train") for row in rows] == ["G1", "K1", "K2"]
        assert [query.tree.set(row, "status") for row in rows] == ["暂无余票", "可订", "候补"]
        query.filter_var.set("k")
        assert "硬卧" in query.tree.cget("displaycolumns")
        assert "二等座" not in query.tree.cget("displaycolumns")
        query.to_waitlist()
        plan.generate()
        assert plan.plan.total_combos == 3

        query.tree.selection_set(query.tree.get_children()[0])
        query.to_waitlist()
        assert plan.plan is None
        plan.generate()
        assert {c.train_no for c in plan.plan.orders[0].combos} == {"K1"}
        assert plan.plan.total_combos == 2
        plan.cutoff_var.set("2027-02-10T05:00:00+08:00")
        assert plan.plan is None and not plan.tree.get_children()
        plan.source.set("config")
        plan._set_default_cutoff(0)
        plan.generate()
        assert plan.plan.total_combos == len(app.tasks[0].candidates)
    finally:
        app.destroy()
