"""Tests for the improved Claude Code tool (pane-based)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nanobot.agent.tools.claude_code import ClaudeCodeTool, _generate_id


def _make_tool(workspace: Path) -> ClaudeCodeTool:
    return ClaudeCodeTool(workspace=workspace)


# ── Basic tool properties ────────────────────────────────────────────────

def test_tool_name_and_description(tmp_path):
    tool = _make_tool(tmp_path)
    assert tool.name == "claude_code"
    assert "tmux panes" in tool.description.lower()


def test_to_schema(tmp_path):
    tool = _make_tool(tmp_path)
    schema = tool.to_schema()
    assert schema["function"]["name"] == "claude_code"
    params = schema["function"]["parameters"]
    assert "action" in params["properties"]
    assert set(params["properties"]["action"]["enum"]) == {
        "create",
        "list",
        "resume",
        "status",
        "archive",
    }


def test_set_context(tmp_path):
    tool = _make_tool(tmp_path)
    tool.set_context("telegram", "123456")
    assert tool._channel == "telegram"
    assert tool._chat_id == "123456"


# ── Session ID generation ─────────────────────────────────────────────────

def test_generate_id_format():
    session_id = _generate_id()
    assert session_id.startswith("cc-")
    parts = session_id.split("-")
    assert len(parts) == 4  # cc-YYYYMMDD-HHMMSS-random
    assert len(parts[1]) == 8  # date
    assert len(parts[2]) == 6  # time
    assert len(parts[3]) == 6  # random


# ── Storage ───────────────────────────────────────────────────────────────

def test_load_sessions_missing_file(tmp_path):
    tool = _make_tool(tmp_path)
    sessions = tool._load_sessions()
    assert sessions == []


def test_load_sessions_corrupt_json(tmp_path):
    tool = _make_tool(tmp_path)
    storage_dir = tmp_path / "claude-code"
    storage_dir.mkdir()
    (storage_dir / "sessions.json").write_text("{invalid json")
    sessions = tool._load_sessions()
    assert sessions == []


def test_save_and_load_roundtrip(tmp_path):
    tool = _make_tool(tmp_path)
    sessions = [
        {
            "id": "cc-test-1",
            "purpose": "Test",
            "workspace_path": "/tmp/test",
            "tmux_pane_id": "%1",
            "status": "active",
        }
    ]
    tool._save_sessions(sessions)
    loaded = tool._load_sessions()
    assert loaded == sessions


# ── Create session (in tmux) ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_validates_workspace(tmp_path):
    tool = _make_tool(tmp_path)
    with patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,123,0"}):
        result = await tool.execute(
            action="create",
            purpose="Test",
            workspace_path="/nonexistent/path"
        )
        assert "Error" in result
        assert "does not exist" in result


@pytest.mark.asyncio
async def test_create_in_tmux_success(tmp_path):
    tool = _make_tool(tmp_path)
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    with (
        patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,123,0"}),
        patch("subprocess.run") as mock_run,
    ):
        # Mock split-window returning pane ID
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "%10\n"
        mock_run.return_value = mock_result

        result = await tool.execute(
            action="create",
            purpose="Test session",
            workspace_path=str(work_dir),
            message="Hello"
        )

        assert "Created Claude Code session" in result
        assert "cc-" in result
        assert "%10" in result
        assert "split pane" in result

        # Verify split-window was called
        calls = [str(call) for call in mock_run.call_args_list]
        split_calls = [c for c in calls if "split-window" in c]
        assert len(split_calls) > 0


@pytest.mark.asyncio
async def test_create_not_in_tmux(tmp_path):
    tool = _make_tool(tmp_path)
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    with (
        patch.dict("os.environ", {}, clear=True),
        patch.object(ClaudeCodeTool, "_check_binary", return_value=None),
    ):
        result = await tool.execute(
            action="create",
            purpose="Test",
            workspace_path=str(work_dir)
        )
        assert "Error" in result
        assert "not currently in a tmux session" in result.lower()


# ── List sessions ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_empty_sessions(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.execute(action="list")
    assert "No Claude Code sessions" in result


@pytest.mark.asyncio
async def test_list_sessions(tmp_path):
    tool = _make_tool(tmp_path)
    sessions = [
        {
            "id": "cc-1",
            "purpose": "First",
            "workspace_path": "/tmp/1",
            "tmux_pane_id": "%1",
            "status": "active",
            "created_at_ms": 1000000,
            "last_used_at_ms": 1000000,
        },
        {
            "id": "cc-2",
            "purpose": "Second",
            "workspace_path": "/tmp/2",
            "tmux_pane_id": "%2",
            "status": "detached",
            "created_at_ms": 2000000,
            "last_used_at_ms": 2000000,
        },
    ]
    tool._save_sessions(sessions)

    with patch.object(ClaudeCodeTool, "_list_panes", return_value={"%1"}):
        result = await tool.execute(action="list")
        assert "cc-1" in result
        assert "cc-2" in result
        assert "First" in result
        assert "Second" in result


@pytest.mark.asyncio
async def test_list_filter_by_status(tmp_path):
    tool = _make_tool(tmp_path)
    sessions = [
        {
            "id": "cc-active",
            "purpose": "Active",
            "tmux_pane_id": "%1",
            "status": "active",
            "created_at_ms": 1000000,
            "last_used_at_ms": 1000000,
        },
        {
            "id": "cc-archived",
            "purpose": "Archived",
            "tmux_pane_id": "%2",
            "status": "archived",
            "created_at_ms": 2000000,
            "last_used_at_ms": 2000000,
        },
    ]
    tool._save_sessions(sessions)

    with patch.object(ClaudeCodeTool, "_list_panes", return_value={"%1"}):
        result = await tool.execute(action="list", status="active")
        assert "cc-active" in result
        assert "cc-archived" not in result


# ── Resume session ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resume_requires_session_id(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.execute(action="resume", session_id="")
    assert "Error" in result
    assert "required" in result


@pytest.mark.asyncio
async def test_resume_no_sessions(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.execute(action="resume", session_id="cc-fake")
    assert "Error" in result
    assert "no sessions found" in result


@pytest.mark.asyncio
async def test_resume_exact_match(tmp_path):
    tool = _make_tool(tmp_path)
    sessions = [
        {
            "id": "cc-test",
            "purpose": "Test",
            "workspace_path": "/tmp/test",
            "tmux_pane_id": "%5",
            "status": "active",
            "created_at_ms": 1000000,
            "last_used_at_ms": 1000000,
        }
    ]
    tool._save_sessions(sessions)

    with patch.object(ClaudeCodeTool, "_pane_exists", return_value=True):
        result = await tool.execute(action="resume", session_id="cc-test")
        assert "cc-test is active" in result
        assert "%5" in result
        assert "select-pane" in result


@pytest.mark.asyncio
async def test_resume_pane_gone(tmp_path):
    tool = _make_tool(tmp_path)
    sessions = [
        {
            "id": "cc-test",
            "purpose": "Test",
            "workspace_path": "/tmp/test",
            "tmux_pane_id": "%5",
            "status": "active",
            "created_at_ms": 1000000,
            "last_used_at_ms": 1000000,
        }
    ]
    tool._save_sessions(sessions)

    with patch.object(ClaudeCodeTool, "_pane_exists", return_value=False):
        result = await tool.execute(action="resume", session_id="cc-test")
        assert "Error" in result
        assert "no longer exists" in result
        assert "detached" in result


# ── Status ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_session(tmp_path):
    tool = _make_tool(tmp_path)
    sessions = [
        {
            "id": "cc-test",
            "purpose": "Test",
            "workspace_path": "/tmp/test",
            "tmux_pane_id": "%3",
            "status": "active",
            "created_at_ms": 1000000,
            "last_used_at_ms": 1000000,
        }
    ]
    tool._save_sessions(sessions)

    with (
        patch.object(ClaudeCodeTool, "_pane_exists", return_value=True),
        patch.object(ClaudeCodeTool, "_capture_pane", return_value="Recent output"),
    ):
        result = await tool.execute(action="status", session_id="cc-test")
        assert "Session: cc-test" in result
        assert "Status: active" in result
        assert "Pane ID: %3" in result
        assert "Pane alive: yes" in result
        assert "Recent output" in result


# ── Archive ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_archive_session(tmp_path):
    tool = _make_tool(tmp_path)
    sessions = [
        {
            "id": "cc-test",
            "purpose": "Test",
            "tmux_pane_id": "%1",
            "status": "active",
            "created_at_ms": 1000000,
            "last_used_at_ms": 1000000,
        }
    ]
    tool._save_sessions(sessions)

    result = await tool.execute(action="archive", session_id="cc-test")
    assert "archived" in result

    # Verify status updated
    updated = tool._load_sessions()
    assert updated[0]["status"] == "archived"


# ── Unknown action ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_action(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.execute(action="invalid")
    assert "Error" in result
    assert "Unknown action" in result
