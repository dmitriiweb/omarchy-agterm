"""Opt-in end-to-end checks requiring a real agterm instance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import unittest
from agterm_e2e_support import AgtermScenario, BRIDGE_PATH


ENABLED = os.environ.get("AGTERM_E2E") == "1"
EXPECTED_DEFAULT_STATES = {
    "working_a": "active",
    "working_b": "active",
    "attention": "blocked",
    "finished": "completed",
    "idle": "idle",
}


def read_pipe(stream, name, messages):
    try:
        for line in iter(stream.readline, ""):
            messages.put((name, line))
    finally:
        messages.put((name, None))
        stream.close()


def fixture_state_map(scenario, snapshot):
    fixture_ids = set(scenario.sessions.values())
    rows = [row for row in snapshot.get("sessions", []) if row.get("id") in fixture_ids]
    if len(rows) != len(fixture_ids) or {row.get("id") for row in rows} != fixture_ids:
        raise AssertionError(f"expected one bridge row per fixture session; got {rows!r}")
    return {role: next(row["status"] for row in rows if row["id"] == session_id)
            for role, session_id in scenario.sessions.items()}, rows


def child_pids(pid):
    try:
        children = Path(f"/proc/{pid}/task/{pid}/children").read_text()
    except OSError:
        return set()
    return {int(value) for value in children.split()}

@unittest.skipUnless(ENABLED, "set AGTERM_E2E=1 to run against a real agterm instance")
class AgtermEndToEndTests(unittest.TestCase):
    def test_real_workspace_status_lifecycle_and_bridge_watch(self):
        socket = os.environ.get("AGTERM_SOCKET", "")
        window = os.environ.get("AGTERM_WINDOW_ID", "")
        if not socket or not window:
            self.fail("AGTERM_E2E=1 requires explicit AGTERM_SOCKET and AGTERM_WINDOW_ID")
        cli = os.environ.get("AGTERMCTL") or shutil.which("agtermctl")
        if not cli:
            self.fail("AGTERM_E2E=1 requires agtermctl on PATH or an AGTERMCTL executable path")

        scenario = AgtermScenario(cli, socket, window)
        self.addCleanup(scenario.cleanup)
        process = None
        readers = []
        subscriber_pids = set()
        messages = queue.Queue()
        try:
            scenario.create()
            scenario.set_states({"attention": "active"})
            scenario.split("attention")
            scenario.set_states(EXPECTED_DEFAULT_STATES)

            initial = scenario.snapshot()
            initial_map, initial_rows = fixture_state_map(scenario, initial)
            self.assertEqual(initial_map, EXPECTED_DEFAULT_STATES)
            ordered_fixture_ids = [row["id"] for row in initial["sessions"] if row["id"] in scenario.sessions.values()]
            self.assertEqual(ordered_fixture_ids, [
                scenario.sessions["attention"],
                scenario.sessions["finished"],
                scenario.sessions["working_a"],
                scenario.sessions["working_b"],
                scenario.sessions["idle"],
            ])
            attention_row = next(row for row in initial_rows if row["id"] == scenario.sessions["attention"])
            self.assertEqual(attention_row["pane"], "right")
            self.assertEqual(
                {state: list(initial_map.values()).count(state) for state in ("active", "blocked", "completed", "idle")},
                {"active": 2, "blocked": 1, "completed": 1, "idle": 1},
            )
            initial_recency = {row["id"]: row["lastUsedAt"] for row in initial_rows}

            process = subprocess.Popen(
                [sys.executable, str(BRIDGE_PATH), "watch", "--cli", scenario.cli],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=scenario.env,
            )
            for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                thread = threading.Thread(target=read_pipe, args=(stream, label, messages), daemon=True)
                thread.start()
                readers.append(thread)

            def wait_for_map(expected, timeout=12):
                deadline = time.monotonic() + timeout
                observed = []
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    try:
                        label, line = messages.get(timeout=max(0.01, remaining))
                    except queue.Empty:
                        break
                    if line is None:
                        continue
                    if label == "stderr":
                        observed.append(line.rstrip())
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        observed.append(line.rstrip())
                        continue
                    actual, rows = fixture_state_map(scenario, value)
                    if actual == expected:
                        self.assertEqual({row["id"]: row["lastUsedAt"] for row in rows}, initial_recency)
                        return value
                    observed.append(actual)
                self.fail(f"bridge watch did not emit {expected!r} within {timeout}s; observed {observed!r}")

            wait_for_map(EXPECTED_DEFAULT_STATES)
            deadline = time.monotonic() + 2
            while not subscriber_pids and time.monotonic() < deadline:
                subscriber_pids = child_pids(process.pid)
                if not subscriber_pids:
                    time.sleep(0.02)
            self.assertTrue(subscriber_pids, "bridge watch did not start its event subscriber")
            lifecycle = (
                ("blocked", {**EXPECTED_DEFAULT_STATES, "working_a": "blocked"}),
                ("active", EXPECTED_DEFAULT_STATES),
                ("completed", {**EXPECTED_DEFAULT_STATES, "working_a": "completed"}),
                ("idle", {**EXPECTED_DEFAULT_STATES, "working_a": "idle"}),
            )
            for state, expected in lifecycle:
                scenario.set_states({"working_a": state})
                wait_for_map(expected)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=4)
                for thread in readers:
                    thread.join(timeout=2)
                deadline = time.monotonic() + 2
                while any(Path(f"/proc/{pid}").exists() for pid in subscriber_pids) and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertFalse(
                    {pid for pid in subscriber_pids if Path(f"/proc/{pid}").exists()},
                    "bridge watch left an event subscriber running",
                )


if __name__ == "__main__":
    unittest.main()
