#!/usr/bin/env python3
"""Read-only agterm status bridge for the Omarchy Quickshell widget.

Only the explicit `focus` operation mutates GUI selection. Never executes shell text.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import signal
import shutil
import subprocess
import sys
import threading
import time


SORT_RANK = {"blocked": 0, "completed": 1, "active": 2, "idle": 3}
EVENT_KINDS = "status,notify,session.created,session.closed,session.selected,tree.changed"
REFRESH_INTERVAL_SECONDS = 20
RETRY_SECONDS = 3
CACHE_SUBDIR = "omarchy-agterm"
CACHE_FILE = "usage.json"


class AgtermError(Exception):
    """An unavailable or unexpectedly responding agtermctl."""


def cli_path(explicit: str = "") -> str:
    if explicit:
        candidate = Path(os.path.expandvars(os.path.expanduser(explicit)))
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise AgtermError(f"agtermctl is not executable: {candidate}")
        return str(candidate)

    found = shutil.which("agtermctl")
    if found:
        return found
    for candidate in (
        Path.home() / ".local/bin/agtermctl",
        Path("/usr/bin/agtermctl"),
        Path("/usr/local/bin/agtermctl"),
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise AgtermError("agtermctl not found. Set its absolute path in widget settings.")


def cache_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / CACHE_SUBDIR / CACHE_FILE


def load_usage() -> dict[str, float]:
    path = cache_path()
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    usage: dict[str, float] = {}
    for key, value in data.items():
        try:
            usage[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return usage


def save_usage(usage: dict[str, float]) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(usage, ensure_ascii=False, separators=(",", ":")))
    temp.replace(path)


def record_usage(session_id: str, when: float | None = None, usage: dict[str, float] | None = None) -> dict[str, float]:
    current = usage if usage is not None else load_usage()
    current[session_id] = float(when if when is not None else time.time())
    if usage is None:
        save_usage(current)
    return current


def call_ctl(cli: str, *args: str) -> dict:
    try:
        result = subprocess.run(
            [cli, *args], capture_output=True, text=True,
            timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgtermError(f"Cannot run agtermctl: {exc}") from exc
    if result.returncode:
        raise AgtermError((result.stderr or result.stdout or "agtermctl failed").strip()[:280])
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AgtermError("Invalid agtermctl JSON response") from exc
    if not isinstance(response, dict) or not response.get("ok", False):
        raise AgtermError(str(response.get("error", "agtermctl response not ok"))[:280])
    return response.get("result", {})


def session_timestamp(session: dict) -> float:
    for key in ("lastUsedAt", "lastSelectedAt", "selectedAt", "updatedAt", "statusChangedAt"):
        value = session.get(key)
        try:
            stamp = float(value)
        except (TypeError, ValueError):
            continue
        if stamp > 0:
            return stamp
    return 0.0


def extract_sessions(windows: list[dict], trees: dict[str, dict], usage: dict[str, float] | None = None) -> list[dict]:
    sessions: list[dict] = []
    usage_map = dict(usage or {})
    usage_changed = False
    now = time.time()
    for window in windows:
        if not window.get("open"):
            continue
        window_id = str(window.get("id", ""))
        if not window_id or window_id not in trees:
            continue
        tree = trees[window_id]
        for workspace in tree.get("workspaces", []):
            workspace_name = str(workspace.get("name") or "Workspace")
            for session in workspace.get("sessions", []):
                if session.get("content") == "page":
                    continue
                session_id = str(session.get("id", ""))
                if not session_id:
                    continue
                status = str(session.get("status") or "idle").lower()
                if status not in SORT_RANK:
                    status = "idle"
                derived_last_used = max(session_timestamp(session), float(usage_map.get(session_id, 0) or 0))
                active = bool(session.get("active", False))
                if active and derived_last_used < now:
                    derived_last_used = now
                    usage_map[session_id] = derived_last_used
                    usage_changed = True
                sessions.append({
                    "id": session_id,
                    "windowId": window_id,
                    "windowName": str(window.get("name") or "agterm"),
                    "workspace": workspace_name,
                    "name": str(session.get("name") or session.get("title") or f"Session {session_id[:8]}"),
                    "cwd": str(session.get("cwd") or ""),
                    "status": status,
                    "pane": str(session.get("statusPane") or ""),
                    "active": active,
                    "unseen": int(session.get("unseen") or 0),
                    "changedAt": float(session.get("statusChangedAt") or 0),
                    "lastUsedAt": derived_last_used,
                })
    if usage_changed:
        try:
            save_usage(usage_map)
        except OSError:
            pass
    sessions.sort(key=lambda s: (
        SORT_RANK[s["status"]],
        -s["changedAt"] if s["status"] in ("blocked", "completed") else 0,
        s["windowName"].lower(),
        s["workspace"].lower(),
        s["name"].lower(),
    ))
    return sessions


def snapshot(cli: str) -> dict:
    windows = call_ctl(cli, "window", "list", "--json").get("windows", [])
    if not isinstance(windows, list):
        raise AgtermError("window.list returned an unexpected structure")
    trees: dict[str, dict] = {}
    for window in windows:
        if not window.get("open"):
            continue
        window_id = str(window.get("id", ""))
        if not window_id:
            continue
        try:
            tree = call_ctl(cli, "tree", "--json", "--window", window_id).get("tree")
            if isinstance(tree, dict):
                trees[window_id] = tree
        except AgtermError:
            continue
    return {
        "online": True,
        "error": "",
        "windowCount": len(trees),
        "sessions": extract_sessions(windows, trees, load_usage()),
    }


def write_snapshot(value: dict, last: str | None) -> str:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if last != serialized:
        print(serialized, flush=True)
    return serialized


def read_events(process: subprocess.Popen, messages: queue.Queue) -> None:
    try:
        assert process.stdout is not None
        for line in process.stdout:
            stripped = line.strip()
            if stripped:
                messages.put(("event", process, stripped))
    except (OSError, ValueError):
        pass
    finally:
        messages.put(("end", process, ""))


def watch(explicit_cli: str) -> None:
    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    messages: queue.Queue = queue.Queue()
    events: subprocess.Popen | None = None
    next_spawn = 0.0
    next_refresh = 0.0
    pending_refresh = 0.0
    last_snapshot: str | None = None
    usage = load_usage()
    try:
        while True:
            now = time.monotonic()
            try:
                cli = cli_path(explicit_cli)
            except AgtermError as exc:
                last_snapshot = write_snapshot({
                    "online": False, "sessions": [], "windowCount": 0,
                    "error": str(exc),
                }, last_snapshot)
                time.sleep(RETRY_SECONDS)
                continue

            if events is None and now >= next_spawn:
                try:
                    events = subprocess.Popen(
                        [cli, "events", "--json", "--kind", EVENT_KINDS],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        text=True, bufsize=1,
                    )
                    threading.Thread(
                        target=read_events, args=(events, messages), daemon=True,
                    ).start()
                except OSError:
                    events = None
                    next_spawn = now + RETRY_SECONDS

            if now >= next_refresh or (pending_refresh and now >= pending_refresh):
                try:
                    value = snapshot(cli)
                except AgtermError as exc:
                    value = {"online": False, "sessions": [], "windowCount": 0,
                             "error": str(exc)}
                last_snapshot = write_snapshot(value, last_snapshot)
                next_refresh = time.monotonic() + REFRESH_INTERVAL_SECONDS
                pending_refresh = 0.0

            try:
                kind, process, payload = messages.get(timeout=0.3)
            except queue.Empty:
                continue
            if process is not events:
                continue
            if kind == "event":
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    event = {}
                result = event.get("result") if isinstance(event, dict) else {}
                if isinstance(result, dict):
                    sid = result.get("sessionId") or result.get("session_id") or result.get("id")
                    if isinstance(sid, str) and sid:
                        usage = record_usage(sid, usage=usage)
                        try:
                            save_usage(usage)
                        except OSError:
                            pass
                if not pending_refresh:
                    pending_refresh = time.monotonic() + 0.15
            elif kind == "end":
                events = None
                next_spawn = time.monotonic() + RETRY_SECONDS
                pending_refresh = time.monotonic() + 0.15
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        if events is not None and events.poll() is None:
            events.terminate()
            try:
                events.wait(timeout=1)
            except subprocess.TimeoutExpired:
                events.kill()


def focus(cli: str, window_id: str, session_id: str) -> None:
    import re
    allowed = re.compile(r"^[a-fA-F0-9-]{8,64}$")
    if not allowed.fullmatch(window_id) or not allowed.fullmatch(session_id):
        raise AgtermError("Invalid window/session ID")
    call_ctl(cli, "session", "select", "--target", session_id,
             "--window", window_id, "--json")
    call_ctl(cli, "window", "select", window_id, "--json")
    try:
        record_usage(session_id)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("watch", "snapshot", "focus"):
        sub = commands.add_parser(command)
        if command == "focus":
            sub.add_argument("window_id")
            sub.add_argument("session_id")
        sub.add_argument("--cli", default="", help="Optional absolute path to agtermctl")
    args = parser.parse_args(argv)
    try:
        if args.command == "watch":
            watch(args.cli)
        elif args.command == "snapshot":
            print(json.dumps(snapshot(cli_path(args.cli)), ensure_ascii=False))
        else:
            focus(cli_path(args.cli), args.window_id, args.session_id)
    except AgtermError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
