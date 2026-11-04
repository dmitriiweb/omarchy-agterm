import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, call

path = Path(__file__).resolve().parents[1] / "scripts/agterm_bridge.py"
spec = importlib.util.spec_from_file_location("agterm_bridge", path)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

ID_A = "12345678-abcd-abcd-abcd-123456789abc"
ID_B = "23456789-abcd-abcd-abcd-123456789abc"
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
            self.assertEqual(args, ("tree", "--json", "--window", WIN))
            return {"tree": {"workspaces": [{"name": "one", "sessions": [
                {"id": ID_A, "status": "completed"}]}]}}
        with patch.object(bridge, "call_ctl", side_effect=mocked),              patch.object(bridge, "load_usage", return_value={}):
            result = bridge.snapshot("agtermctl")
        self.assertTrue(result["online"])
        self.assertEqual(result["windowCount"], 1)
        self.assertEqual(result["sessions"][0]["status"], "completed")

    def test_focus_only_calls_select_and_records_usage(self):
        with tempfile.TemporaryDirectory() as td,              patch.dict("os.environ", {"XDG_CACHE_HOME": td}, clear=False),              patch.object(bridge, "call_ctl") as mocked:
            bridge.focus("agtermctl", WIN, ID_B)
            usage = bridge.load_usage()
        self.assertEqual(mocked.call_args_list, [
            call("agtermctl", "session", "select", "--target", ID_B,
                 "--window", WIN, "--json"),
            call("agtermctl", "window", "select", WIN, "--json"),
        ])
        self.assertIn(ID_B, usage)

    def test_focus_rejects_cli_option_injection(self):
        with self.assertRaises(bridge.AgtermError):
            bridge.focus("agtermctl", "--socket", ID_B)


if __name__ == "__main__":
    unittest.main()
