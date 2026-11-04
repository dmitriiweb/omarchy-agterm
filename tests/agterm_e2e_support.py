"""Isolated helpers for opt-in tests against a real agterm instance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

BRIDGE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agterm_bridge.py"
ROLES = ("working_a", "working_b", "attention", "finished", "idle")
DEFAULT_STATES = {
    "working_a": "active",
    "working_b": "active",
    "attention": "blocked",
    "finished": "completed",
    "idle": "idle",
}
VALID_STATES = frozenset(("active", "blocked", "completed", "idle"))


class AgtermScenario:
    """Own one synthetic workspace and its sessions, never pre-existing targets."""

    def __init__(self, cli: str, socket: str, window: str):
        if not cli or not socket or not window:
            raise ValueError("cli, socket, and window must be explicit")
        self.cli = cli
        self.socket = socket
        self.window = window
        self.workspace_id = None
        self.sessions = {}
        self._roles = {}
        self._panes = {}
        self._temporary = tempfile.TemporaryDirectory(prefix="agterm-e2e-")
        root = Path(self._temporary.name)
        self.env = os.environ.copy()
        self.env.update({
            "AGTERM_SOCKET": socket,
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_CACHE_HOME": str(root / "cache"),
        })
        self._cwd = root / "cwd"
        self._cwd.mkdir()
        self._workspace_deleted = False
        self._cleaned = False

    def ctl(self, *args: str) -> dict:
        """Run one bounded native CLI request with explicit socket/window isolation."""
        arguments = list(map(str, args))
        if arguments and arguments[0] in {"tree", "session", "workspace"} and "--window" not in arguments:
            arguments.extend(("--window", self.window))
        command = [self.cli, *arguments, "--socket", self.socket, "--json"]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
            env=self.env,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "agtermctl failed").strip()
            raise RuntimeError(f"agtermctl {' '.join(command[1:])} failed: {detail[:500]}")
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("agtermctl returned invalid JSON") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise RuntimeError(f"agtermctl returned an unsuccessful response: {response!r}")
        value = response.get("result", {})
        if not isinstance(value, dict):
            raise RuntimeError("agtermctl result was not an object")
        return value

    def create(self) -> "AgtermScenario":
        """Create a uniquely named collapsed workspace and five non-selected sessions."""
        if self.workspace_id is not None:
            raise RuntimeError("scenario has already been created")
        name = f"E2E {uuid.uuid4()}"
        result = self.ctl("workspace", "new", name, "--collapsed", "--window", self.window)
        workspace_id = result.get("id")
        if not isinstance(workspace_id, str) or not workspace_id:
            raise RuntimeError("workspace.new did not return a workspace UUID")
        self.workspace_id = workspace_id
        try:
            for role in ROLES:
                session_name = f"E2E {role} {uuid.uuid4().hex[:8]}"
                created = self.ctl(
                    "session", "new",
                    "--workspace", workspace_id,
                    "--name", session_name,
                    "--cwd", str(self._cwd),
                    "--command", "sleep 3600",
                    "--no-select",
                    "--window", self.window,
                )
                session_id = created.get("id")
                if not isinstance(session_id, str) or not session_id:
                    raise RuntimeError(f"session.new for {role} did not return a session UUID")
                self.sessions[role] = session_id
                self._roles[session_id] = role
                self._panes[role] = "left"
            self.set_states(DEFAULT_STATES)
        except BaseException:
            try:
                self.cleanup()
            except BaseException as cleanup_error:
                raise RuntimeError(f"scenario creation failed and cleanup failed: {cleanup_error}")
            raise
        return self

    def split(self, role: str) -> None:
        """Enable a right split on one of this scenario's sessions."""
        session_id = self._session_id(role)
        self.ctl("session", "split", "visibility", "on", "--target", session_id, "--window", self.window)
        self._panes[role] = "right"

    def set_states(self, mapping: dict[str, str]) -> None:
        """Set requested fixture statuses only; roles may be supplied partially."""
        for role, state in mapping.items():
            session_id = self._session_id(role)
            if state not in VALID_STATES:
                raise ValueError(f"unsupported agterm status: {state}")
            self.ctl(
                "session", "status", state,
                "--target", session_id,
                "--pane", self._panes[role],
                "--window", self.window,
            )

    def snapshot(self) -> dict:
        """Return one bridge snapshot using isolated cache/config and explicit CLI."""
        result = subprocess.run(
            [sys.executable, str(BRIDGE_PATH), "snapshot", "--cli", self.cli],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=self.env,
        )
        if result.returncode:
            raise RuntimeError(f"bridge snapshot failed: {(result.stderr or result.stdout).strip()[:500]}")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("bridge snapshot returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("bridge snapshot was not an object")
        return value

    def cleanup(self) -> None:
        """Delete only the owned workspace UUID, surfacing any cleanup failure."""
        if self._cleaned:
            return
        failure = None
        if self.workspace_id is not None and not self._workspace_deleted:
            try:
                self.ctl("workspace", "delete", "--target", self.workspace_id, "--window", self.window)
                self._workspace_deleted = True
            except BaseException as exc:
                failure = exc
        try:
            self._temporary.cleanup()
        except BaseException as exc:
            failure = failure or exc
        self._cleaned = failure is None
        if failure is not None:
            raise RuntimeError(f"failed to clean agterm E2E workspace {self.workspace_id}: {failure}") from failure

    def _session_id(self, role: str) -> str:
        if role not in self.sessions:
            raise KeyError(f"unknown or uncreated scenario role: {role}")
        session_id = self.sessions[role]
        if self._roles.get(session_id) != role:
            raise RuntimeError("refusing to mutate a session outside this scenario")
        return session_id
