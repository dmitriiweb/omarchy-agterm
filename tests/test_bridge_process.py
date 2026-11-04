import importlib.util
import json
import os
from pathlib import Path
import queue
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest


BRIDGE_PATH = Path(__file__).resolve().parents[1] / "scripts/agterm_bridge.py"
spec = importlib.util.spec_from_file_location("process_bridge", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

SESSION = "12345678-abcd-abcd-abcd-123456789abc"
OTHER = "23456789-abcd-abcd-abcd-123456789abc"
WINDOW = "abcdefab-abcd-abcd-abcd-123456789abc"

# A separate executable implements the public CLI boundary, not bridge internals.
FAKE_CLI = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
import time

root = Path(os.environ["TEST_AGTERM_ROOT"])
args = sys.argv[1:]
socket = os.environ["AGTERM_SOCKET"]
if args.count("--socket") != 1 or args[args.index("--socket") + 1] != socket:
    sys.stderr.write("missing explicit test socket\n")
    sys.exit(64)
index = args.index("--socket")
del args[index:index + 2]
state_path = root / "state.json"
state = json.loads(state_path.read_text())

def fail(message):
    sys.stderr.write(message + "\n")
    sys.exit(1)

if args[0] == "events":
    supported = {"status", "notify", "session.created", "session.closed", "tree.changed"}
    if not set(args[args.index("--kind") + 1].split(",")).issubset(supported):
        fail("unsupported event kind")
    with (root / "subscriber-pids").open("a") as file:
        file.write(str(os.getpid()) + "\n")
    revision = None
    while True:
        state = json.loads(state_path.read_text())
        if state.get("eventFailure"):
            fail("events temporarily unavailable")
        if revision != state.get("revision", 0):
            revision = state.get("revision", 0)
            print(json.dumps({"kind": state.get("eventKind", "tree.changed"),
                              "session": state["session"], "payload": {}}), flush=True)
        time.sleep(0.02)
elif args[:2] == ["window", "list"]:
    if state.get("offline"):
        fail("test socket unavailable")
    result = {"windows": [{"id": state["window"], "open": True}]}
elif args[0] == "tree":
    if state.get("treeFailure"):
        fail("test window tree unavailable")
    result = {"tree": {"workspaces": [{"id": "work", "name": "work", "sessions": [
        {"id": state["session"], "title": state.get("title", ""), "active": True,
         **({"status": state["status"]} if "status" in state else {})},
        {"id": state["other"], "status": "idle", "active": False}
    ]}]}}
elif args[:2] == ["session", "select"]:
    if state.get("sessionFailure"):
        fail("session was closed")
    state["selectedSession"] = args[args.index("--target") + 1]
    state_path.write_text(json.dumps(state))
    result = {}
elif args[:2] == ["window", "select"]:
    if state.get("windowFailure"):
        fail("compositor refused window focus")
    state["selectedWindow"] = args[2]
    state_path.write_text(json.dumps(state))
    result = {}
else:
    fail("unsupported test command")
print(json.dumps({"ok": True, "result": result}))
'''


class BridgeProcessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cli = self.root / "agtermctl with spaces"
        self.cli.write_text(FAKE_CLI)
        self.cli.chmod(0o755)
        self.state = {"session": SESSION, "other": OTHER, "window": WINDOW, "revision": 0}
        self.write_state()
        self.env = dict(os.environ, TEST_AGTERM_ROOT=str(self.root),
                        AGTERM_SOCKET=str(self.root / "isolated socket"),
                        XDG_CONFIG_HOME=str(self.root / "config"),
                        XDG_CACHE_HOME=str(self.root / "cache"),
                        PYTHONDONTWRITEBYTECODE="1")

    def write_state(self, **changes):
        self.state.update(changes)
        self.state["revision"] += 1
        temporary = self.root / "state.next"
        temporary.write_text(json.dumps(self.state))
        temporary.replace(self.root / "state.json")

    def read_usage(self):
        path = self.root / "cache" / bridge.CACHE_SUBDIR / bridge.CACHE_FILE
        return json.loads(path.read_text()) if path.exists() else {}

    def run_focus(self):
        return subprocess.run(
            [sys.executable, str(BRIDGE_PATH), "focus", WINDOW, OTHER, "--cli", str(self.cli)],
            env=self.env, capture_output=True, text=True, timeout=10,
        )

    def test_focus_records_usage_only_after_both_selections_succeed(self):
        for failure, message in (("sessionFailure", "session was closed"),
                                 ("windowFailure", "compositor refused window focus")):
            with self.subTest(failure=failure):
                self.write_state(**{"sessionFailure": False, "windowFailure": False, failure: True})
                result = self.run_focus()
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertNotIn(OTHER, self.read_usage())
                received = json.loads((self.root / "state.json").read_text())
                self.assertNotIn("selectedWindow", received)
                if failure == "sessionFailure":
                    self.assertNotIn("selectedSession", received)
        self.write_state(sessionFailure=False, windowFailure=False)
        before = time.time()
        result = self.run_focus()
        self.assertEqual(result.returncode, 0, result.stderr)
        received = json.loads((self.root / "state.json").read_text())
        self.assertEqual(received["selectedSession"], OTHER)
        self.assertEqual(received["selectedWindow"], WINDOW)
        self.assertGreaterEqual(self.read_usage()[OTHER], before)
        self.assertLessEqual(self.read_usage()[OTHER], time.time())

    def start_watch(self):
        process = subprocess.Popen(
            [sys.executable, str(BRIDGE_PATH), "watch", "--cli", str(self.cli)],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        messages = queue.Queue()

        def collect():
            for line in process.stdout:
                messages.put(json.loads(line))

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()

        def cleanup():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            reader.join(timeout=2)
            process.stdout.close()
            process.stderr.close()

        self.addCleanup(cleanup)
        return process, messages

    def await_snapshot(self, messages, predicate):
        seen = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                value = messages.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            seen.append(value)
            if predicate(value):
                return value
        self.fail(f"Expected snapshot not received; observed {seen!r}")

    def test_missing_cli_then_socket_failure_and_recovery_preserve_recency(self):
        self.cli.unlink()
        process, messages = self.start_watch()
        missing = self.await_snapshot(messages, lambda value: not value["online"])
        self.assertIn("not executable", missing["error"])
        self.write_state(offline=True)
        self.cli.write_text(FAKE_CLI)
        self.cli.chmod(0o755)
        offline = self.await_snapshot(messages, lambda value: "test socket unavailable" in value.get("error", ""))
        self.assertEqual(offline["sessions"], [])
        self.write_state(offline=False, status="active")
        recovered = self.await_snapshot(messages, lambda value: value["online"] and value["sessions"][0]["status"] == "active")
        initial_usage = self.read_usage()[SESSION]
        self.write_state(status="blocked", eventKind="status")
        self.await_snapshot(messages, lambda value: value["online"] and value["sessions"][0]["status"] == "blocked")
        self.assertEqual(self.read_usage()[SESSION], initial_usage)
        self.write_state(treeFailure=True, eventKind="notify")
        failed_tree = self.await_snapshot(messages, lambda value: not value["online"] and "tree unavailable" in value["error"])
        self.assertEqual(failed_tree["sessions"], [])
        self.write_state(treeFailure=False, status="completed")
        self.await_snapshot(messages, lambda value: value["online"] and value["sessions"][0]["status"] == "completed")
        self.assertEqual(self.read_usage()[SESSION], initial_usage)
        self.assertEqual(recovered["windowCount"], 1)
        process.terminate()
        process.wait(timeout=4)
        for pid in (self.root / "subscriber-pids").read_text().splitlines():
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid), 0)

    def test_event_failure_keeps_snapshot_rows_and_recovers_monitoring(self):
        self.write_state(eventFailure=True, status="blocked")
        _, messages = self.start_watch()
        degraded = self.await_snapshot(messages, lambda value: value.get("eventWarning") and value["online"])
        self.assertEqual(degraded["sessions"][0]["id"], SESSION)
        self.assertEqual(degraded["sessions"][0]["status"], "blocked")
        self.write_state(eventFailure=False, status="completed")
        recovered = self.await_snapshot(messages, lambda value: value["online"] and not value.get("eventWarning") and value["sessions"][0]["status"] == "completed")
        self.assertEqual(recovered["error"], "")

    def test_watch_tracks_title_activity_without_status_or_recency_writes(self):
        self.write_state(title="π ⠋ Agent", eventKind="tree.changed")
        _, messages = self.start_watch()
        self.await_snapshot(messages, lambda value: value["online"] and value["sessions"][0]["status"] == "active")
        initial_usage = self.read_usage()[SESSION]
        self.write_state(title="π - Agent")
        stopped = self.await_snapshot(messages, lambda value: value["online"] and all(row["status"] == "idle" for row in value["sessions"]))
        self.assertEqual({row["id"]: row["status"] for row in stopped["sessions"]},
                         {SESSION: "idle", OTHER: "idle"})
        self.write_state(title="π ⠏ Agent", status="blocked")
        self.await_snapshot(messages, lambda value: value["online"] and value["sessions"][0]["status"] == "blocked")
        self.assertEqual(self.read_usage()[SESSION], initial_usage)

    def test_concurrent_stale_cache_writer_cannot_erase_newer_focus(self):
        # First writer pauses after reading the cache inside its exclusive lock.
        # Second writer announces its lock attempt before the first is released.
        worker = r'''
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bridge", sys.argv[1])
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
role = sys.argv[2]
if role == "first":
    original = bridge.load_usage
    def load():
        value = original()
        print("loaded", flush=True)
        sys.stdin.readline()
        return value
    bridge.load_usage = load
    bridge.save_usage({sys.argv[3]: 500.0})
else:
    original = bridge.fcntl.flock
    def flock(file, operation):
        if operation == bridge.fcntl.LOCK_EX:
            print("locking", flush=True)
        return original(file, operation)
    bridge.fcntl.flock = flock
    bridge.save_usage({sys.argv[3]: 100.0, sys.argv[4]: 200.0})
'''
        processes = []
        try:
            first = subprocess.Popen(
                [sys.executable, "-c", worker, str(BRIDGE_PATH), "first", SESSION],
                env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            processes.append(first)
            self.assertTrue(select.select([first.stdout], [], [], 5)[0], "First writer did not reach cache read")
            self.assertEqual(first.stdout.readline().strip(), "loaded")
            second = subprocess.Popen(
                [sys.executable, "-c", worker, str(BRIDGE_PATH), "second", SESSION, OTHER],
                env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            processes.append(second)
            self.assertTrue(select.select([second.stdout], [], [], 5)[0], "Second writer did not attempt cache lock")
            self.assertEqual(second.stdout.readline().strip(), "locking")
            first.stdin.write("continue\n")
            first.stdin.flush()
            for process in processes:
                _, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
            usage = self.read_usage()
            self.assertEqual(usage[SESSION], 500.0)
            self.assertEqual(usage[OTHER], 200.0)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
