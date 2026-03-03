# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

nanobot is an ultra-lightweight personal AI assistant framework (~4,000 lines of core agent code). It's a Python project that provides:
- Multi-provider LLM support (OpenRouter, Anthropic, OpenAI, DeepSeek, local vLLM, etc.)
- Multiple chat platform integrations (Telegram, Discord, WhatsApp, Feishu, Matrix, Slack, Email, QQ, DingTalk, Mochat)
- MCP (Model Context Protocol) support for external tool servers
- Agent loop with tool execution, memory, skills, and session management
- Cron-based scheduled tasks and heartbeat system

## Development Commands

### Installation & Setup
```bash
# Install in editable mode for development
pip install -e .

# Install with optional dependencies (e.g., Matrix support)
pip install -e .[matrix]

# Install dev dependencies
pip install -e .[dev]

# Initialize config and workspace
nanobot onboard
```

### Testing
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_commands.py

# Run tests with async support
pytest -v  # pytest-asyncio is configured in pyproject.toml

# Run tests matching a pattern
pytest -k test_memory
```

### Code Quality
```bash
# Run linter (Ruff)
ruff check .

# Auto-fix linting issues
ruff check --fix .

# Format code
ruff format .
```

### Running nanobot
```bash
# Interactive chat mode
nanobot agent

# Single message
nanobot agent -m "Hello!"

# Show plain-text replies (no markdown rendering)
nanobot agent --no-markdown

# Show runtime logs during chat
nanobot agent --logs

# Start gateway (for chat channels)
nanobot gateway

# Check status
nanobot status
```

### Cron & Scheduled Tasks
```bash
# Add a cron job
nanobot cron add --name "daily" --message "Good morning!" --cron "0 9 * * *"

# Add recurring job (every N seconds)
nanobot cron add --name "hourly" --message "Check status" --every 3600

# List jobs
nanobot cron list

# Remove a job
nanobot cron remove <job_id>
```

### Provider Management
```bash
# OAuth login for providers (OpenAI Codex, GitHub Copilot)
nanobot provider login openai-codex
nanobot provider login github-copilot

# Link WhatsApp (scan QR code)
nanobot channels login

# Check channel status
nanobot channels status
```

## Architecture

### Core Components

**Agent Loop** (`nanobot/agent/loop.py`)
- The heart of nanobot: receives messages → builds context → calls LLM → executes tools → sends responses
- Manages max_iterations (default 40), temperature (default 0.1), and token limits
- Integrates ContextBuilder, SessionManager, ToolRegistry, and SubagentManager

**Provider Registry** (`nanobot/providers/registry.py`)
- Single source of truth for LLM provider metadata
- To add a new provider: (1) Add ProviderSpec to PROVIDERS tuple, (2) Add field to ProvidersConfig in config/schema.py
- Handles auto-prefixing, environment variables, gateway detection, and per-model overrides
- Order matters — gateways (OpenRouter, AiHubMix) come first for fallback matching

**Tool System** (`nanobot/agent/tools/`)
- Registry-based with dynamic tool registration
- Built-in tools: filesystem (read/write/edit/list), shell (exec), web (search/fetch), message, cron, spawn (subagents)
- MCP tools auto-registered from configured servers
- Tools implement: `to_schema()`, `validate_params()`, `execute()`

**Session Management** (`nanobot/session/manager.py`)
- Conversations stored as JSONL files in workspace/sessions/
- Messages are append-only for LLM cache efficiency
- Consolidation writes summaries to MEMORY.md/HISTORY.md but doesn't modify message history
- `get_history()` returns unconsolidated messages aligned to user turns

**Memory System** (`nanobot/agent/memory.py`)
- Two-layer design:
  - `MEMORY.md`: Long-term facts (included in agent context)
  - `HISTORY.md`: Grep-searchable chronological log
- Consolidation via LLM tool call (`save_memory`) with `history_entry` and `memory_update`
- Triggered when session exceeds memory_window (default 100 messages)

**Skills System** (`nanobot/agent/skills.py`)
- Skills are markdown files (`SKILL.md`) that teach the agent capabilities
- Load order: workspace/skills/ (highest priority) → builtin nanobot/skills/
- Built-in skills: github, weather, tmux, cron, memory, summarize, clawhub, skill-creator
- Skills can have requirements checked via frontmatter metadata

**Message Bus** (`nanobot/bus/`)
- Async event-driven architecture
- InboundMessage: from channels to agent
- OutboundMessage: from agent to channels
- Queue-based with asyncio primitives

**Channels** (`nanobot/channels/`)
- Pluggable integrations for chat platforms
- Base class pattern: all channels inherit from BaseChannel
- Each channel implements: `start()`, `stop()`, `send_message()`
- Channel manager handles lifecycle and routing

### Key Patterns

1. **Pydantic Config Schema** (`nanobot/config/schema.py`)
   - All config uses Pydantic BaseModel with camelCase/snake_case dual support
   - Config file: `~/.nanobot/config.json`
   - Nested config structure: providers, channels, agents, tools, cron

2. **Workspace Organization**
   - Default: `~/.nanobot/workspace/`
   - Structure: sessions/, skills/, memory/, HEARTBEAT.md, WORKSPACE.md
   - Templates synced from `nanobot/templates/` on startup

3. **Provider Detection**
   - By API key prefix (e.g., `sk-or-` for OpenRouter)
   - By api_base URL keyword (e.g., "openrouter" in URL)
   - By model name keywords (e.g., "claude" → anthropic, "qwen" → dashscope)

4. **Tool Execution Safety**
   - `restrictToWorkspace` flag sandboxes file operations
   - Tool errors return "Error: ..." string with hint to try different approach
   - Validation happens before execution via `validate_params()`

5. **Context Building** (`nanobot/agent/context.py`)
   - System prompt → Memory (MEMORY.md) → Skills → Session history → Runtime context
   - Supports prompt caching for Anthropic models (cache_control blocks)
   - Dynamic tool definitions injected based on registered tools

## Important Files & Locations

### Configuration
- `pyproject.toml` — Project metadata, dependencies, build config, Ruff/pytest settings
- `nanobot/config/schema.py` — Full config schema with all options
- `~/.nanobot/config.json` — User config file (created by `nanobot onboard`)

### Core Logic
- `nanobot/agent/loop.py` — Main agent processing loop
- `nanobot/agent/context.py` — Context/prompt building
- `nanobot/agent/tools/registry.py` — Tool registry
- `nanobot/providers/registry.py` — Provider registry
- `nanobot/session/manager.py` — Session persistence
- `nanobot/agent/memory.py` — Memory consolidation

### Entry Points
- `nanobot/cli/commands.py` — All CLI commands (typer app)
- `nanobot/__main__.py` — Main entry point

### Channel Implementations
- `nanobot/channels/telegram.py` — Telegram bot
- `nanobot/channels/discord.py` — Discord bot
- `nanobot/channels/whatsapp.py` — WhatsApp (via Node.js bridge)
- `nanobot/channels/matrix.py` — Matrix/Element
- `nanobot/channels/slack.py` — Slack (Socket Mode)
- `nanobot/channels/feishu.py` — Feishu (WebSocket)
- `nanobot/channels/dingtalk.py` — DingTalk (Stream Mode)
- `nanobot/channels/qq.py` — QQ (botpy SDK)
- `nanobot/channels/email.py` — Email (IMAP/SMTP)
- `nanobot/channels/mochat.py` — Mochat/Claw IM

### Testing
- `tests/` — All tests use pytest with pytest-asyncio
- Test naming: `test_*.py` with functions `test_*` or `async def test_*`

## Adding New Features

### Adding a New LLM Provider

1. Add a `ProviderSpec` entry in `nanobot/providers/registry.py`:
```python
ProviderSpec(
    name="myprovider",
    keywords=("myprovider", "mymodel"),
    env_key="MYPROVIDER_API_KEY",
    display_name="My Provider",
    litellm_prefix="myprovider",
    skip_prefixes=("myprovider/",),
)
```

2. Add config field in `nanobot/config/schema.py`:
```python
class ProvidersConfig(BaseModel):
    ...
    myprovider: ProviderConfig = ProviderConfig()
```

That's it! Environment variables, model prefixing, and status display work automatically.

### Adding a New Tool

1. Create tool class in `nanobot/agent/tools/`:
```python
from nanobot.agent.tools.base import Tool

class MyTool(Tool):
    name = "my_tool"
    description = "What this tool does"

    def to_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "param": {"type": "string", "description": "Param desc"}
                    },
                    "required": ["param"]
                }
            }
        }

    async def execute(self, param: str) -> str:
        # Tool implementation
        return f"Result: {param}"
```

2. Register in `AgentLoop.__init__()` (in `loop.py`):
```python
self.tools.register(MyTool())
```

### Adding a New Channel

1. Create channel class in `nanobot/channels/`:
```python
from nanobot.channels.base import BaseChannel

class MyChannel(BaseChannel):
    async def start(self) -> None:
        # Initialize connection, start polling/websocket
        pass

    async def stop(self) -> None:
        # Clean shutdown
        pass

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        # Send message to user
        pass
```

2. Add config in `nanobot/config/schema.py`:
```python
class MyChannelConfig(Base):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)

class ChannelsConfig(Base):
    ...
    mychannel: MyChannelConfig = Field(default_factory=MyChannelConfig)
```

3. Register in `nanobot/channels/manager.py`.

## Testing Guidelines

- All tests use `pytest` with `pytest-asyncio` configured in `pyproject.toml`
- Async tests: `async def test_something()`
- Use fixtures for common setup (tmp_path, monkeypatch, etc.)
- Mock external dependencies (LLM calls, API requests, file I/O where appropriate)
- Test files mirror source structure: `tests/test_<module>.py`

## Common Gotchas

1. **Session History vs Consolidation**: Sessions are append-only. Consolidation writes to MEMORY.md/HISTORY.md but doesn't modify `session.messages` or `get_history()` output. This preserves LLM cache continuity.

2. **Provider Auto-Detection**: Order in PROVIDERS registry matters. Gateways (OpenRouter, AiHubMix) come first so they can handle fallback routing.

3. **Tool Validation**: Always validate params before execution. Return "Error: ..." strings (with hint) rather than raising exceptions to give agent better feedback.

4. **Config Schema Changes**: When adding new config fields, update both the schema class AND provide sensible defaults to avoid breaking existing configs.

5. **Channel Allow Lists**: Empty `allow_from` means "allow all users" in v0.1.4.post3 and earlier. In newer versions (including source builds), empty list means "deny all" — use `["*"]` to allow everyone.

6. **MCP Tool Timeout**: Default is 30s per tool call. Set `toolTimeout` per-server in config for slow operations.

7. **Workspace Restriction**: Set `tools.restrictToWorkspace: true` in production to sandbox file operations to workspace directory.

8. **Provider-Specific Behavior**:
   - Anthropic supports prompt caching via `cache_control` blocks
   - Some models have parameter overrides (e.g., Kimi k2.5 requires `temperature: 1.0`)
   - Gateway providers (OpenRouter, AiHubMix) can route any model

## Security Considerations

- **API Keys**: Stored in `~/.nanobot/config.json` — never commit this file
- **Channel Authentication**: Use `allow_from` lists to restrict access
- **Workspace Sandboxing**: Enable `restrictToWorkspace` to prevent path traversal
- **Email Channel**: Requires explicit `consent_granted: true` flag
- **Shell Tool**: Inherits user permissions — be cautious with `exec_tool.pathAppend`

## Docker Usage

```bash
# Build image
docker build -t nanobot .

# Initialize config (first time)
docker run -v ~/.nanobot:/root/.nanobot --rm nanobot onboard

# Edit config on host
vim ~/.nanobot/config.json

# Run gateway
docker run -v ~/.nanobot:/root/.nanobot -p 18790:18790 nanobot gateway

# Or use docker-compose
docker compose run --rm nanobot-cli onboard
docker compose up -d nanobot-gateway
```

## Line Count Verification

Run `bash core_agent_lines.sh` to verify the ~4,000 line core agent count (a key feature of nanobot's lightweight design).
