import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest

from agterm_e2e_support import AgtermScenario


REPOSITORY = Path(__file__).resolve().parents[1]

HARNESS = '''import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
  id: harness
  readonly property var fixtureIds: FIXTURE_IDS
  readonly property string fixtureWindow: WINDOW_ID

  PanelWindow {
    id: testBar
    anchors.bottom: true
    anchors.right: true
    implicitWidth: widget.implicitWidth
    implicitHeight: widget.implicitHeight
    exclusionMode: ExclusionMode.Ignore
    color: "#24283b"
    WlrLayershell.namespace: "agterm-widget-e2e"

    WidgetUnderTest {
      id: widget
      anchors.fill: parent
      settings: ({cliPath: CLI_PATH})
      // Isolate this view from unrelated, concurrently running user sessions.
      // Data still comes from the real widget watcher and real agterm socket.
      onStateChanged: {
        var rows = state.sessions || []
        var fixtureRows = rows.filter(function(row) { return harness.fixtureIds.indexOf(row.id) !== -1 })
        if (state.online && rows.length !== fixtureRows.length)
          state = Object.assign({}, state, {sessions: fixtureRows})
      }
    }
  }

  function texts(node, visited, result) {
    if (!node || visited.indexOf(node) !== -1) return
    visited.push(node)
    if (node.activeFocus === true) result.keyboardFocused = true
    if (typeof node.text === "string") result.push(node.text)
    if (node.children) {
      for (var i = 0; i < node.children.length; i++) texts(node.children[i], visited, result)
    }
    if (node.resources) {
      for (var j = 0; j < node.resources.length; j++) texts(node.resources[j], visited, result)
    }
    if (node.contentItem && node.contentItem.length !== undefined) {
      for (var k = 0; k < node.contentItem.length; k++) texts(node.contentItem[k], visited, result)
    } else if (node.contentItem) {
      texts(node.contentItem, visited, result)
    }
  }

  IpcHandler {
    target: "agterm-widget-e2e"
    function inspect(): string {
      var labels = []
      harness.texts(widget, [], labels)
      return JSON.stringify({
        online: widget.online, counts: widget.counts, sessions: widget.sessions,
        visibleSessions: widget.visibleSessions, opened: widget.opened,
        focusError: widget.focusError, labels: labels, keyboardFocused: labels.keyboardFocused === true
      })
    }
    function open(): void { widget.open() }
    function close(): void { widget.close() }
    function attention(): void { widget.focusAttention() }
    function refresh(): void { widget.refresh() }
    function focusTarget(sessionId: string): void {
      widget.focusSession({id: sessionId, windowId: harness.fixtureWindow})
    }
  }
}
'''


@unittest.skipUnless(os.environ.get("AGTERM_E2E") == "1" and os.environ.get("AGTERM_E2E_NATIVE") == "1",
                     "Native E2E requires AGTERM_E2E=1 AGTERM_E2E_NATIVE=1 and a Wayland desktop")
class NativeWidgetE2ETests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(shutil.which("qs"), "Native E2E requires qs")
        self.assertTrue(os.environ.get("WAYLAND_DISPLAY"), "Native E2E requires WAYLAND_DISPLAY")
        self.assertTrue(os.environ.get("AGTERM_SOCKET"), "Set an explicit AGTERM_SOCKET")
        self.assertTrue(os.environ.get("AGTERM_WINDOW_ID"), "Set an explicit AGTERM_WINDOW_ID")
        cli = os.environ.get("AGTERMCTL") or shutil.which("agtermctl")
        self.assertTrue(cli, "agtermctl is required")
        self.scenario = AgtermScenario(cli, os.environ["AGTERM_SOCKET"], os.environ["AGTERM_WINDOW_ID"])
        self.addCleanup(self.scenario.cleanup)
        self.scenario.create()
        self.original_selection = next(
            (row["id"] for workspace in self.scenario.ctl("tree")["tree"]["workspaces"]
             for row in workspace.get("sessions", []) if row.get("active")), None,
        )
        self.addCleanup(self.restore_selection)
        root = Path(self.scenario.env["XDG_CONFIG_HOME"]).parent / "qml"
        root.mkdir(parents=True)
        omarchy = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "shell"
        for name in ("Ui", "Commons"):
            self.assertTrue((omarchy / name).is_dir(), f"Missing native Omarchy module: {name}")
            (root / name).symlink_to(omarchy / name, target_is_directory=True)
        shutil.copyfile(REPOSITORY / "BarWidget.qml", root / "WidgetUnderTest.qml")
        for name in ("scripts", "assets"):
            (root / name).symlink_to(REPOSITORY / name, target_is_directory=True)
        source = HARNESS.replace("FIXTURE_IDS", json.dumps(list(self.scenario.sessions.values())))
        source = source.replace("CLI_PATH", json.dumps(str(cli)))
        source = source.replace("WINDOW_ID", json.dumps(self.scenario.window))
        self.qml = root / "shell.qml"
        self.qml.write_text(source)
        self.log = (root / "quickshell.log").open("w+")
        self.addCleanup(self.log.close)
        self.process = subprocess.Popen(
            ["qs", "--path", str(self.qml), "--no-color"], env=self.scenario.env,
            stdout=self.log, stderr=subprocess.STDOUT, text=True,
        )
        self.addCleanup(self.stop_harness)
        self.await_view(lambda view: view["online"] and len(view["sessions"]) == 5)

    def restore_selection(self):
        if self.original_selection:
            self.scenario.ctl("session", "select", "--target", self.original_selection)

    def stop_harness(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)

    def ipc(self, method, *arguments):
        result = subprocess.run(
            ["qs", "ipc", "--path", str(self.qml), "call", "agterm-widget-e2e", method, *arguments],
            env=self.scenario.env, capture_output=True, text=True, timeout=3,
        )
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        if method == "inspect":
            return json.loads(result.stdout)
        return result.stdout

    def await_view(self, predicate):
        deadline = time.monotonic() + 12
        last = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.log.flush()
                self.log.seek(0)
                self.fail("Native QML harness exited:\n" + self.log.read())
            try:
                last = self.ipc("inspect")
                if predicate(last):
                    return last
            except (RuntimeError, json.JSONDecodeError) as error:
                last = str(error)
            time.sleep(0.1)
        self.log.flush()
        self.log.seek(0)
        self.fail(f"Native widget state did not converge: {last!r}; QML log={self.log.read()[-4000:]}")

    def test_mixed_counts_render_and_attention_targets_blocked_not_recent_idle(self):
        # Give idle the newest plugin recency without changing its lifecycle state.
        result = subprocess.run(
            [os.sys.executable, str(REPOSITORY / "scripts/agterm_bridge.py"), "focus",
             self.scenario.window, self.scenario.sessions["idle"], "--cli", self.scenario.cli],
            env=self.scenario.env, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.scenario.set_states({"idle": "idle"})
        self.ipc("refresh")
        self.ipc("open")
        view = self.await_view(lambda value: value["opened"] and value["visibleSessions"][0]["id"] == self.scenario.sessions["idle"])
        self.assertEqual(view["counts"], {"blocked": 1, "completed": 1, "active": 2})
        self.assertEqual({row["id"]: row["status"] for row in view["sessions"]}, {
            self.scenario.sessions["working_a"]: "active",
            self.scenario.sessions["working_b"]: "active",
            self.scenario.sessions["attention"]: "blocked",
            self.scenario.sessions["finished"]: "completed",
            self.scenario.sessions["idle"]: "idle",
        })
        self.assertEqual(view["sessions"][0]["id"], self.scenario.sessions["attention"])
        labels = self.await_view(lambda value: value["keyboardFocused"] and all(
            text in value["labels"] for text in ("1 Blocked", "1 Done", "2 Working")))
        self.assertTrue(labels["opened"])
        self.ipc("attention")
        self.await_view(lambda value: not value["opened"])
        deadline = time.monotonic() + 3
        while True:
            tree = self.scenario.ctl("tree")["tree"]
            selected = [row["id"] for workspace in tree["workspaces"]
                        for row in workspace.get("sessions", []) if row.get("active")]
            if selected == [self.scenario.sessions["attention"]] or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        self.log.flush()
        self.log.seek(0)
        self.assertEqual(selected, [self.scenario.sessions["attention"]],
                         f"fixture roles={self.scenario.sessions!r}; QML log={self.log.read()[-4000:]}")
        self.assertEqual(self.ipc("inspect")["focusError"], "")
        self.scenario.set_states({"attention": "active", "working_b": "completed"})
        changed = self.await_view(lambda value: value["counts"] == {"blocked": 0, "completed": 2, "active": 2})
        self.assertEqual(next(row["status"] for row in changed["sessions"] if row["id"] == self.scenario.sessions["attention"]), "active")

    def test_closed_session_focus_keeps_popup_open_with_visible_error(self):
        target = self.scenario.sessions["attention"]
        self.ipc("open")
        self.await_view(lambda view: view["opened"])
        self.scenario.ctl("session", "close", "--target", target)
        self.ipc("focusTarget", target)
        failed = self.await_view(lambda view: bool(view["focusError"]))
        self.assertTrue(failed["opened"])
        self.assertIn(failed["focusError"], failed["labels"])
        self.assertTrue(failed["focusError"].startswith("Could not focus session: "))


if __name__ == "__main__":
    unittest.main()
