# Final Cut Closed-Loop Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Editor CLI into a source-preserving Final Cut Pro 12.3 controller that applies natural-language edits, renders each candidate through Final Cut, watches the result, and leaves the verified timeline ready for the editor's manual export.

**Architecture:** A persisted session controller coordinates narrow adapters for CommandPost, FCPXML MCP, the shared `watch` skill, internet-media acquisition, and verification. Every edit works in a versioned session directory, validates local paths against an allowlist, and advances an idempotent state machine. Claude Code and Codex call four grouped MCP tools backed by the same controller.

**Tech Stack:** Python 3.13, Typer, pytest, MCP Python SDK, `fcp-mcp-server==0.22.1`, CommandPost 2.1 WebSocket control surface, `bradautomates/claude-video==0.2.0`, FFmpeg/ffprobe, yt-dlp, macOS Final Cut Pro 12.3.

---

## File map

Create these focused modules:

- `src/editor_cli/session/models.py`: session states, edit requests, pass records, and results.
- `src/editor_cli/session/paths.py`: session layout and fail-closed path allowlist.
- `src/editor_cli/session/store.py`: atomic snapshots and append-only journal.
- `src/editor_cli/session/controller.py`: closed-loop state machine and resume logic.
- `src/editor_cli/adapters/commandpost.py`: loopback-only CommandPost WebSocket client.
- `src/editor_cli/adapters/fcp_assets.py`: installed Final Cut and Motion asset catalog.
- `src/editor_cli/adapters/fcpxml_mcp.py`: typed client for pinned FCPXML MCP tools.
- `src/editor_cli/adapters/watch.py`: shared video-evidence bundle generation.
- `src/editor_cli/acquire/internet.py`: internet asset acquisition and provenance.
- `src/editor_cli/verification/technical.py`: FFmpeg and timeline integrity checks.
- `src/editor_cli/verification/review.py`: required-edit evidence and pass comparison.
- `src/editor_cli/mcp_server.py`: four grouped agent tools.
- `src/editor_cli/setup.py`: dependency, configuration, and permission doctor.
- `skills/final-cut-editor/SKILL.md`: closed-loop tool sequence shared by Claude Code and Codex.
- `scripts/fcp_live_canary.py`: disposable-library live acceptance test.

Modify these files:

- `pyproject.toml`: pin controller dependencies and expose the MCP entry point.
- `uv.lock`: lock all new dependencies.
- `src/editor_cli/config.py`: add controller paths and tool settings without requiring cloud keys for local commands.
- `src/editor_cli/cli.py`: add `doctor`, `setup`, `edit-active`, and `session` commands.
- `README.md`: document installation, permissions, workflow, and the manual final-export boundary.
- `.gitignore`: exclude local session artifacts and canary output.

Tests mirror the production modules under `tests/session/`, `tests/adapters/`,
`tests/acquire/`, `tests/verification/`, and `tests/integration/`.

## Task 1: Pin controller dependencies and extend configuration

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/editor_cli/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing controller-config tests**

Add tests that load controller configuration without Gemini or ElevenLabs keys
and reject a non-loopback CommandPost URL:

```python
from pathlib import Path

import pytest

from editor_cli.config import ConfigError, load_controller_config


def test_controller_config_needs_no_cloud_keys(tmp_path: Path):
    cfg = load_controller_config(
        env={"EDITOR_CLI_SESSION_ROOT": str(tmp_path / "sessions")}
    )
    assert cfg.session_root == (tmp_path / "sessions").resolve()
    assert cfg.commandpost_url == "ws://127.0.0.1:27480/"
    assert cfg.max_passes == 3


def test_controller_config_rejects_non_loopback_commandpost():
    with pytest.raises(ConfigError, match="loopback"):
        load_controller_config(
            env={"EDITOR_CLI_COMMANDPOST_URL": "ws://192.168.1.50:27480/"}
        )
```

- [ ] **Step 2: Run the tests and confirm the missing API**

Run: `uv run pytest tests/test_config.py -q`  
Expected: collection fails because `load_controller_config` does not exist.

- [ ] **Step 3: Add dependencies and controller configuration**

Add direct dependencies:

```toml
dependencies = [
    "fcp-mcp-server==0.22.1",
    "google-genai>=2.8.0",
    "mcp>=1.3.0,<3",
    "pillow>=10",
    "typer>=0.12",
    "websockets>=16,<17",
    "yt-dlp>=2026.6.9",
]

[project.scripts]
editor-cli = "editor_cli.cli:app"
editor-cli-mcp = "editor_cli.mcp_server:main"
```

Add a separate local-controller config so `doctor` and `session status` do not
require cloud credentials:

```python
@dataclass(frozen=True)
class ControllerConfig:
    session_root: Path
    commandpost_url: str = "ws://127.0.0.1:27480/"
    fcpxml_command: tuple[str, ...] = ("uvx", "fcp-mcp-server==0.22.1")
    max_passes: int = 3


def load_controller_config(env: Optional[dict[str, str]] = None) -> ControllerConfig:
    src = dict(os.environ if env is None else env)
    url = src.get("EDITOR_CLI_COMMANDPOST_URL", "ws://127.0.0.1:27480/")
    parsed = urlsplit(url)
    if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ConfigError("CommandPost URL must use an unencrypted loopback WebSocket")
    root = Path(src.get("EDITOR_CLI_SESSION_ROOT", "~/Movies/Editor CLI Sessions"))
    max_passes = int(src.get("EDITOR_CLI_MAX_PASSES", "3"))
    if max_passes != 3:
        raise ConfigError("EDITOR_CLI_MAX_PASSES must be 3 for the initial release")
    return ControllerConfig(session_root=root.expanduser().resolve(), commandpost_url=url)
```

- [ ] **Step 4: Lock and verify configuration**

Run: `uv lock && uv sync --extra dev && uv run pytest tests/test_config.py -q`  
Expected: all config tests pass and the lock contains `fcp-mcp-server==0.22.1`.

- [ ] **Step 5: Commit the dependency boundary**

```bash
git add pyproject.toml uv.lock src/editor_cli/config.py tests/test_config.py
git commit -m "build: pin Final Cut controller dependencies"
```

## Task 2: Define session contracts and state transitions

**Files:**
- Create: `src/editor_cli/session/__init__.py`
- Create: `src/editor_cli/session/models.py`
- Create: `tests/session/test_models.py`

- [ ] **Step 1: Write failing model tests**

```python
import pytest

from editor_cli.session.models import EditOperation, EditProgram, EditRequest, SessionState


def test_session_state_has_closed_loop_order():
    assert [state.value for state in SessionState] == [
        "idle", "capture", "preserve", "analyze", "apply", "import",
        "preview", "verify", "correct", "ready", "blocked",
    ]


def test_edit_request_normalizes_required_operations():
    request = EditRequest(prompt="  remove gaps  ", required_operations=("gaps",))
    assert request.prompt == "remove gaps"
    assert request.required_operations == ("gaps",)


def test_edit_program_rejects_unwrapped_action():
    with pytest.raises(ValueError, match="Unsupported edit action"):
        EditProgram((EditOperation("edit", "run_shell", {}),))
```

- [ ] **Step 2: Confirm the tests fail**

Run: `uv run pytest tests/session/test_models.py -q`  
Expected: import fails because `editor_cli.session.models` does not exist.

- [ ] **Step 3: Implement immutable session contracts**

```python
class SessionState(str, Enum):
    IDLE = "idle"
    CAPTURE = "capture"
    PRESERVE = "preserve"
    ANALYZE = "analyze"
    APPLY = "apply"
    IMPORT = "import"
    PREVIEW = "preview"
    VERIFY = "verify"
    CORRECT = "correct"
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class EditRequest:
    prompt: str
    required_operations: tuple[str, ...] = ()
    internet_media: bool = True

    def __post_init__(self) -> None:
        clean = self.prompt.strip()
        if not clean:
            raise ValueError("Edit prompt cannot be empty")
        object.__setattr__(self, "prompt", clean)


ALLOWED_EDIT_ACTIONS = frozenset({
    ("edit", "insert_clip"), ("edit", "delete_clips"),
    ("edit", "trim_clip"), ("edit", "split_clip"),
    ("edit", "reorder_clips"), ("edit", "change_speed"),
    ("edit", "add_transition"), ("edit", "add_audio"),
    ("edit", "add_connected_clip"), ("edit", "assign_role"),
    ("edit", "fill_gaps"), ("edit", "fix_flash_frames"),
    ("edit", "remove_silence_candidates"),
    ("mark", "add_marker"), ("mark", "batch_add_markers"),
    ("generate", "apply_template"),
})


@dataclass(frozen=True)
class EditOperation:
    group: str
    action: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class EditProgram:
    operations: tuple[EditOperation, ...]
    changed_ranges: tuple[tuple[float, float], ...] = ()

    def __post_init__(self) -> None:
        if not self.operations:
            raise ValueError("Edit program must contain at least one operation")
        for operation in self.operations:
            if (operation.group, operation.action) not in ALLOWED_EDIT_ACTIONS:
                raise ValueError(f"Unsupported edit action: {operation.group}.{operation.action}")

    def validate_for(self, analysis: dict[str, Any]) -> None:
        duration = float(analysis["duration_seconds"])
        for start, end in self.changed_ranges:
            if start < 0 or end <= start or end > duration:
                raise ValueError(f"Changed range is outside the timeline: {start}-{end}")


@dataclass(frozen=True)
class ProjectIdentity:
    library: str
    event: str
    project: str
    duration_seconds: float


@dataclass(frozen=True)
class PassResult:
    number: int
    fcpxml_path: str
    preview_path: str | None
    required_checks: dict[str, bool]
    score: float

    @property
    def verified(self) -> bool:
        return bool(self.required_checks) and all(self.required_checks.values())
```

- [ ] **Step 4: Verify session contracts**

Run: `uv run pytest tests/session/test_models.py -q`  
Expected: all model tests pass.

- [ ] **Step 5: Commit session contracts**

```bash
git add src/editor_cli/session tests/session/test_models.py
git commit -m "feat: define Final Cut edit session contracts"
```

## Task 3: Build the session layout and filesystem allowlist

**Files:**
- Create: `src/editor_cli/session/paths.py`
- Create: `tests/session/test_paths.py`

- [ ] **Step 1: Write traversal and exact-reference tests**

```python
from pathlib import Path

import pytest

from editor_cli.session.paths import AccessDenied, SessionPaths


def test_allowlist_accepts_session_files_and_exact_media_reference(tmp_path: Path):
    paths = SessionPaths.create(tmp_path / "sessions", "abc123")
    referenced = (tmp_path / "source" / "clip.mov").resolve()
    paths.add_media_references([referenced])
    assert paths.require_read(paths.assets / "meme.mp4").is_relative_to(paths.root)
    assert paths.require_read(referenced) == referenced


def test_allowlist_rejects_reference_sibling(tmp_path: Path):
    paths = SessionPaths.create(tmp_path / "sessions", "abc123")
    referenced = (tmp_path / "source" / "clip.mov").resolve()
    paths.add_media_references([referenced])
    with pytest.raises(AccessDenied):
        paths.require_read(referenced.parent / "private.mov")
```

- [ ] **Step 2: Confirm the allowlist tests fail**

Run: `uv run pytest tests/session/test_paths.py -q`  
Expected: import fails because `SessionPaths` does not exist.

- [ ] **Step 3: Implement session paths and exact-file grants**

```python
@dataclass
class SessionPaths:
    root: Path
    source: Path
    assets: Path
    candidates: Path
    previews: Path
    evidence: Path
    _media_refs: set[Path] = field(default_factory=set)

    @classmethod
    def create(cls, base: Path, session_id: str) -> "SessionPaths":
        if not re.fullmatch(r"[a-zA-Z0-9_-]{6,64}", session_id):
            raise ValueError("Invalid session id")
        root = (base / session_id).resolve()
        parts = [root / name for name in ("source", "assets", "candidates", "previews", "evidence")]
        for path in (root, *parts):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.chmod(0o700)
        return cls(root, *parts)

    def add_media_references(self, paths: Iterable[Path]) -> None:
        self._media_refs.update(path.expanduser().resolve() for path in paths)

    def require_read(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if resolved == self.root or resolved.is_relative_to(self.root) or resolved in self._media_refs:
            return resolved
        raise AccessDenied(f"Path is outside this edit session: {resolved}")
```

- [ ] **Step 4: Verify fail-closed path behavior**

Run: `uv run pytest tests/session/test_paths.py -q`  
Expected: all allowlist tests pass, including symlink-escape cases.

- [ ] **Step 5: Commit the local access boundary**

```bash
git add src/editor_cli/session/paths.py tests/session/test_paths.py
git commit -m "feat: enforce edit-session path allowlist"
```

## Task 4: Add atomic state snapshots and an append-only journal

**Files:**
- Create: `src/editor_cli/session/store.py`
- Create: `tests/session/test_store.py`

- [ ] **Step 1: Write crash-safety and uncertain-write tests**

```python
from editor_cli.session.models import SessionState
from editor_cli.session.store import SessionStore


def test_store_round_trips_state_and_appends_events(tmp_path):
    store = SessionStore(tmp_path)
    store.save_state({"state": SessionState.CAPTURE.value, "pass": 0})
    store.append("capture_started", {"project": "Demo"})
    assert store.load_state()["state"] == "capture"
    assert store.events()[-1]["kind"] == "capture_started"


def test_pending_external_action_survives_restart(tmp_path):
    store = SessionStore(tmp_path)
    token = store.begin_external_action("commandpost.export", {"project": "Demo"})
    reopened = SessionStore(tmp_path)
    assert reopened.pending_actions() == [token]
```

- [ ] **Step 2: Confirm storage tests fail**

Run: `uv run pytest tests/session/test_store.py -q`  
Expected: import fails because `SessionStore` does not exist.

- [ ] **Step 3: Implement atomic replace and JSONL append**

```python
class SessionStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.state_path = self.root / "state.json"
        self.journal_path = self.root / "journal.jsonl"

    def save_state(self, value: dict[str, Any]) -> None:
        fd, raw = tempfile.mkstemp(prefix="state-", suffix=".json", dir=self.root)
        temp = Path(raw)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, sort_keys=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.state_path)
        finally:
            temp.unlink(missing_ok=True)

    def append(self, kind: str, data: dict[str, Any]) -> str:
        event_id = str(uuid.uuid4())
        row = {"id": event_id, "at": datetime.now(timezone.utc).isoformat(), "kind": kind, "data": data}
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event_id
```

- [ ] **Step 4: Verify restart behavior**

Run: `uv run pytest tests/session/test_store.py -q`  
Expected: all store tests pass, including one simulated torn state-file write.

- [ ] **Step 5: Commit persistence**

```bash
git add src/editor_cli/session/store.py tests/session/test_store.py
git commit -m "feat: persist resumable edit sessions"
```

## Task 5: Implement the loopback-only CommandPost adapter

**Files:**
- Create: `src/editor_cli/adapters/__init__.py`
- Create: `src/editor_cli/adapters/commandpost.py`
- Create: `src/editor_cli/adapters/fcp_assets.py`
- Create: `tests/adapters/test_commandpost.py`
- Create: `tests/adapters/test_fcp_assets.py`

- [ ] **Step 1: Write protocol and allowlist tests**

```python
import pytest

from editor_cli.adapters.commandpost import CommandPostClient, CommandPostError


def test_client_rejects_unknown_handler():
    client = CommandPostClient("ws://127.0.0.1:27480/")
    with pytest.raises(CommandPostError, match="allowlist"):
        client.command_message("global_applescript", "runAnything")


def test_menu_command_message_has_request_id():
    client = CommandPostClient("ws://127.0.0.1:27480/")
    message = client.command_message("global_menuactions", "Final Cut Pro/File/Export XML")
    assert message["type"] == "command"
    assert message["payload"]["handler"] == "global_menuactions"
    assert message["id"]


def test_asset_catalog_scans_only_approved_roots(tmp_path):
    approved = tmp_path / "Motion Templates.localized"
    effect = approved / "Effects.localized" / "Comedy" / "Punch In.moef"
    effect.parent.mkdir(parents=True)
    effect.write_text("fixture")
    catalog = InstalledAssetCatalog((approved,)).scan()
    assert [(item.kind, item.name) for item in catalog] == [("effect", "Punch In")]
```

- [ ] **Step 2: Confirm the adapter tests fail**

Run: `uv run pytest tests/adapters/test_commandpost.py -q`  
Expected: import fails because the adapter does not exist.

- [ ] **Step 3: Implement the narrow WebSocket client**

```python
ALLOWED_HANDLERS = frozenset({
    "global_menuactions",
    "global_handler",
    "fcpx_videoEffect",
    "fcpx_audioEffect",
    "fcpx_generator",
    "fcpx_title",
    "fcpx_transition",
})


class CommandPostClient:
    def __init__(self, url: str, timeout_seconds: float = 20.0):
        parsed = urlsplit(url)
        if parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise CommandPostError("CommandPost must use loopback")
        self.url = url
        self.timeout_seconds = timeout_seconds

    def command_message(self, handler: str, action_id: str, **parameters: Any) -> dict[str, Any]:
        if handler not in ALLOWED_HANDLERS:
            raise CommandPostError(f"Handler is outside the allowlist: {handler}")
        return {
            "type": "command",
            "id": str(uuid.uuid4()),
            "payload": {"handler": handler, "actionId": action_id, "parameters": parameters},
        }

    async def request(self, message: dict[str, Any]) -> dict[str, Any]:
        async with connect(self.url, open_timeout=self.timeout_seconds) as socket:
            await socket.send(json.dumps(message))
            raw = await asyncio.wait_for(socket.recv(), self.timeout_seconds)
        response = json.loads(raw)
        if response.get("error"):
            raise CommandPostError(str(response["error"]))
        return response
```

Add a `doctor()` probe that uses `lsof -nP -iTCP:27480 -sTCP:LISTEN` and fails
unless every listener address resolves to `127.0.0.1` or `::1`. Test the parser
with fixtures for loopback, wildcard, and LAN listeners.

Implement `InstalledAssetCatalog` with explicit roots for Final Cut's app
bundle, `/Library/Plug-Ins/FxPlug`, `~/Movies/Motion Templates.localized`, and
Final Cut's installed sound-effects directory. Recognize `.moef`, `.moti`,
`.motr`, and `.motn` bundles. Return normalized category/name identifiers for
the five CommandPost plugin handlers. Do not enumerate a root's parent or
follow symlinks outside the approved roots.

- [ ] **Step 4: Verify protocol, listener, and timeout behavior**

Run: `uv run pytest tests/adapters/test_commandpost.py tests/adapters/test_fcp_assets.py -q`  
Expected: all adapter tests pass against a local fake WebSocket server and a
temporary installed-asset tree.

- [ ] **Step 5: Commit the CommandPost boundary**

```bash
git add src/editor_cli/adapters tests/adapters/test_commandpost.py tests/adapters/test_fcp_assets.py
git commit -m "feat: add restricted CommandPost controller"
```

## Task 6: Wrap FCPXML MCP behind typed operations

**Files:**
- Create: `src/editor_cli/adapters/fcpxml_mcp.py`
- Create: `tests/adapters/test_fcpxml_mcp.py`

- [ ] **Step 1: Write MCP initialization and error tests**

```python
@pytest.mark.anyio
async def test_fcpxml_client_initializes_and_calls_grouped_tool(fake_stdio):
    client = FCPXMLMCPClient(("uvx", "fcp-mcp-server==0.22.1"), transport=fake_stdio)
    result = await client.call("inspect", {"action": "analyze", "path": "/tmp/a.fcpxml"})
    assert result["timeline"]["project"] == "Demo"
    assert fake_stdio.initialized is True


@pytest.mark.anyio
async def test_fcpxml_client_rejects_unwrapped_tool():
    client = FCPXMLMCPClient(("uvx", "fcp-mcp-server==0.22.1"))
    with pytest.raises(FCPXMLMCPError, match="unsupported"):
        await client.call("raw_shell", {})
```

- [ ] **Step 2: Confirm the typed client tests fail**

Run: `uv run pytest tests/adapters/test_fcpxml_mcp.py -q`  
Expected: import fails because `FCPXMLMCPClient` does not exist.

- [ ] **Step 3: Implement the official MCP stdio client**

```python
ALLOWED_TOOLS = frozenset({
    "inspect", "diagnose", "edit", "mark", "generate", "transcript",
    "deliver", "preview", "watch", "index", "scenes", "organize", "find",
})


class FCPXMLMCPClient:
    def __init__(self, command: tuple[str, ...]):
        self.command = command

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool not in ALLOWED_TOOLS:
            raise FCPXMLMCPError(f"unsupported FCPXML tool: {tool}")
        params = StdioServerParameters(command=self.command[0], args=list(self.command[1:]))
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments=arguments)
        if result.isError:
            raise FCPXMLMCPError(_text_content(result.content))
        text = _text_content(result.content)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
```

Use a project-owned `FCP_MCP_JOURNAL` directory under the session root for each
subprocess. Pass no home-directory default to the upstream server.

- [ ] **Step 4: Run contract tests against the pinned package**

Run: `uv run pytest tests/adapters/test_fcpxml_mcp.py -q`  
Expected: fake transport tests pass and a real `initialize` plus `tools/list`
smoke test finds the 13 wrapped grouped tools.

- [ ] **Step 5: Commit the FCPXML adapter**

```bash
git add src/editor_cli/adapters/fcpxml_mcp.py tests/adapters/test_fcpxml_mcp.py
git commit -m "feat: wrap the FCPXML MCP edit engine"
```

## Task 7: Capture and preserve the active Final Cut project

**Files:**
- Create: `src/editor_cli/session/capture.py`
- Create: `tests/session/test_capture.py`

- [ ] **Step 1: Write identity and preservation tests**

```python
@pytest.mark.anyio
async def test_capture_rejects_ambiguous_active_project(tmp_path, fake_fcp):
    fake_fcp.projects = [project("A"), project("B")]
    with pytest.raises(CaptureError, match="select one project"):
        await capture_active_project(fake_fcp, SessionPaths.create(tmp_path, "session1"))


@pytest.mark.anyio
async def test_capture_preserves_source_before_candidate(tmp_path, fake_fcp):
    fake_fcp.projects = [project("Demo", active=True, duration=12.0)]
    result = await capture_active_project(fake_fcp, SessionPaths.create(tmp_path, "session1"))
    assert result.source_xml.name == "active-source.fcpxml"
    assert result.preserved_name.startswith("Demo - Before AI - ")
    assert sha256(result.source_xml) == result.source_sha256
```

- [ ] **Step 2: Confirm capture tests fail**

Run: `uv run pytest tests/session/test_capture.py -q`  
Expected: import fails because the capture module does not exist.

- [ ] **Step 3: Implement identity-checked capture**

Implement this sequence:

```python
async def capture_active_project(control: FinalCutControl, paths: SessionPaths) -> CaptureResult:
    identity = await control.active_project()
    export_path = paths.source / "active-source.fcpxml"
    await control.export_xml(identity, export_path)
    parsed = await control.inspect_xml(export_path)
    if parsed.project != identity.project:
        raise CaptureError("Exported project identity does not match the active project")
    if abs(parsed.duration_seconds - identity.duration_seconds) > parsed.frame_seconds:
        raise CaptureError("Exported timeline duration does not match the active project")
    preserved = f"{identity.project} - Before AI - {datetime.now():%Y-%m-%d %H-%M}"
    await control.duplicate_project(identity, preserved)
    return CaptureResult(identity, export_path, file_sha256(export_path), preserved)
```

Mark the export action pending in `SessionStore` before sending it to
CommandPost. On resume, require a fresh identity check rather than replaying an
uncertain export or duplicate command.

- [ ] **Step 4: Verify ordering and resume safety**

Run: `uv run pytest tests/session/test_capture.py -q`  
Expected: all capture tests pass and the fake records export validation before
duplication.

- [ ] **Step 5: Commit active-project capture**

```bash
git add src/editor_cli/session/capture.py tests/session/test_capture.py
git commit -m "feat: preserve active Final Cut projects"
```

## Task 8: Install and integrate shared video perception

**Files:**
- Create: `src/editor_cli/adapters/watch.py`
- Create: `tests/adapters/test_watch.py`
- Modify: `src/editor_cli/setup.py`

- [ ] **Step 1: Write evidence-bundle tests**

```python
def test_watch_builds_reusable_evidence_bundle(tmp_path, fake_runner):
    preview = tmp_path / "preview.mp4"
    preview.write_bytes(b"video")
    adapter = WatchAdapter(script=Path("/skills/watch/scripts/watch.py"), runner=fake_runner)
    bundle = adapter.analyze(preview, tmp_path / "evidence", changed_ranges=[(4.0, 7.5)])
    assert bundle.manifest.exists()
    assert bundle.frames
    assert bundle.changed_ranges == ((4.0, 7.5),)
    assert fake_runner.argv.count("--detail") == 1
```

- [ ] **Step 2: Confirm watch-adapter tests fail**

Run: `uv run pytest tests/adapters/test_watch.py -q`  
Expected: import fails because `WatchAdapter` does not exist.

- [ ] **Step 3: Implement deterministic watch invocation**

```python
class WatchAdapter:
    def __init__(self, script: Path, runner: CommandRunner = run_command):
        self.script = script.resolve()
        self.runner = runner

    def analyze(self, preview: Path, out: Path, changed_ranges: list[tuple[float, float]]) -> EvidenceBundle:
        out.mkdir(parents=True, exist_ok=True)
        args = [sys.executable, str(self.script), str(preview), "--detail", "balanced",
                "--max-frames", "100", "--out-dir", str(out)]
        self.runner(args, timeout=900)
        manifest = build_evidence_manifest(preview, out, changed_ranges)
        atomic_json_write(out / "manifest.json", manifest)
        return EvidenceBundle.from_manifest(out / "manifest.json")
```

Add targeted follow-up extraction for each changed range with two seconds of
context on both sides and a two-frames-per-second cap.

- [ ] **Step 4: Verify the shared installation path**

Add a setup dry-run test that produces this exact command without executing it:

```text
npx skills add bradautomates/claude-video -g -y
```

Run: `uv run pytest tests/adapters/test_watch.py tests/test_setup.py -q`  
Expected: all perception and setup tests pass.

- [ ] **Step 5: Commit shared video perception**

```bash
git add src/editor_cli/adapters/watch.py src/editor_cli/setup.py tests/adapters/test_watch.py tests/test_setup.py
git commit -m "feat: share video evidence across agents"
```

## Task 9: Add technical and creative verification

**Files:**
- Create: `src/editor_cli/verification/__init__.py`
- Create: `src/editor_cli/verification/technical.py`
- Create: `src/editor_cli/verification/review.py`
- Create: `tests/verification/test_technical.py`
- Create: `tests/verification/test_review.py`

- [ ] **Step 1: Write required-check tests**

```python
def test_required_check_failure_blocks_pass():
    report = ReviewReport(
        required={"remove_gaps": True, "meme_insert": False},
        observations=("Meme insert is missing at 00:12",),
    )
    assert report.verified is False


def test_technical_probe_rejects_black_or_missing_preview(fake_ffprobe):
    fake_ffprobe.result = {"streams": [], "format": {"duration": "0"}}
    report = inspect_preview(Path("preview.mp4"), runner=fake_ffprobe)
    assert report.required["readable_video"] is False
```

- [ ] **Step 2: Confirm verifier tests fail**

Run: `uv run pytest tests/verification -q`  
Expected: imports fail because verification modules do not exist.

- [ ] **Step 3: Implement evidence-based pass reports**

```python
@dataclass(frozen=True)
class ReviewReport:
    required: dict[str, bool]
    observations: tuple[str, ...]
    changed_ranges: tuple[tuple[float, float], ...] = ()

    @property
    def verified(self) -> bool:
        return bool(self.required) and all(self.required.values())


def combine_reports(technical: ReviewReport, creative: ReviewReport) -> ReviewReport:
    overlap = technical.required.keys() & creative.required.keys()
    if overlap:
        raise ValueError(f"Duplicate verification keys: {sorted(overlap)}")
    return ReviewReport(
        required={**technical.required, **creative.required},
        observations=technical.observations + creative.observations,
        changed_ranges=creative.changed_ranges,
    )
```

Technical checks call `ffprobe`, black-frame detection, silence detection, and
FCPXML QC. Creative checks consume a strict JSON review produced from the
watch evidence bundle and request. Reject malformed or missing required keys.

- [ ] **Step 4: Verify all failure modes**

Run: `uv run pytest tests/verification -q`  
Expected: all verifier tests pass, including malformed JSON, black preview,
unexpected silence, missing media, and a failed required edit.

- [ ] **Step 5: Commit verification**

```bash
git add src/editor_cli/verification tests/verification
git commit -m "feat: verify rendered Final Cut candidates"
```

## Task 10: Add internet media acquisition with provenance

**Files:**
- Create: `src/editor_cli/acquire/internet.py`
- Create: `tests/acquire/test_internet.py`

- [ ] **Step 1: Write safe-download tests**

```python
def test_acquire_records_url_hash_and_timeline_use(tmp_path, fake_downloader):
    acquirer = InternetAcquirer(tmp_path / "assets", downloader=fake_downloader)
    asset = acquirer.acquire("https://example.com/reaction.mp4", purpose="reaction at 00:12")
    assert asset.source_url == "https://example.com/reaction.mp4"
    assert asset.sha256 == file_sha256(asset.path)
    assert asset.purpose == "reaction at 00:12"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://host/a"])
def test_acquire_rejects_non_http_sources(tmp_path, url):
    with pytest.raises(AcquisitionError):
        InternetAcquirer(tmp_path).acquire(url, purpose="test")
```

- [ ] **Step 2: Confirm acquisition tests fail**

Run: `uv run pytest tests/acquire/test_internet.py -q`  
Expected: import fails because `InternetAcquirer` does not exist.

- [ ] **Step 3: Implement bounded downloads and provenance**

```python
class InternetAcquirer:
    def __init__(self, assets_dir: Path, downloader: Downloader = YtDlpDownloader(), max_bytes: int = 500_000_000):
        self.assets_dir = assets_dir.resolve()
        self.downloader = downloader
        self.max_bytes = max_bytes

    def acquire(self, url: str, purpose: str) -> AcquiredAsset:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise AcquisitionError("Internet assets require an HTTPS URL")
        metadata = self.downloader.inspect(url)
        if metadata.filesize and metadata.filesize > self.max_bytes:
            raise AcquisitionError("Internet asset exceeds the 500 MB limit")
        path = self.downloader.download(url, self.assets_dir)
        reject_executable(path)
        return AcquiredAsset(
            path=path.resolve(), source_url=url, retrieved_at=utc_now(),
            sha256=file_sha256(path), purpose=purpose,
            author=metadata.author, license_note=metadata.license_note,
        )
```

Write provenance to `assets/provenance.jsonl` before timeline import. Never
pass browser cookies or credential files to yt-dlp in this release.

- [ ] **Step 4: Verify content limits and provenance**

Run: `uv run pytest tests/acquire/test_internet.py -q`  
Expected: all acquisition tests pass, including oversized content, redirects
to non-HTTPS, executable signatures, and duplicate content hashes.

- [ ] **Step 5: Commit internet acquisition**

```bash
git add src/editor_cli/acquire/internet.py tests/acquire/test_internet.py
git commit -m "feat: acquire edit assets with provenance"
```

## Task 11: Implement the closed-loop session controller

**Files:**
- Create: `src/editor_cli/session/controller.py`
- Create: `tests/session/test_controller.py`

- [ ] **Step 1: Write one-pass, correction, and blocked tests**

```python
@pytest.mark.anyio
async def test_controller_finishes_after_verified_first_pass(deps, request):
    controller = EditSessionController(deps)
    session = await controller.start(request)
    candidate = await controller.apply(session.id, valid_edit_program())
    result = await controller.record_review(session.id, candidate.number, verified_report())
    assert result.state is SessionState.READY
    assert result.passes == 1
    assert deps.fcp.original_unchanged is True


@pytest.mark.anyio
async def test_controller_caps_at_three_and_leaves_best_candidate(deps, request):
    controller = EditSessionController(deps)
    session = await controller.start(request)
    for score in (.4, .7, .6):
        candidate = await controller.apply(session.id, valid_edit_program())
        result = await controller.record_review(
            session.id, candidate.number, failed_report(score)
        )
    assert result.state is SessionState.BLOCKED
    assert result.passes == 3
    assert result.best_pass.number == 2
    assert deps.fcp.opened_project == result.best_pass.project_name
```

- [ ] **Step 2: Confirm controller tests fail**

Run: `uv run pytest tests/session/test_controller.py -q`  
Expected: import fails because the controller does not exist.

- [ ] **Step 3: Implement the host-driven three-pass state machine**

```python
class EditSessionController:
    def __init__(self, deps: ControllerDeps):
        self.deps = deps

    async def start(self, request: EditRequest) -> SessionHandle:
        session = await self.deps.sessions.create(request)
        capture = await self._capture_and_preserve(session)
        analysis = await self._analyze_source(session, capture, request)
        return session.awaiting_edit_program(capture, analysis)

    async def apply(self, session_id: str, program: EditProgram) -> Candidate:
        session = self.deps.sessions.load(session_id)
        if session.pass_count >= 3:
            raise SessionError("This edit session already used all three passes")
        program.validate_for(session.analysis)
        candidate = await self._apply_program(session, program, session.pass_count + 1)
        await self._import_candidate(session, candidate)
        preview = await self._render_preview(session, candidate)
        evidence = await self._extract_evidence(session, candidate, preview)
        return session.awaiting_review(candidate, preview, evidence)

    async def record_review(self, session_id: str, pass_number: int,
                            report: ReviewReport) -> SessionResult:
        session = self.deps.sessions.load(session_id)
        result = session.record_review(pass_number, report)
        if result.verified:
            await self.deps.fcp.open_project(result.project_name)
            return session.ready()
        if session.pass_count < 3:
            return session.needs_correction(report.observations)
        best = session.best_pass()
        await self.deps.fcp.open_project(best.project_name)
        return session.blocked(best)
```

Each private transition writes `state.json`, begins external actions in the
journal before execution, and records completion afterward. Claude Code or
Codex supplies each typed edit program and review report through the MCP tools.
A resumed session continues only from completed transitions and asks adapters
to reconcile any pending external action.

- [ ] **Step 4: Verify ordering, cap, and recovery**

Run: `uv run pytest tests/session/test_controller.py -q`  
Expected: all state-machine tests pass, including process restart after each
transition, strict three-pass enforcement, and selection of the strongest
failed pass.

- [ ] **Step 5: Commit closed-loop orchestration**

```bash
git add src/editor_cli/session/controller.py tests/session/test_controller.py
git commit -m "feat: run closed-loop Final Cut edit sessions"
```

## Task 12: Teach Claude Code and Codex the closed-loop workflow

**Files:**
- Create: `skills/final-cut-editor/SKILL.md`
- Create: `tests/test_final_cut_editor_skill.py`

- [ ] **Step 1: Write the skill-contract test**

```python
from pathlib import Path


def test_final_cut_skill_requires_rendered_visual_verification():
    text = Path("skills/final-cut-editor/SKILL.md").read_text()
    required = (
        "editor_session", "editor_timeline", "editor_media", "editor_verify",
        "watch", "three passes", "original project", "final export",
    )
    assert all(term in text for term in required)
    assert "XML change is not proof" in text
```

- [ ] **Step 2: Confirm the skill is missing**

Run: `uv run pytest tests/test_final_cut_editor_skill.py -q`  
Expected: failure because `skills/final-cut-editor/SKILL.md` does not exist.

- [ ] **Step 3: Write the shared editing skill**

The skill must give both hosts this exact control loop:

```markdown
---
name: final-cut-editor
description: Edit the active Final Cut Pro project through Editor CLI, then watch and verify every rendered candidate.
---

# Final Cut Editor

Use this skill when the user asks Claude Code or Codex to change an open Final
Cut Pro timeline.

1. Call `editor_session` with `action: doctor`, then `action: start` and the
   user's complete prompt.
2. Confirm the returned library, event, and project identity. Stop on ambiguity.
3. Use `editor_timeline` with `action: inspect` to read clips, gaps, roles,
   markers, effects, and pacing.
4. Acquire requested internet media through `editor_media`; never search local
   folders for replacement media.
5. Submit one typed edit program through `editor_timeline` with `action: apply`.
6. Call `editor_verify` with `action: preview`, then `action: watch`.
7. Read the returned watch frames and transcript. XML change is not proof.
8. Record every required check through `editor_verify` with `action: record`.
9. If a required check fails, submit a correction and repeat. Stop after three passes.
10. Leave the best project open. State that the original project is untouched
    and the user performs the final export in Final Cut Pro.
```

- [ ] **Step 4: Verify the skill contract**

Run: `uv run pytest tests/test_final_cut_editor_skill.py -q`  
Expected: all required control-loop and safety phrases are present.

- [ ] **Step 5: Commit the shared host workflow**

```bash
git add skills/final-cut-editor/SKILL.md tests/test_final_cut_editor_skill.py
git commit -m "feat: teach agents the Final Cut edit loop"
```

## Task 13: Expose four grouped MCP tools

**Files:**
- Create: `src/editor_cli/mcp_server.py`
- Create: `tests/test_mcp_server.py`

- [ ] **Step 1: Write tool-list and harmless-read tests**

```python
@pytest.mark.anyio
async def test_mcp_exposes_only_grouped_tools(mcp_client):
    names = {tool.name for tool in (await mcp_client.list_tools()).tools}
    assert names == {"editor_session", "editor_timeline", "editor_media", "editor_verify"}


@pytest.mark.anyio
async def test_editor_session_doctor_is_read_only(mcp_client, fake_controller):
    result = await mcp_client.call_tool("editor_session", {"action": "doctor"})
    assert result.structuredContent["final_cut"]["version"] == "12.3"
    assert fake_controller.mutations == []
```

- [ ] **Step 2: Confirm server tests fail**

Run: `uv run pytest tests/test_mcp_server.py -q`  
Expected: import fails because `mcp_server` does not exist.

- [ ] **Step 3: Implement the grouped FastMCP surface**

```python
mcp = FastMCP("editor-cli")


@mcp.tool()
async def editor_session(action: Literal["doctor", "start", "status", "resume", "finish"],
                         prompt: str | None = None, session_id: str | None = None) -> dict:
    return await services.session.dispatch(action, prompt=prompt, session_id=session_id)


@mcp.tool()
async def editor_timeline(action: Literal["inspect", "apply", "diff", "undo"],
                          session_id: str, edit_program: dict | None = None) -> dict:
    return await services.timeline.dispatch(action, session_id, edit_program=edit_program)


@mcp.tool()
async def editor_media(action: Literal["acquire", "list"], session_id: str,
                       url: str | None = None, purpose: str | None = None) -> dict:
    return await services.media.dispatch(action, session_id, url=url, purpose=purpose)


@mcp.tool()
async def editor_verify(action: Literal["preview", "watch", "record", "compare"],
                        session_id: str, pass_number: int | None = None,
                        report: dict | None = None) -> dict:
    return await services.verify.dispatch(
        action, session_id, pass_number=pass_number, report=report
    )


def main() -> None:
    mcp.run(transport="stdio")
```

Reject unknown keys with strict Pydantic request models. Keep CommandPost and
raw FCPXML tools private to the process.

- [ ] **Step 4: Verify initialize, tools/list, and real read**

Run: `uv run pytest tests/test_mcp_server.py -q`  
Expected: MCP initialize succeeds, exactly four tools appear, and `doctor`
returns a harmless device read without opening Final Cut.

- [ ] **Step 5: Commit the agent interface**

```bash
git add src/editor_cli/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: expose grouped video editor MCP tools"
```

## Task 14: Add idempotent setup and host configuration

**Files:**
- Create: `src/editor_cli/setup.py`
- Create: `tests/test_setup.py`
- Modify: `src/editor_cli/cli.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write backup and repeat-run tests**

```python
def test_setup_backs_up_changed_agent_config(tmp_path, fake_platform):
    config = tmp_path / "config.toml"
    config.write_text("[existing]\nvalue = 1\n")
    result = run_setup(SetupPaths(codex_config=config), platform=fake_platform)
    assert result.backups == [config.with_suffix(".toml.editor-cli.bak")]


def test_setup_second_run_has_no_changes(tmp_path, fake_platform):
    paths = setup_paths(tmp_path)
    first = run_setup(paths, platform=fake_platform)
    second = run_setup(paths, platform=fake_platform)
    assert first.changed
    assert second.changed == []
```

- [ ] **Step 2: Confirm setup tests fail**

Run: `uv run pytest tests/test_setup.py -q`  
Expected: import fails because setup functions do not exist.

- [ ] **Step 3: Implement staged setup with backups**

The setup command performs these typed steps and records each result:

```python
SETUP_STEPS = (
    "verify_device",
    "install_commandpost",
    "install_watch_skill",
    "install_editor_skill",
    "configure_commandpost",
    "configure_claude_code",
    "configure_codex",
    "verify_mcp",
)


def backup_before_write(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_suffix(path.suffix + ".editor-cli.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    return backup
```

Verify the CommandPost DMG checksum from the GitHub release metadata, mount it,
copy the signed application to `/Applications`, unmount it, and verify its code
signature. Never replace a different installed version without an explicit
`--upgrade-commandpost` flag.

Install `watch` with Agent Skills CLI and confirm both
`~/.claude/skills/watch/SKILL.md` and `~/.codex/skills/watch/SKILL.md` resolve to
the pinned package. Symlink `skills/final-cut-editor` into both hosts' user
skill directories and verify the resolved files remain inside this repository.
Merge MCP entries into Claude Code and Codex configs while preserving every
unrelated key.

- [ ] **Step 4: Run setup tests and a dry run**

Run: `uv run pytest tests/test_setup.py tests/test_cli_smoke.py -q`  
Expected: all tests pass.

Run: `uv run editor-cli setup --dry-run`  
Expected: reports planned CommandPost, watch-skill, and MCP changes without
writing outside the repository.

- [ ] **Step 5: Commit setup and CLI wiring**

```bash
git add src/editor_cli/setup.py src/editor_cli/cli.py tests/test_setup.py tests/test_cli_smoke.py .gitignore
git commit -m "feat: install the local Final Cut controller"
```

## Task 15: Add `edit-active`, status, resume, and result reporting

**Files:**
- Modify: `src/editor_cli/cli.py`
- Create: `tests/integration/test_edit_active.py`

- [ ] **Step 1: Write CLI workflow tests**

```python
def test_edit_active_captures_request_for_agent_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITOR_CLI_SESSION_ROOT", str(tmp_path))
    result = CliRunner().invoke(app, ["edit-active", "remove gaps and add a reaction at 00:12"])
    assert result.exit_code == 0
    assert "session" in result.output.lower()
    assert "continue in claude code or codex" in result.output.lower()


def test_session_status_reports_blocked_checks(monkeypatch, blocked_session):
    result = CliRunner().invoke(app, ["session", "status", blocked_session.id])
    assert result.exit_code == 2
    assert "meme_insert" in result.output
```

- [ ] **Step 2: Confirm CLI workflow tests fail**

Run: `uv run pytest tests/integration/test_edit_active.py -q`  
Expected: the CLI reports that `edit-active` and `session` do not exist.

- [ ] **Step 3: Add thin Typer controllers**

```python
@app.command("edit-active")
def edit_active(prompt: str = typer.Argument(..., help="Complete edit request.")) -> None:
    session = asyncio.run(build_controller().start(EditRequest(prompt=prompt)))
    typer.secho(f"Session ready: {session.id}", fg=typer.colors.GREEN)
    typer.echo("Continue in Claude Code or Codex with the final-cut-editor skill.")
```

Implement `session status` and `session resume` as read/resume wrappers over the
same stored controller used by MCP. The host skill supplies edit programs and
visual review reports; the CLI never claims that capture alone completed an
edit.

- [ ] **Step 4: Verify CLI and MCP parity**

Run: `uv run pytest tests/integration/test_edit_active.py tests/test_mcp_server.py -q`  
Expected: both surfaces return the same session ID, pass count, best candidate,
and required-check results.

- [ ] **Step 5: Commit operator workflow**

```bash
git add src/editor_cli/cli.py tests/integration/test_edit_active.py
git commit -m "feat: add active Final Cut editing commands"
```

## Task 16: Build the disposable Final Cut 12.3 live canary

**Files:**
- Create: `scripts/fcp_live_canary.py`
- Create: `tests/integration/test_live_canary_contract.py`
- Create: `tests/fixtures/canary/expected.json`

- [ ] **Step 1: Write the canary contract test**

```python
def test_canary_definition_covers_visible_and_structural_edits():
    expected = json.loads(Path("tests/fixtures/canary/expected.json").read_text())
    assert set(expected["required_checks"]) == {
        "source_unchanged", "gap_removed", "title_visible", "transition_visible",
        "reaction_insert_visible", "preview_rendered", "preview_watched",
    }
    assert expected["duration_seconds"] == 8.0
```

- [ ] **Step 2: Confirm the fixture and script are missing**

Run: `uv run pytest tests/integration/test_live_canary_contract.py -q`  
Expected: failure because the canary fixture does not exist.

- [ ] **Step 3: Implement deterministic canary media and checks**

Generate an eight-second 1080p timeline from FFmpeg color cards and tones. The
source contains a one-second gap. The edit must remove the gap, add a visible
title from seconds 1 through 3, add a transition, and insert a generated
reaction card at second 5.

```python
EXPECTED_CHECKS = (
    "source_unchanged", "gap_removed", "title_visible", "transition_visible",
    "reaction_insert_visible", "preview_rendered", "preview_watched",
)


def main() -> int:
    require_final_cut_version("12.3")
    workspace = create_canary_workspace()
    source_hashes = hash_tree(workspace.source)
    result = asyncio.run(run_canary(workspace))
    result.required_checks["source_unchanged"] = source_hashes == hash_tree(workspace.source)
    write_result(workspace.result_path, result)
    return 0 if all(result.required_checks.values()) else 1
```

The script creates a new disposable `.fcpbundle` under the canary workspace.
It refuses a path that already exists and never opens a real user library.

- [ ] **Step 4: Run the contract test, then the live canary**

Run: `uv run pytest tests/integration/test_live_canary_contract.py -q`  
Expected: contract test passes.

Run with Final Cut and CommandPost open:

```bash
uv run python scripts/fcp_live_canary.py
```

Expected: exit 0, seven required checks pass, the source hash stays unchanged,
and the generated verified project remains open in the disposable library.

- [ ] **Step 5: Commit the live acceptance test**

```bash
git add scripts/fcp_live_canary.py tests/integration/test_live_canary_contract.py tests/fixtures/canary/expected.json
git commit -m "test: add Final Cut 12.3 controller canary"
```

## Task 17: Run full verification and document the working device

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-09-05-final-cut-closed-loop-controller-design.md`

- [ ] **Step 1: Run the complete automated suite**

Run: `uv run pytest -q`  
Expected: all tests pass with only tests marked as optional live-network checks
skipped.

- [ ] **Step 2: Run static and packaging checks**

Run: `uv build && uv run python -m editor_cli.mcp_server --help`  
Expected: wheel and source distribution build; MCP entry point imports without
warnings.

Run: `git diff --check`  
Expected: no whitespace errors.

- [ ] **Step 3: Verify both agent hosts**

Start fresh Claude Code and Codex sessions in the repository. In each host:

1. initialize the `editor-cli` MCP server;
2. list tools and confirm the four grouped tools;
3. call `editor_session` with `action=doctor`;
4. use the installed `watch` skill on the canary preview; and
5. confirm the tool reports Final Cut 12.3 and the verified canary result.

Expected: both hosts consume the same evidence manifest and report the same
preview hash.

- [ ] **Step 4: Update operator documentation with measured results**

Replace the README's planned-controller language with installed commands,
permission steps, session layout, recovery commands, and the manual final
export boundary. Record the exact canary date, Final Cut build, CommandPost
version, watch version, test count, and evidence path. Update `CLAUDE.md` so
video-editing requests invoke the controller and shared watch skill.

- [ ] **Step 5: Commit documentation and push the branch**

```bash
git add README.md CLAUDE.md docs/superpowers/specs/2026-09-05-final-cut-closed-loop-controller-design.md
git commit -m "docs: document verified Final Cut controller"
git push
```

- [ ] **Step 6: Review the PR without merging**

Confirm PR #18 contains every implementation commit, a meaningful README
update, green checks, the live-canary evidence, and no secrets or session media.
Leave the PR open for user review. Merging requires separate authorization.
