"""RailPilot CLI: security checks and safe, dry-run-first workflows."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import ConfigError, events_path, load_config
from .core.clock import calibrate
from .core.guard import Guard
from .core.waitlist import build_plan
from .models import Task
from .security.doctor import run_checks
from .store.events import EventStore


def _setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def _config_path(value: str | None) -> Path:
    if value:
        return Path(value)
    roots = [Path.cwd()]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parent.parent)
    for root in roots:
        for candidate in (root / "config.yaml", root / "design" / "config.example.yaml"):
            if candidate.exists():
                return candidate
    return Path("config.yaml")


def _load_tasks(config: dict) -> list[Task]:
    tasks: list[Task] = []
    for index, raw in enumerate(config.get("tasks", [])):
        if isinstance(raw, dict):
            tasks.append(Task.from_config(raw, index))
    return tasks


def _default_cutoff(task: Task) -> str:
    first = sorted(task.dates)[0]
    return f"{first}T18:00:00+08:00"


def cmd_doctor(args: argparse.Namespace) -> int:
    result = run_checks(_config_path(args.config))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def cmd_clock(args: argparse.Namespace) -> int:
    config = load_config(_config_path(args.config))
    url = str(config.get("timing", {}).get("sync_url", "https://www.12306.cn"))
    samples = int(config.get("timing", {}).get("sync_samples", 5))
    try:
        state = calibrate(url=url, samples=samples)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": type(exc).__name__}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "offset_ms": state.offset_ms,
                "min_rtt_ms": state.min_rtt_ms,
                "samples": state.samples,
                "measured_at": state.measured_at,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_waitlist(args: argparse.Namespace) -> int:
    config = load_config(_config_path(args.config))
    tasks = _load_tasks(config)
    if not tasks:
        print(json.dumps({"ok": False, "error": "no tasks"}, ensure_ascii=False))
        return 1
    task = tasks[0]
    cutoff = args.cutoff or _default_cutoff(task)
    try:
        plan = build_plan(
            task.candidates,
            task.passengers,
            cutoff,
            accept_new_trains=bool(config.get("waitlist", {}).get("accept_new_trains", True)),
            latest_arrival=str(config.get("waitlist", {}).get("latest_arrival", "")),
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "plan": plan.to_dict()}, ensure_ascii=False, indent=2))
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    from .rail.adapter import PlaywrightAdapter

    adapter = PlaywrightAdapter(headless=False)
    try:
        adapter.open()
        ok = adapter.login_qr(timeout_s=args.timeout)
        print(json.dumps({"ok": ok, "blocked_hosts": adapter.blocked_hosts}, ensure_ascii=False))
        return 0 if ok else 1
    finally:
        adapter.close()


def cmd_query(args: argparse.Namespace) -> int:
    from .rail.adapter import PlaywrightAdapter

    adapter = PlaywrightAdapter(headless=args.headless)
    try:
        adapter.open()
        candidates = adapter.query(args.date, args.from_station, args.to_station)
        print(
            json.dumps(
                {
                    "ok": True,
                    "count": len(candidates),
                    "candidates": [c.__dict__ for c in candidates],
                    "blocked_hosts": adapter.blocked_hosts,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    finally:
        adapter.close()


def cmd_run_dry(args: argparse.Namespace) -> int:
    config = load_config(_config_path(args.config))
    tasks = _load_tasks(config)
    if not tasks:
        print(json.dumps({"ok": False, "error": "no tasks"}, ensure_ascii=False))
        return 1
    task = tasks[0]
    guard = Guard(
        max_manual_cancels_per_day=int(config.get("safety", {}).get("max_manual_cancels_per_day", 2)),
        max_unpaid_orders=int(config.get("safety", {}).get("max_unpaid_orders", 1)),
    )
    from .core.planner import fingerprint, rank_candidates

    ranked = rank_candidates(task)
    candidate = ranked[0]
    decision = guard.before_submit(fingerprint(task, candidate))
    print(
        json.dumps(
            {
                "ok": decision.allow,
                "task": task.name,
                "candidate": candidate.__dict__,
                "decision": decision.reason,
                "note": "dry-run only; no browser, no order",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if decision.allow else 1


def cmd_events(args: argparse.Namespace) -> int:
    config = load_config(_config_path(args.config))
    store = EventStore(events_path(config))
    try:
        rows = store.timeline(args.task_id or "ui", limit=args.limit)
        print(json.dumps([e.__dict__ for e in rows], ensure_ascii=False, indent=2))
    finally:
        store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="railpilot", description="Local-first 12306 assistant")
    parser.add_argument("--config", help="config YAML path")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="run local security checks")
    doctor.set_defaults(func=cmd_doctor)

    clock = sub.add_parser("clock", help="calibrate against 12306 HTTP Date")
    clock.set_defaults(func=cmd_clock)

    waitlist = sub.add_parser("waitlist", help="build a waitlist plan from config")
    waitlist.add_argument("--cutoff", default="", help="ISO8601 cutoff, e.g. 2027-02-10T18:00:00+08:00")
    waitlist.set_defaults(func=cmd_waitlist)

    login = sub.add_parser("login", help="open official QR login in a real browser")
    login.add_argument("--timeout", type=int, default=180)
    login.set_defaults(func=cmd_login)

    query = sub.add_parser("query", help="query official left-ticket page")
    query.add_argument("--date", required=True)
    query.add_argument("--from", dest="from_station", required=True)
    query.add_argument("--to", dest="to_station", required=True)
    query.add_argument("--headless", action="store_true")
    query.set_defaults(func=cmd_query)

    run = sub.add_parser("run", help="dry-run the orchestration path")
    run.add_argument("--dry-run", action="store_true", default=True)
    run.set_defaults(func=cmd_run_dry)

    events = sub.add_parser("events", help="show local event timeline")
    events.add_argument("--task-id", default="")
    events.add_argument("--limit", type=int, default=50)
    events.set_defaults(func=cmd_events)

    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    _setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
