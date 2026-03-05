"""
Redesigned Claude Code session management - uses tmux split panes instead of nested sessions.

Key improvements:
1. Detects if already in tmux and creates split panes (right side)
2. Auto-accepts workspace trust dialog
3. Tracks both session-based and pane-based Claude Code instances
"""

import json
import os
import random
import shutil
import string
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool

SESSIONS_DIR = "claude-code"
SESSIONS_FILE = "sessions.json"


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _generate_id() -> str:
    now = datetime.now(timezone.utc)
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"cc-{now.strftime('%Y%m%d-%H%M%S')}-{rand}"


class ClaudeCodeTool(Tool):
    """Improved Claude Code tool that uses tmux split panes."""

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._storage_dir = workspace / SESSIONS_DIR
        self._storage_file = self._storage_dir / SESSIONS_FILE
        self._channel = ""
        self._chat_id = ""

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the current session context."""
        self._channel = channel
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "claude_code"

    @property
    def description(self) -> str:
        return (
            "Manage Claude Code sessions in tmux panes. Actions: "
            "create (start Claude Code in a new tmux pane), "
            "list (show all sessions), "
            "resume (switch to an existing pane), "
            "status (check pane status), "
            "archive (mark as archived)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "resume", "status", "archive"],
                    "description": "Action to perform",
                },
                "purpose": {
                    "type": "string",
                    "description": "Purpose/task description (for create)",
                },
                "workspace_path": {
                    "type": "string",
                    "description": "Working directory (for create)",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session ID (for resume/status/archive)",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "detached", "archived", "all"],
                    "description": "Filter by status (for list, default: all)",
                },
                "message": {
                    "type": "string",
                    "description": "Initial prompt for Claude Code (for create)",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        purpose: str = "",
        workspace_path: str = "",
        session_id: str = "",
        status: str = "all",
        message: str = "",
        **kwargs: Any,
    ) -> str:
        if action == "create":
            return self._create_session(purpose, workspace_path, message)
        elif action == "list":
            return self._list_sessions(status)
        elif action == "resume":
            return self._resume_session(session_id)
        elif action == "status":
            return self._status_session(session_id)
        elif action == "archive":
            return self._archive_session(session_id)
        return f"Error: Unknown action '{action}'"

    # ── Storage ──────────────────────────────────────────────────────

    def _load_sessions(self) -> list[dict[str, Any]]:
        if not self._storage_file.exists():
            return []
        try:
            data = json.loads(self._storage_file.read_text())
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save_sessions(self, sessions: list[dict[str, Any]]) -> None:
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._storage_file.write_text(json.dumps(sessions, indent=2))

    # ── Validation ────────────────────────────────────────────────────

    @staticmethod
    def _check_binary(name: str) -> str | None:
        """Return error if binary not found."""
        if shutil.which(name) is None:
            return f"Error: '{name}' is not installed or not in PATH"
        return None

    def _find_session(
        self, session_id: str, sessions: list[dict[str, Any]]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Find session by exact or fuzzy match."""
        if not session_id:
            return None, "Error: session_id is required"
        if not sessions:
            return None, "Error: no sessions found"

        # Exact match
        for s in sessions:
            if s["id"] == session_id:
                return s, None

        # Fuzzy match
        candidates = [
            s
            for s in sessions
            if session_id in s["id"]
            or session_id.lower() in s.get("purpose", "").lower()
        ]
        if len(candidates) == 1:
            return candidates[0], None
        if len(candidates) > 1:
            lines = ["Multiple sessions match:"]
            for c in candidates:
                lines.append(f"  {c['id']} — {c['purpose']}")
            return None, "\n".join(lines)

        return None, f"Error: no session found matching '{session_id}'"

    # ── Tmux helpers ─────────────────────────────────────────────────

    @staticmethod
    def _is_in_tmux() -> bool:
        """Check if we're currently inside a tmux session."""
        return "TMUX" in os.environ

    @staticmethod
    def _get_current_pane() -> str:
        """Get current tmux pane ID."""
        return os.environ.get("TMUX_PANE", "")

    @staticmethod
    def _pane_exists(pane_id: str) -> bool:
        """Check if a tmux pane exists."""
        try:
            result = subprocess.run(
                ["tmux", "display-message", "-p", "-t", pane_id, "#{pane_id}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0 and result.stdout.strip() == pane_id
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    @staticmethod
    def _list_panes() -> set[str]:
        """Get all tmux pane IDs."""
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return {line.strip() for line in result.stdout.splitlines() if line.strip()}
            return set()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return set()

    @staticmethod
    def _capture_pane(pane_id: str, lines: int = 50) -> str | None:
        """Capture pane output."""
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-p", "-J", "-t", pane_id, "-S", str(-lines)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.rstrip()
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    # ── Actions ──────────────────────────────────────────────────────

    def _create_session(
        self,
        purpose: str,
        workspace_path: str,
        message: str,
    ) -> str:
        """Create a new Claude Code session in a tmux pane."""
        # Validate binaries
        for binary in ("tmux", "claude"):
            if err := self._check_binary(binary):
                return err

        session_id = _generate_id()
        work_dir = workspace_path or str(self._workspace)
        if not os.path.isdir(work_dir):
            return f"Error: workspace path does not exist: {work_dir}"

        # Determine if we should use split-window or new-session
        in_tmux = self._is_in_tmux()

        if in_tmux:
            # Create a split pane to the right
            try:
                result = subprocess.run(
                    [
                        "tmux", "split-window", "-h",
                        "-P", "-F", "#{pane_id}",
                        "-c", work_dir,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode != 0:
                    return f"Error: failed to create tmux pane: {result.stderr}"

                pane_id = result.stdout.strip()
                if not pane_id:
                    return "Error: failed to get pane ID"

            except subprocess.TimeoutExpired:
                return "Error: tmux pane creation timed out"
            except FileNotFoundError:
                return "Error: tmux is not installed or not in PATH"

            # Unset CLAUDECODE to allow nested sessions
            subprocess.run(
                ["tmux", "send-keys", "-t", pane_id, "unset CLAUDECODE", "Enter"],
                capture_output=True,
                timeout=5,
            )

            # Wait for prompt
            time.sleep(0.2)

            # Start Claude Code
            claude_cmd = "claude"
            if message:
                escaped = message.replace("'", "'\\''")
                claude_cmd = f"claude '{escaped}'"

            subprocess.run(
                ["tmux", "send-keys", "-t", pane_id, claude_cmd, "Enter"],
                capture_output=True,
                timeout=5,
            )

            # Auto-accept workspace trust dialog after 1.5 seconds
            time.sleep(1.5)
            subprocess.run(
                ["tmux", "send-keys", "-t", pane_id, "Enter"],
                capture_output=True,
                timeout=5,
            )

            # Save session metadata
            now = _now_ms()
            session_metadata = {
                "created_by_channel": self._channel or "unknown",
                "created_by_chat_id": self._chat_id or "unknown",
                "pane_id": pane_id,
                "type": "pane",
            }

            session_data = {
                "id": session_id,
                "purpose": purpose or "General session",
                "workspace_path": work_dir,
                "tmux_pane_id": pane_id,
                "tmux_type": "pane",
                "status": "active",
                "created_at_ms": now,
                "last_used_at_ms": now,
                "metadata": session_metadata,
            }

            sessions = self._load_sessions()
            sessions.append(session_data)
            self._save_sessions(sessions)

            return f"""Created Claude Code session: {session_id}
  Purpose: {purpose or 'General session'}
  Workspace: {work_dir}
  Pane ID: {pane_id}

The Claude Code session is running in a split pane to the right.
Switch to it with: tmux select-pane -t {pane_id}
Session ID: {session_id} (use for status/resume)"""

        else:
            # Fallback: not in tmux, return instruction
            return (
                "Error: Not currently in a tmux session. "
                "Please start tmux first or run nanobot inside tmux."
            )

    def _list_sessions(self, status_filter: str) -> str:
        sessions = self._load_sessions()
        if not sessions:
            return "No Claude Code sessions found."

        # Sync status with tmux reality
        live_panes = self._list_panes()
        for s in sessions:
            pane_id = s.get("tmux_pane_id")
            if s["status"] == "active" and pane_id and pane_id not in live_panes:
                s["status"] = "detached"
        self._save_sessions(sessions)

        if status_filter and status_filter != "all":
            sessions = [s for s in sessions if s["status"] == status_filter]

        if not sessions:
            return f"No sessions with status '{status_filter}'."

        # Sort by last_used descending
        sessions.sort(key=lambda s: s.get("last_used_at_ms", 0), reverse=True)

        lines = ["Claude Code sessions:"]
        for s in sessions:
            created = datetime.fromtimestamp(
                s["created_at_ms"] / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M")
            pane_info = s.get("tmux_pane_id", "N/A")
            lines.append(
                f"  [{s['status']}] {s['id']} — {s['purpose']} (pane: {pane_info}, created {created})"
            )
        return "\n".join(lines)

    def _resume_session(self, session_id: str) -> str:
        sessions = self._load_sessions()
        match, err = self._find_session(session_id, sessions)
        if err:
            return err

        if match["status"] == "archived":
            return f"Error: session '{match['id']}' is archived."

        pane_id = match.get("tmux_pane_id")
        if not pane_id:
            return "Error: session has no pane ID"

        if not self._pane_exists(pane_id):
            match["status"] = "detached"
            self._save_sessions(sessions)
            return f"Error: pane {pane_id} no longer exists (status updated to detached)"

        # Update timestamp
        match["last_used_at_ms"] = _now_ms()
        match["status"] = "active"
        self._save_sessions(sessions)

        return f"""Session {match['id']} is active.
  Purpose: {match['purpose']}
  Workspace: {match['workspace_path']}
  Pane: {pane_id}

To switch to this pane:
  tmux select-pane -t {pane_id}"""

    def _status_session(self, session_id: str) -> str:
        sessions = self._load_sessions()
        match, err = self._find_session(session_id, sessions)
        if err:
            return err

        pane_id = match.get("tmux_pane_id")
        alive = self._pane_exists(pane_id) if pane_id else False

        # Update status
        if match["status"] != "archived":
            if alive:
                match["status"] = "active"
                match["last_used_at_ms"] = _now_ms()
            elif match["status"] == "active":
                match["status"] = "detached"
        self._save_sessions(sessions)

        created = datetime.fromtimestamp(
            match["created_at_ms"] / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
        last_used = datetime.fromtimestamp(
            match["last_used_at_ms"] / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            f"Session: {match['id']}",
            f"  Status: {match['status']}",
            f"  Purpose: {match['purpose']}",
            f"  Workspace: {match['workspace_path']}",
            f"  Pane ID: {pane_id or 'N/A'}",
            f"  Created: {created}",
            f"  Last used: {last_used}",
            f"  Pane alive: {'yes' if alive else 'no'}",
        ]

        if alive and pane_id:
            output = self._capture_pane(pane_id)
            if output:
                recent = [ln for ln in output.splitlines() if ln.strip()][-20:]
                lines.append("")
                lines.append("Recent output:")
                lines.extend(f"  {ln}" for ln in recent)

        return "\n".join(lines)

    def _archive_session(self, session_id: str) -> str:
        sessions = self._load_sessions()
        match, err = self._find_session(session_id, sessions)
        if err:
            return err

        if match["status"] == "archived":
            return f"Session {match['id']} is already archived."

        match["status"] = "archived"
        match["last_used_at_ms"] = _now_ms()
        self._save_sessions(sessions)

        return f"Session {match['id']} archived."
