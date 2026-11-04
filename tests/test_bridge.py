import importlib.util
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "scripts/agterm_bridge.py"
spec = importlib.util.spec_from_file_location("agterm_bridge", path)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

ID_A = "12345678-abcd-abcd-abcd-123456789abc"
ID_B = "23456789-abcd-abcd-abcd-123456789abc"
ID_C = "3456789a-abcd-abcd-abcd-123456789abc"
WIN = "abcdefab-abcd-abcd-abcd-123456789abc"


class BridgeTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(bridge.extract_sessions([], {}), [])

    def test_sort_status_filter_pages_and_preserve_workspace(self):
        windows = [{"id": WIN, "name": "main", "open": True},
                   {"id": "closed", "name": "old", "open": False}]
        trees = {WIN: {"workspaces": [
            {"id": "w1", "name": "work", "sessions": [
                {"id": ID_A, "name": "API", "status": "active", "cwd": "/home/di/a"},
                {"id": ID_B, "name": "Router", "status": "blocked", "statusPane": "right",
                 "cwd": "/home/di/b", "statusChangedAt": 10},
                {"id": "page-companion", "content": "page"},
            ]}
        ]}}
        result = bridge.extract_sessions(windows, trees, {})
        self.assertEqual([r["name"] for r in result], ["Router", "API"])
        self.assertEqual(result[0]["windowId"], WIN)
        self.assertEqual(result[0]["workspace"], "work")
        self.assertEqual(result[0]["pane"], "right")

    def test_last_used_prefers_cache_or_session_timestamp(self):
        windows = [{"id": WIN, "name": "main", "open": True}]
        trees = {WIN: {"workspaces": [{"name": "work", "sessions": [
            {"id": ID_A, "name": "API", "status": "idle", "lastUsedAt": 4},
            {"id": ID_B, "name": "Router", "status": "idle"},
        ]}]}}
        result = bridge.extract_sessions(windows, trees, {ID_B: 9})
        by_id = {row["id"]: row for row in result}
        self.assertEqual(by_id[ID_A]["lastUsedAt"], 4)
        self.assertEqual(by_id[ID_B]["lastUsedAt"], 9)

    def test_snapshot_collects_open_windows(self):
        def mocked(_cli, *args):
            if args[:2] == ("window", "list"):
                return {"windows": [{"id": WIN, "name": "work", "open": True}]}
            return {"tree": {"workspaces": [{"name": "one", "sessions": [
                {"id": ID_A, "status": "completed"}]}]}}
        with patch.object(bridge, "call_ctl", side_effect=mocked),              patch.object(bridge, "load_usage", return_value={}):
            result = bridge.snapshot("agtermctl")
        self.assertTrue(result["online"])
        self.assertEqual(result["windowCount"], 1)
        self.assertEqual(result["sessions"][0]["status"], "completed")
    def test_snapshot_reports_partial_and_total_tree_failures(self):
        windows = [
            {"id": WIN, "name": "main", "open": True},
            {"id": "bbbbbbbb-abcd-abcd-abcd-123456789abc", "name": "side", "open": True},
        ]

        def partial(_cli, *args):
            if args[:2] == ("window", "list"):
                return {"windows": windows}
            if args[-1] == WIN:
                return {"tree": {"workspaces": []}}
            raise bridge.AgtermError("window disappeared")

        with patch.object(bridge, "call_ctl", side_effect=partial), patch.object(bridge, "load_usage", return_value={}):
            result = bridge.snapshot("agtermctl")
        self.assertTrue(result["online"])
        self.assertEqual(result["windowCount"], 1)
        self.assertIn("window disappeared", result["warning"])

        def failed_tree(_cli, *args):
            if args[:2] == ("window", "list"):
                return {"windows": windows}
            raise bridge.AgtermError("tree unavailable")

        with patch.object(bridge, "call_ctl", side_effect=failed_tree):
            result = bridge.snapshot("agtermctl")
        self.assertFalse(result["online"])
        self.assertIn("tree unavailable", result["error"])

        def malformed_tree(_cli, *args):
            if args[:2] == ("window", "list"):
                return {"windows": windows}
            return {"tree": {"workspaces": [None]}}

        with patch.object(bridge, "call_ctl", side_effect=malformed_tree):
            result = bridge.snapshot("agtermctl")
        self.assertFalse(result["online"])
        self.assertIn("Invalid tree response", result["error"])

    def test_snapshot_is_observational_except_selection_transitions(self):
        windows = [{"id": WIN, "name": "main", "open": True}]
        active = {"work": ID_A, "other": ID_C}

        def mocked(_cli, *args):
            if args[:2] == ("window", "list"):
                return {"windows": windows}
            return {"tree": {"workspaces": [
                {"id": "work", "name": "work", "sessions": [
                    {"id": ID_A, "active": active["work"] == ID_A, "status": "active"},
                    {"id": ID_B, "active": active["work"] == ID_B, "status": "idle"},
                ]},
                {"id": "other", "name": "other", "sessions": [
                    {"id": ID_C, "active": active["other"] == ID_C, "status": "idle"},
                ]},
            ]}}

        with tempfile.TemporaryDirectory() as td, \
             patch.dict("os.environ", {"XDG_CACHE_HOME": td}, clear=False), \
             patch.object(bridge, "call_ctl", side_effect=mocked), \
             patch.object(bridge.time, "time", side_effect=[100.0, 200.0, 300.0]):
            bridge.snapshot("agtermctl")
            initial = bridge.load_usage()
            bridge.snapshot("agtermctl")
            repeated = bridge.load_usage()
            active["work"] = ID_B
            bridge.snapshot("agtermctl")
            changed = bridge.load_usage()
        self.assertEqual(initial[ID_A], 100.0)
        self.assertEqual(initial[ID_C], 100.0)
        self.assertEqual(repeated[ID_A], initial[ID_A])
        self.assertEqual(repeated[ID_C], initial[ID_C])
        self.assertEqual(changed[ID_B], 300.0)
        self.assertEqual(changed[ID_A], initial[ID_A])
        self.assertEqual(changed[ID_C], initial[ID_C])

    def test_watch_refreshes_for_a_bare_status_event_and_reaps_subscriber(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / "status"
            state.write_text("idle")
            tree_read = root / "tree-read"
            event_pid = root / "event-pid"
            cli = root / "agtermctl"
            cli.write_text(f"""#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv[1:]
root = pathlib.Path({str(root)!r})
if args[:2] == ["window", "list"]:
    result = {{"windows": [{{"id": {WIN!r}, "name": "main", "open": True}}]}}
elif args[0] == "tree":
    status = (root / "status").read_text()
    (root / "tree-read").touch()
    result = {{"tree": {{"workspaces": [{{"name": "work", "sessions": [{{"id": {ID_A!r}, "status": status}}]}}]}}}}
elif args[0] == "events":
    count_file = root / "event-count"
    count = int(count_file.read_text()) + 1 if count_file.exists() else 1
    count_file.write_text(str(count))
    (root / "event-pid").write_text(str(os.getpid()))
    if count == 1:
        sys.stderr.write("subscriber closed stdout but remained alive\\n")
        os.close(1)
        while True:
            time.sleep(1)
    while not (root / "tree-read").exists():
        time.sleep(0.01)
    (root / "status").write_text("active")
    print(json.dumps({{"kind": "status", "session": {ID_A!r}, "payload": {{"status": "active"}}}}), flush=True)
    while True:
        time.sleep(1)
else:
    raise SystemExit(64)
print(json.dumps({{"ok": True, "result": result}}))
""")
            cli.chmod(0o755)
            env = dict(os.environ, XDG_CACHE_HOME=str(root / "cache"))
            proc = subprocess.Popen(
                ["python3", str(path), "watch", "--cli", str(cli)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
            )
            seen = []
            deadline = time.monotonic() + 8
            try:
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([proc.stdout], [], [], 0.2)
                    if not ready:
                        continue
                    line = proc.stdout.readline()
                    if not line:
                        break
                    seen.append(json.loads(line))
                    if seen[-1].get("sessions", [{}])[0].get("status") == "active":
                        break
            finally:
                proc.terminate()
                proc.wait(timeout=3)
                proc.stdout.close()
                proc.stderr.close()
            self.assertTrue(any(item.get("sessions", [{}])[0].get("status") == "idle" for item in seen))
            self.assertTrue(any(item.get("sessions", [{}])[0].get("status") == "active" for item in seen),
                            f"event count={event_count if (event_count := (root / 'event-count').read_text()) else 'missing'}; snapshots={seen!r}")
            self.assertTrue(any("Event monitor exited (-15)" in item.get("eventWarning", "") for item in seen))
            self.assertGreaterEqual(int((root / "event-count").read_text()), 2)
            usage_path = root / "cache" / bridge.CACHE_SUBDIR / bridge.CACHE_FILE
            usage_data = json.loads(usage_path.read_text()) if usage_path.exists() else {}
            self.assertNotIn(ID_A, usage_data)
            pid = int(event_pid.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_save_usage_does_not_regress_a_newer_focus_timestamp(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.dict("os.environ", {"XDG_CACHE_HOME": td}, clear=False):
            bridge.save_usage({ID_A: 500.0})
            bridge.save_usage({ID_A: 100.0, ID_B: 200.0})
            usage = bridge.load_usage()
        self.assertEqual(usage[ID_A], 500.0)
        self.assertEqual(usage[ID_B], 200.0)


    def test_session_timestamp_ignores_update_and_status_change(self):
        self.assertEqual(bridge.session_timestamp({"updatedAt": 99, "statusChangedAt": 100}), 0)


    def test_empty_snapshots_are_online_without_tree_failures(self):
        cases = [
            [{"windows": []}],
            [{"windows": [{"id": WIN, "open": False}]}],
            [{"windows": [{"id": WIN, "open": True}]},
             {"tree": {"workspaces": [{"id": "empty", "sessions": []}]}}],
        ]
        for responses in cases:
            with self.subTest(responses=responses), \
                 patch.object(bridge, "call_ctl", side_effect=responses), \
                 patch.object(bridge, "load_usage", return_value={}):
                result = bridge.snapshot("agtermctl")
            self.assertTrue(result["online"])
            self.assertEqual(result["sessions"], [])
            self.assertEqual(result["error"], "")
            self.assertEqual(result["warning"], "")

    def test_invalid_window_lists_are_not_empty_successes(self):
        for windows in (None, {}, "windows", [None], [1]):
            with self.subTest(windows=windows), \
                 patch.object(bridge, "call_ctl", return_value={"windows": windows}):
                with self.assertRaises(bridge.AgtermError):
                    bridge.snapshot("agtermctl")

    def test_protocol_errors_are_reported_at_the_response_boundary(self):
        cases = [
            ("not-json", "Invalid agtermctl JSON"),
            ("null", "response not ok"),
            ("[]", "response not ok"),
            ('{"ok": false, "error": "socket unavailable"}', "socket unavailable"),
            ('{"ok": true, "result": []}', "unexpected result"),
        ]
        for stdout, error in cases:
            response = subprocess.CompletedProcess([], 0, stdout, "")
            with self.subTest(stdout=stdout), \
                 patch.object(bridge.subprocess, "run", return_value=response):
                with self.assertRaisesRegex(bridge.AgtermError, error):
                    bridge.call_ctl("agtermctl", "window", "list", "--json")

    def test_attention_order_is_status_first_not_recency_or_selection(self):
        windows = [{"id": WIN, "open": True}]
        trees = {WIN: {"workspaces": [{"name": "work", "sessions": [
            {"id": ID_A, "status": "idle", "active": True, "lastUsedAt": 900},
            {"id": ID_B, "status": "active", "lastUsedAt": 800},
            {"id": ID_C, "status": "completed", "statusChangedAt": 20},
            {"id": "blocked-old", "status": "blocked", "statusChangedAt": 1},
            {"id": "blocked-new", "status": "blocked", "statusChangedAt": 30},
        ]}]}}
        rows = bridge.extract_sessions(windows, trees, {})
        self.assertEqual([row["id"] for row in rows],
                         ["blocked-new", "blocked-old", ID_C, ID_B, ID_A])

    def test_selected_foreground_process_does_not_imply_agent_work(self):
        windows = [{"id": WIN, "open": True}]
        trees = {WIN: {"workspaces": [{"sessions": [{
            "id": ID_A, "active": True, "foreground": {"argv": ["omp"]},
        }]}]}}
        rows = bridge.extract_sessions(windows, trees, {})
        self.assertEqual(rows[0]["status"], "idle")
        self.assertEqual(rows[0]["lastUsedAt"], 0)

    def test_agterm_spinner_title_reports_work_without_structured_status(self):
        windows = [{"id": WIN, "open": True}]
        for title in ("π ⠏ Integration v02", "⠋ π - Integration v02 - omp"):
            with self.subTest(title=title):
                trees = {WIN: {"workspaces": [{"sessions": [{
                    "id": ID_A, "title": title, "active": False,
                }]}]}}
                row = bridge.extract_sessions(windows, trees, {ID_A: 100})[0]
                self.assertEqual(row["status"], "active")
                self.assertEqual(row["lastUsedAt"], 100)

    def test_explicit_agterm_status_wins_over_spinner_title(self):
        windows = [{"id": WIN, "open": True}]
        for status in ("idle", "blocked", "completed", "active"):
            with self.subTest(status=status):
                trees = {WIN: {"workspaces": [{"sessions": [{
                    "id": ID_A, "title": "π ⠋ Agent", "status": status,
                }]}]}}
                self.assertEqual(bridge.extract_sessions(windows, trees, {})[0]["status"], status)

    def test_stopped_or_unrecognized_titles_do_not_report_work(self):
        windows = [{"id": WIN, "open": True}]
        session = {"id": ID_A, "title": "π ⠋ Agent"}
        trees = {WIN: {"workspaces": [{"sessions": [session]}]}}
        self.assertEqual(bridge.extract_sessions(windows, trees, {})[0]["status"], "active")
        for title in ("π - Agent", "editor ⠋ π", "π ⠋suffix", "⠋ progress", ""):
            with self.subTest(title=title):
                session["title"] = title
                self.assertEqual(bridge.extract_sessions(windows, trees, {})[0]["status"], "idle")
        session.pop("title")
        session["name"] = "π ⠋ custom user label"
        self.assertEqual(bridge.extract_sessions(windows, trees, {})[0]["status"], "idle")

    def test_focus_rejects_cli_option_injection(self):
        with self.assertRaises(bridge.AgtermError):
            bridge.focus("agtermctl", "--socket", ID_B)


if __name__ == "__main__":
    unittest.main()
