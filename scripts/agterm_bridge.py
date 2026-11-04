#!/usr/bin/env python3
"""Read-only agterm status bridge for the Omarchy Quickshell widget.

Only the explicit `focus` operation mutates GUI selection. Never executes shell text.
"""

from __future__ import annotations

import argparse
import fcntl
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
EVENT_KINDS = "status,notify,session.created,session.closed,tree.changed"
TITLE_SPINNER_FRAMES = frozenset("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
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
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        merged = load_usage()
        for key, value in usage.items():
            merged[key] = max(float(merged.get(key, 0) or 0), float(value))
        selected: dict[str, tuple[str, float]] = {}
        for key, value in merged.items():
            if not key.startswith("@selected:"):
                continue
            prefix = key.rsplit(":", 1)[0]
            if prefix not in selected or value > selected[prefix][1]:
                selected[prefix] = (key, value)
        for key in [key for key in merged if key.startswith("@selected:")]:
            merged.pop(key)
        merged.update({key: value for key, value in selected.values()})
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")))
        temp.replace(path)
        fcntl.flock(lock, fcntl.LOCK_UN)


def record_usage(session_id: str, when: float | None = None) -> dict[str, float]:
    merged = load_usage()
    merged[session_id] = max(float(merged.get(session_id, 0) or 0), float(when if when is not None else time.time()))
    save_usage(merged)
    return merged


def call_ctl(cli: str, *args: str) -> dict:
    command = [cli, *args]
    socket = os.environ.get("AGTERM_SOCKET", "")
    if socket:
        command.extend(("--socket", socket))
    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
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
        error = response.get("error", "agtermctl response not ok") if isinstance(response, dict) else "agtermctl response not ok"
        raise AgtermError(str(error)[:280])
    result = response.get("result", {})
    if not isinstance(result, dict):
        raise AgtermError("agtermctl returned an unexpected result")
    return result


def session_timestamp(session: dict) -> float:
    for key in ("lastUsedAt", "lastSelectedAt", "selectedAt"):
        value = session.get(key)
        try:
            stamp = float(value)
        except (TypeError, ValueError):
            continue
        if stamp > 0:
            return stamp
    return 0.0


def session_status(session: dict) -> str:
    reported = session.get("status")
    if reported is not None:
        status = str(reported).lower()
        return status if status in SORT_RANK else "idle"
    # Read only the terminal title returned by agterm, never an editable row name,
    # selected-session flag, or foreground executable. Explicit status wins.
    title = session.get("title")
    if isinstance(title, str):
        parts = title.split(maxsplit=2)
        if len(parts) >= 2 and (
            (parts[0] == "π" and parts[1] in TITLE_SPINNER_FRAMES)
            or (parts[0] in TITLE_SPINNER_FRAMES and parts[1] == "π")
        ):
            return "active"
    return "idle"


def extract_sessions(windows: list[dict], trees: dict[str, dict], usage: dict[str, float] | None = None) -> list[dict]:
    sessions: list[dict] = []
    usage_map = usage or {}
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
                status = session_status(session)
                try:
                    unseen = int(session.get("unseen") or 0)
                except (TypeError, ValueError):
                    unseen = 0
                try:
                    changed_at = float(session.get("statusChangedAt") or 0)
                except (TypeError, ValueError):
                    changed_at = 0.0
                sessions.append({
                    "id": session_id,
                    "windowId": window_id,
                    "windowName": str(window.get("name") or "agterm"),
                    "workspace": workspace_name,
                    "name": str(session.get("name") or session.get("title") or f"Session {session_id[:8]}"),
                    "cwd": str(session.get("cwd") or ""),
                    "status": status,
                    "pane": str(session.get("statusPane") or ""),
                    "active": bool(session.get("active", False)),
                    "unseen": unseen,
                    "changedAt": changed_at,
                    "lastUsedAt": max(session_timestamp(session), float(usage_map.get(session_id, 0) or 0)),
                })
    sessions.sort(key=lambda s: (
        SORT_RANK[s["status"]],
        -s["changedAt"] if s["status"] in ("blocked", "completed") else 0,
        s["windowName"].lower(),
        s["workspace"].lower(),
        s["name"].lower(),
    ))
    return sessions


def snapshot(cli: str) -> dict:
    windows_result = call_ctl(cli, "window", "list", "--json")
    windows = windows_result.get("windows")
    if not isinstance(windows, list) or any(not isinstance(window, dict) for window in windows):
        raise AgtermError("window.list returned an unexpected structure")
    open_windows = [window for window in windows if window.get("open")]
    trees: dict[str, dict] = {}
    errors: list[str] = []
    for window in open_windows:
        window_id = str(window.get("id", ""))
        if not window_id:
            errors.append("An open window has no id")
            continue
        try:
            tree = call_ctl(cli, "tree", "--json", "--window", window_id).get("tree")
            if (
                not isinstance(tree, dict)
                or not isinstance(tree.get("workspaces"), list)
                or any(
                    not isinstance(workspace, dict)
                    or not isinstance(workspace.get("sessions", []), list)
                    or any(not isinstance(session, dict) for session in workspace.get("sessions", []))
                    for workspace in tree["workspaces"]
                )
            ):
                raise AgtermError(f"Invalid tree response for window {window_id}")
            trees[window_id] = tree
        except AgtermError as exc:
            errors.append(f"{window.get('name') or window_id}: {exc}")
    if open_windows and not trees:
        return {
            "online": False,
            "error": "; ".join(errors)[:280] or "Unable to read any open window trees",
            "windowCount": 0,
            "sessions": [],
        }
    usage = load_usage()
    sessions = extract_sessions(windows, trees, usage)
    now = time.time()
    usage_changed = False
    for window_id, tree in trees.items():
        for workspace in tree["workspaces"]:
            workspace_name = str(workspace.get("name") or "Workspace")
            workspace_key = str(workspace.get("id") or workspace_name)
            selected = next((s for s in workspace.get("sessions", []) if s.get("active") and s.get("content") != "page" and s.get("id")), None)
            if selected is None:
                continue
            session_id = str(selected["id"])
            selection_prefix = f"@selected:{window_id}:{workspace_key}:"
            selected_key = f"{selection_prefix}{session_id}"
            prior_keys = [key for key in usage if key.startswith(selection_prefix)]
            if selected_key in usage:
                continue
            changed_selection = bool(prior_keys)
            for key in prior_keys:
                usage.pop(key, None)
            usage[selected_key] = now
            usage_changed = True
            if not changed_selection and session_id not in usage and not session_timestamp(selected):
                usage[session_id] = now
            elif changed_selection:
                usage[session_id] = max(float(usage.get(session_id, 0) or 0), now)
    if usage_changed:
        try:
            save_usage(usage)
        except OSError:
            pass
    for session in sessions:
        session["lastUsedAt"] = max(session["lastUsedAt"], float(usage.get(session["id"], 0) or 0))
    return {
        "online": True,
        "error": "",
        "warning": "; ".join(errors)[:280],
        "windowCount": len(trees),
        "sessions": sessions,
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


def read_event_errors(process: subprocess.Popen, messages: queue.Queue) -> None:
    try:
        assert process.stderr is not None
        reported = False
        while line := process.stderr.readline(281):
            if not reported and line.strip():
                messages.put(("stderr", process, line.strip()[:280]))
                reported = True
    except (OSError, ValueError):
        pass

def stop_event_process(process: subprocess.Popen) -> int | None:
    if process.poll() is not None:
        return process.returncode
    try:
        process.terminate()
    except OSError:
        pass
    try:
        return process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            return process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            return process.poll()

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
    event_warning = "Event monitor is starting"
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
                    command = [cli, "events", "--json", "--kind", EVENT_KINDS]
                    socket = os.environ.get("AGTERM_SOCKET", "")
                    if socket:
                        command.extend(("--socket", socket))
                    events = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, bufsize=1,
                    )
                    threading.Thread(
                        target=read_events, args=(events, messages), daemon=True,
                    ).start()
                    threading.Thread(
                        target=read_event_errors, args=(events, messages), daemon=True,
                    ).start()
                    event_warning = ""
                except OSError as exc:
                    events = None
                    event_warning = f"Event monitor unavailable: {exc}"[:280]
                    next_spawn = now + RETRY_SECONDS

            if now >= next_refresh or (pending_refresh and now >= pending_refresh):
                try:
                    value = snapshot(cli)
                except AgtermError as exc:
                    value = {"online": False, "sessions": [], "windowCount": 0,
                             "error": str(exc)}
                value["eventWarning"] = event_warning
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
                if isinstance(event, dict) and event.get("kind") in EVENT_KINDS.split(","):
                    if not pending_refresh:
                        pending_refresh = time.monotonic() + 0.15
            elif kind == "stderr":
                event_warning = f"Event monitor: {payload}"[:280]
                pending_refresh = time.monotonic() + 0.15
            elif kind == "end":
                exit_code = stop_event_process(process)
                events = None
                event_warning = f"Event monitor exited ({exit_code if exit_code is not None else 'unknown'})"[:280]
                next_spawn = time.monotonic() + RETRY_SECONDS
                pending_refresh = time.monotonic() + 0.15
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        if events is not None:
            stop_event_process(events)


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
