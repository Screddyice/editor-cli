# Native Final Cut Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the paid CommandPost and LateNite controller path with a free, project-owned Swift Accessibility helper that edits Final Cut Pro 12.3, renders previews through Final Cut, watches them, and leaves final export to the user.

**Architecture:** A signed Swift executable accepts a strict one-request JSON protocol over stdin/stdout and performs seven allowlisted Final Cut actions through Apple Accessibility and read-only Apple Events. Python owns sessions, path confinement, review contracts, reconciliation, and evidence; the helper proves each Final Cut postcondition and opens no network listener.

**Tech Stack:** Python 3.13, Swift 6.3, Swift Package Manager, AppKit, ApplicationServices, Foundation, MCP Python SDK, `fcp-mcp-server==0.22.1`, `bradautomates/claude-video==0.2.0`, FFmpeg, pytest, XCTest, macOS 26, Final Cut Pro Creator Studio 12.3.

---

## File map

Create:

- `native/final-cut-bridge/Package.swift`: standalone Swift package.
- `native/final-cut-bridge/Sources/FinalCutBridge/Protocol.swift`: strict request and response protocol.
- `native/final-cut-bridge/Sources/FinalCutBridge/Accessibility.swift`: bounded AX tree access.
- `native/final-cut-bridge/Sources/FinalCutBridge/FinalCut.swift`: app identity, Apple Event reads, and postcondition polling.
- `native/final-cut-bridge/Sources/FinalCutBridge/Actions.swift`: seven allowlisted actions.
- `native/final-cut-bridge/Sources/FinalCutBridge/main.swift`: one-shot JSON entry point.
- `native/final-cut-bridge/Tests/FinalCutBridgeTests/`: protocol, accessibility, and action tests.
- `src/editor_cli/adapters/native_final_cut.py`: Python subprocess adapter and typed responses.
- `src/editor_cli/resources/__init__.py`: package-resource access.
- `src/editor_cli/resources/`: packaged Swift source, editing skill, protocol schema, and canary.
- `src/editor_cli/session/locking.py`: per-session interprocess lock.
- `src/editor_cli/session/reconcile.py`: external-action reconciliation.
- `tests/adapters/test_native_final_cut.py`: subprocess and response tests.
- `tests/session/test_locking.py`: locking and compare-and-swap tests.
- `tests/session/test_reconcile.py`: recovery tests.
- `tests/test_native_setup.py`: build, install, doctor, and config tests.
- `tests/test_packaged_resources.py`: wheel and source-distribution resource tests.

Modify:

- `src/editor_cli/setup.py`: compile/install the native helper and configure hosts atomically.
- `src/editor_cli/mcp_server.py`: native doctor and lazy service construction.
- `src/editor_cli/services.py`: strict actions, structured inspection, assets, and bound reviews.
- `src/editor_cli/session/models.py`: controller-owned checks and evidence bindings.
- `src/editor_cli/session/store.py`: versioned compare-and-swap state and actionable journal records.
- `src/editor_cli/session/controller.py`: locks, reconciliation, journaled undo, and unique project names.
- `src/editor_cli/session/capture.py`: exact media-reference persistence and source-copy verification.
- `src/editor_cli/adapters/final_cut_control.py`: replace CommandPost with native helper actions.
- `src/editor_cli/adapters/timeline_engine.py`: structured planning data and candidate diagnostics.
- `src/editor_cli/acquire/internet.py`: redirect validation, timeouts, temp files, and recovery receipts.
- `src/editor_cli/verification/technical.py`: candidate-derived duration and FCPXML validation.
- `scripts/fcp_live_canary.py`: native two-run canary with Final Cut source recapture.
- `pyproject.toml`, `uv.lock`: package resources and remove CommandPost-only dependencies.
- `README.md`, `CLAUDE.md`, `skills/final-cut-editor/SKILL.md`: native setup and workflow.
- Existing tests that mention CommandPost or LateNite.

Delete after native parity tests pass:

- `src/editor_cli/adapters/commandpost.py`
- `commandpost/editor-cli-bridge/init.lua`
- CommandPost-only tests and setup fixtures.

## Global constraints

- No paid controller application, LateNite license, CommandPost process, or WebSocket listener.
- No raw AppleScript, shell, menu, keystroke, or AX execution exposed to agents.
- Final Cut 12.3 must create the watched preview through its Share command.
- Final delivery export remains a user action.
- Local media access stays inside the session plus exact paths captured from the active FCPXML.
- Internet media uses public HTTPS, redirect and peer validation, provenance, a 500 MB cap, and no browser credentials.
- Every external action records intent before execution and reconciles a postcondition before replay.
- The branch and PR stay draft until the real native live canary and dual-host preview hash comparison pass.

## Task 1: Reconcile the interrupted safety work and lock the domain contracts

**Files:**
- Modify: `src/editor_cli/session/models.py`
- Modify: `src/editor_cli/session/paths.py`
- Modify: `src/editor_cli/session/store.py`
- Modify: `tests/session/test_models.py`
- Modify: `tests/session/test_paths.py`
- Modify: `tests/session/test_store.py`

- [ ] **Step 1: Preserve and inspect the interrupted diff**

Run:

```bash
git diff -- src/editor_cli/session/models.py src/editor_cli/session/paths.py src/editor_cli/session/store.py tests/session/test_models.py tests/session/test_paths.py tests/session/test_store.py
```

Expected: the existing uncommitted work contains controller-owned review bindings, media-reference paths, and actionable journal records. Keep correct pieces and replace CommandPost-specific names with transport-neutral names.

- [ ] **Step 2: Add failing contract tests**

Add these assertions:

```python
def test_review_binding_requires_exact_artifact_identity():
    binding = EvidenceBinding(
        session_id="abc123", pass_number=1, state_version=4,
        project_name="Demo - abc123 - AI Pass 1",
        candidate_sha256="a" * 64, preview_sha256="b" * 64,
        manifest_sha256="c" * 64, frame_timestamps=(1.0, 2.0),
    )
    report = ReviewReportInput(binding=binding, required={"gap_removed": True})
    assert report.binding == binding


def test_state_compare_and_swap_rejects_stale_version(tmp_path):
    store = SessionStore(tmp_path)
    store.save_state({"version": 1, "state": "capture"})
    with pytest.raises(StaleSessionState):
        store.compare_and_swap(0, {"version": 1, "state": "preserve"})


def test_nested_media_path_must_be_exact_reference(tmp_path):
    paths = SessionPaths.create(tmp_path / "sessions", "abc123")
    allowed = (tmp_path / "source" / "clip.mov").resolve()
    paths.add_media_references([allowed])
    assert paths.require_read(allowed) == allowed
    with pytest.raises(AccessDenied):
        paths.require_read(allowed.parent / "neighbor.mov")
```

- [ ] **Step 3: Run the tests and confirm the boundary is incomplete**

Run: `uv run pytest tests/session/test_models.py tests/session/test_paths.py tests/session/test_store.py -q`

Expected: failures name missing or inconsistent binding, compare-and-swap, or journal APIs.

- [ ] **Step 4: Implement immutable contracts and compare-and-swap state**

Use these public shapes:

```python
@dataclass(frozen=True)
class EvidenceBinding:
    session_id: str
    pass_number: int
    state_version: int
    project_name: str
    candidate_sha256: str
    preview_sha256: str
    manifest_sha256: str
    frame_timestamps: tuple[float, ...]


@dataclass(frozen=True)
class ExternalAction:
    token: str
    action: str
    arguments: dict[str, Any]
    expected: dict[str, Any]
    status: Literal["pending", "complete", "blocked"]


def compare_and_swap(self, expected_version: int, value: dict[str, Any]) -> None:
    current = self.load_state()
    if int(current.get("version", 0)) != expected_version:
        raise StaleSessionState("Session state changed in another process")
    next_value = dict(value)
    next_value["version"] = expected_version + 1
    self.save_state(next_value)
```

Store complete `ExternalAction` records in journal rows and return records from `pending_actions()`.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/session/test_models.py tests/session/test_paths.py tests/session/test_store.py -q`

Expected: all focused tests pass.

```bash
git add src/editor_cli/session/models.py src/editor_cli/session/paths.py src/editor_cli/session/store.py tests/session/test_models.py tests/session/test_paths.py tests/session/test_store.py
git commit -m "feat: bind edit sessions to verified artifacts"
```

## Task 2: Create the strict one-shot Swift protocol

**Files:**
- Create: `native/final-cut-bridge/Package.swift`
- Create: `native/final-cut-bridge/Sources/FinalCutBridge/Protocol.swift`
- Create: `native/final-cut-bridge/Sources/FinalCutBridge/main.swift`
- Create: `native/final-cut-bridge/Tests/FinalCutBridgeTests/ProtocolTests.swift`

- [ ] **Step 1: Write failing Swift protocol tests**

```swift
func testAllowedActionDecodes() throws {
    let data = #"{"protocolVersion":1,"action":"probe","sessionRoot":"/tmp/session","payload":{}}"#.data(using: .utf8)!
    let request = try StrictProtocol.decodeRequest(data)
    XCTAssertEqual(request.action, .probe)
}

func testUnknownTopLevelKeyFails() {
    let data = #"{"protocolVersion":1,"action":"probe","sessionRoot":"/tmp/session","payload":{},"shell":"id"}"#.data(using: .utf8)!
    XCTAssertThrowsError(try StrictProtocol.decodeRequest(data))
}

func testUnknownActionFails() {
    let data = #"{"protocolVersion":1,"action":"run_script","sessionRoot":"/tmp/session","payload":{}}"#.data(using: .utf8)!
    XCTAssertThrowsError(try StrictProtocol.decodeRequest(data))
}
```

- [ ] **Step 2: Confirm the package is absent**

Run: `swift test --package-path native/final-cut-bridge`

Expected: failure because `Package.swift` does not exist.

- [ ] **Step 3: Implement strict decoding and one-response output**

Define the action enum exactly:

```swift
enum Action: String, Codable, CaseIterable {
    case probe
    case duplicateProject = "duplicate_project"
    case exportXML = "export_xml"
    case importXML = "import_xml"
    case openProject = "open_project"
    case sharePreview = "share_preview"
    case inspectDialogs = "inspect_dialogs"
}
```

`StrictProtocol.decodeRequest` must parse with `JSONSerialization`, require the exact key set `protocolVersion`, `action`, `sessionRoot`, `payload`, require protocol version `1`, reject unknown actions, and ensure `payload` is a dictionary. `main.swift` reads at most 1 MiB from stdin, dispatches once, writes one JSON object plus newline, and exits nonzero for protocol errors.

- [ ] **Step 4: Verify the protocol**

Run: `swift test --package-path native/final-cut-bridge --filter ProtocolTests`

Expected: all protocol tests pass and no listener is opened.

- [ ] **Step 5: Commit**

```bash
git add native/final-cut-bridge
git commit -m "feat: define native Final Cut bridge protocol"
```

## Task 3: Implement bounded Accessibility discovery and device probing

**Files:**
- Create: `native/final-cut-bridge/Sources/FinalCutBridge/Accessibility.swift`
- Create: `native/final-cut-bridge/Sources/FinalCutBridge/FinalCut.swift`
- Create: `native/final-cut-bridge/Tests/FinalCutBridgeTests/AccessibilityTests.swift`
- Create: `native/final-cut-bridge/Tests/FinalCutBridgeTests/ProbeTests.swift`

- [ ] **Step 1: Write failing tests with a fake AX tree**

```swift
func testMenuLookupRejectsAmbiguousItems() throws {
    let root = FakeNode(role: "AXApplication", children: [
        .menu("File", items: [.item("Export XML..."), .item("Export XML...")])
    ])
    XCTAssertThrowsError(try BoundedAX(root: root).uniqueMenuItem(path: ["File", "Export XML..."]))
}

func testProbeRejectsWrongBundleIdentifier() throws {
    let app = FakeFinalCut(bundleID: "example.fake", version: "12.3")
    XCTAssertThrowsError(try FinalCutProbe(system: app).run())
}

func testProbeRequiresExactVersionAndPermissions() throws {
    let app = FakeFinalCut(bundleID: "com.apple.FinalCutApp", version: "12.3", axTrusted: true, automation: true)
    let result = try FinalCutProbe(system: app).run()
    XCTAssertTrue(result.ready)
}
```

- [ ] **Step 2: Run and confirm missing types**

Run: `swift test --package-path native/final-cut-bridge --filter ProbeTests`

Expected: compilation fails because bounded AX and probe types do not exist.

- [ ] **Step 3: Implement the live system boundary**

Use `NSRunningApplication.runningApplications(withBundleIdentifier:)`, require exactly one process, read `CFBundleShortVersionString`, and create the root with `AXUIElementCreateApplication(pid)`. `BoundedAX` may traverse only these roles:

```swift
let allowedRoles: Set<String> = [
    kAXApplicationRole as String, kAXMenuBarRole as String,
    kAXMenuBarItemRole as String, kAXMenuRole as String,
    kAXMenuItemRole as String, kAXWindowRole as String,
    kAXSheetRole as String, kAXButtonRole as String,
    kAXTextFieldRole as String, kAXStaticTextRole as String,
    kAXProgressIndicatorRole as String,
]
```

Report `AXIsProcessTrusted()`. Test Automation through a read-only Apple Event that returns library names. Do not prompt from `probe`.

- [ ] **Step 4: Verify unit and live fail-closed behavior**

Run:

```bash
swift test --package-path native/final-cut-bridge --filter AccessibilityTests
swift test --package-path native/final-cut-bridge --filter ProbeTests
```

Expected: tests pass. A live `probe` may return `ready=false` until macOS permissions are granted.

- [ ] **Step 5: Commit**

```bash
git add native/final-cut-bridge
git commit -m "feat: probe Final Cut through native macOS APIs"
```

## Task 4: Add exact project identity and bounded Final Cut actions

**Files:**
- Create: `native/final-cut-bridge/Sources/FinalCutBridge/Actions.swift`
- Create: `native/final-cut-bridge/Tests/FinalCutBridgeTests/ActionTests.swift`
- Modify: `native/final-cut-bridge/Sources/FinalCutBridge/FinalCut.swift`
- Modify: `native/final-cut-bridge/Sources/FinalCutBridge/main.swift`

- [ ] **Step 1: Write failing action postcondition tests**

```swift
func testDuplicateUsesExactGeneratedNameAndPollsIdentity() throws {
    let system = FakeFinalCut(active: .init(library: "Canary", event: "Event", project: "Source", duration: 8))
    let result = try Actions(system: system).duplicateProject(
        expected: system.active!, name: "Source - a1b2c3 - Before AI", timeout: 2
    )
    XCTAssertEqual(result.project, "Source - a1b2c3 - Before AI")
    XCTAssertEqual(system.setValues, ["Source - a1b2c3 - Before AI"])
}

func testExportRejectsPathOutsideSession() {
    XCTAssertThrowsError(try SessionPath(root: "/tmp/session").output("/tmp/other/source.fcpxml"))
}

func testShareWaitsForStableMovieAndBackgroundCompletion() throws {
    let result = try Actions(system: FakeFinalCut.shareCompletes()).sharePreview(
        expected: .canaryCandidate, output: "/tmp/session/pass-01.mov", timeout: 30
    )
    XCTAssertEqual(result.kind, "final_cut_share")
    XCTAssertEqual(result.project, .canaryCandidate)
}
```

- [ ] **Step 2: Confirm actions are absent**

Run: `swift test --package-path native/final-cut-bridge --filter ActionTests`

Expected: compilation fails for missing `Actions`.

- [ ] **Step 3: Implement all seven actions**

Each mutation must use a deadline, exact `ProjectIdentity`, and a postcondition. Menu paths are fixed to the English Final Cut 12.3 device contract:

```swift
enum FinalCutMenu {
    static let duplicate = ["File", "Duplicate Project As..."]
    static let exportXML = ["File", "Export XML..."]
    static let share = ["File", "Share", "Export File (Default)..."]
}
```

Use `AXPress` on unique enabled elements and `kAXValueAttribute` only on the unique visible text field in the expected sheet. `import_xml` uses `NSWorkspace.shared.open` for the candidate and then polls. `open_project` acts only on an exact project row under the exact event and library. `inspect_dialogs` returns sanitized roles and titles only.

- [ ] **Step 4: Verify action contracts**

Run: `swift test --package-path native/final-cut-bridge`

Expected: protocol, probe, accessibility, path, timeout, ambiguity, and postcondition tests pass.

- [ ] **Step 5: Commit**

```bash
git add native/final-cut-bridge
git commit -m "feat: control Final Cut with verified native actions"
```

## Task 5: Wrap the native helper in Python

**Files:**
- Create: `src/editor_cli/adapters/native_final_cut.py`
- Create: `tests/adapters/test_native_final_cut.py`
- Modify: `src/editor_cli/adapters/final_cut_control.py`
- Modify: `tests/adapters/test_final_cut_control.py`

- [ ] **Step 1: Write failing subprocess and response tests**

```python
def test_native_client_sends_one_strict_request(tmp_path):
    runner = FakeRunner(stdout='{"ok":true,"result":{"protocolVersion":1}}\n')
    client = NativeFinalCutClient(tmp_path / "bridge", runner=runner)
    client.probe(tmp_path)
    request = json.loads(runner.input)
    assert set(request) == {"protocolVersion", "action", "sessionRoot", "payload"}
    assert request["action"] == "probe"


def test_native_client_rejects_unbound_share_result(tmp_path):
    client = NativeFinalCutClient(tmp_path / "bridge", runner=FakeRunner(stdout=wrong_identity_response()))
    with pytest.raises(NativeFinalCutError, match="identity"):
        client.share_preview(expected_identity(), tmp_path / "session" / "pass.mov")
```

- [ ] **Step 2: Confirm the adapter is missing**

Run: `uv run pytest tests/adapters/test_native_final_cut.py -q`

Expected: import failure for `native_final_cut`.

- [ ] **Step 3: Implement the typed adapter**

Expose only these methods:

```python
class NativeFinalCutClient:
    def probe(self, session_root: Path) -> NativeProbe: ...
    def duplicate_project(self, identity: ProjectIdentity, name: str, session_root: Path) -> ProjectIdentity: ...
    def export_xml(self, identity: ProjectIdentity, destination: Path, session_root: Path) -> ExportReceipt: ...
    def import_xml(self, identity: ProjectIdentity, source: Path, session_root: Path) -> ProjectIdentity: ...
    def open_project(self, identity: ProjectIdentity, session_root: Path) -> ProjectIdentity: ...
    def share_preview(self, identity: ProjectIdentity, destination: Path, session_root: Path) -> ShareReceipt: ...
    def inspect_dialogs(self, session_root: Path) -> tuple[BlockingDialog, ...]: ...
```

Run the helper with `subprocess.run(..., input=request_json, text=True, capture_output=True, timeout=action_timeout, check=False)`. Enforce a 1 MiB response cap, exact protocol version, exact identity, and session-root containment.

- [ ] **Step 4: Replace the Final Cut control implementation**

`FinalCutControl.render_preview` must call `share_preview`. Keep the FCPXML proxy under a separate `render_diagnostic_proxy` name that cannot satisfy review. Replace CommandPost imports and constructor parameters with `NativeFinalCutClient`.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/adapters/test_native_final_cut.py tests/adapters/test_final_cut_control.py -q`

Expected: all native adapter and Final Cut control tests pass.

```bash
git add src/editor_cli/adapters/native_final_cut.py src/editor_cli/adapters/final_cut_control.py tests/adapters/test_native_final_cut.py tests/adapters/test_final_cut_control.py
git commit -m "feat: route Final Cut control through native helper"
```

## Task 6: Build, install, and package the native helper

**Files:**
- Create: `src/editor_cli/resources/__init__.py`
- Create: `src/editor_cli/resources/`
- Create: `tests/test_native_setup.py`
- Modify: `src/editor_cli/setup.py`
- Modify: `tests/test_setup.py`
- Modify: `tests/test_packaged_resources.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Write failing package and setup tests**

```python
def test_distribution_contains_native_sources_and_skill(built_wheel):
    names = set(wheel_names(built_wheel))
    assert "editor_cli/resources/native/Package.swift" in names
    assert "editor_cli/resources/skills/final-cut-editor/SKILL.md" in names
    assert "editor_cli/resources/canary/fcp_live_canary.py" in names


def test_setup_builds_and_signs_stable_helper(tmp_path, fake_platform):
    result = run_setup(setup_paths(tmp_path), platform=fake_platform)
    helper = tmp_path / "Library/Application Support/Editor CLI/bin/editor-fcp-bridge"
    assert helper in result.changed
    assert fake_platform.commands_contain(["swift", "build", "-c", "release"])
    assert fake_platform.commands_contain(["codesign", "--force", "--sign", "-"])
```

- [ ] **Step 2: Confirm resources are missing**

Run: `uv run pytest tests/test_native_setup.py tests/test_packaged_resources.py -q`

Expected: failures show absent resources and native build step.

- [ ] **Step 3: Implement package resources and stable installation**

Use `importlib.resources.files("editor_cli.resources")` and copy Swift sources to a mode-700 temporary build directory. Build with:

```python
("swift", "build", "--package-path", str(source), "-c", "release")
```

Copy the binary atomically to the stable path, set mode `0o700`, and sign with:

```text
codesign --force --sign - --identifier com.screddy.editorcli.finalcutbridge <binary>
```

Persist the SHA-256 and protocol version beside the binary. Remove CommandPost download, license-app, plugin, and WebSocket setup steps.

- [ ] **Step 4: Verify an installed wheel**

Run:

```bash
uv build
TMP_ENV=$(mktemp -d)
uv venv "$TMP_ENV/venv"
uv pip install --python "$TMP_ENV/venv/bin/python" dist/editor_cli-0.1.0-py3-none-any.whl
"$TMP_ENV/venv/bin/python" -c 'from editor_cli.resources import native_source; assert native_source().joinpath("Package.swift").is_file()'
```

Expected: wheel and source distribution contain all resources; the installed wheel resolves them without a repository checkout.

- [ ] **Step 5: Commit**

```bash
git add src/editor_cli/resources src/editor_cli/setup.py tests/test_native_setup.py tests/test_setup.py tests/test_packaged_resources.py pyproject.toml uv.lock
git commit -m "build: package native Final Cut controller"
```

## Task 7: Make host configuration atomic and collision-safe

**Files:**
- Modify: `src/editor_cli/setup.py`
- Modify: `tests/test_setup.py`

- [ ] **Step 1: Write failing collision and recovery tests**

```python
def test_setup_refuses_unmanaged_claude_name_collision(tmp_path, fake_platform):
    config = tmp_path / ".claude.json"
    config.write_text(json.dumps({"mcpServers": {"editor-cli": {"command": "/other/tool"}}}))
    with pytest.raises(SetupError, match="unmanaged editor-cli entry"):
        run_setup(paths_with_claude(config), platform=fake_platform)


def test_atomic_config_write_restores_after_validation_failure(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[existing]\nvalue = 1\n")
    with pytest.raises(SetupError):
        atomic_config_update(config, b"not toml", parse=tomllib.loads)
    assert config.read_text() == "[existing]\nvalue = 1\n"
```

- [ ] **Step 2: Confirm current writes are unsafe**

Run: `uv run pytest tests/test_setup.py -k 'collision or atomic' -q`

Expected: failures show replacement of unmanaged entries or non-atomic writes.

- [ ] **Step 3: Implement atomic validated writes**

Write a sibling temporary file, flush and `os.fsync`, parse the temporary content, `os.replace`, then fsync the parent directory. On post-write verification failure, restore the fixed `.editor-cli.bak` through the same atomic path. Recognize a managed MCP entry only when its command and marker match Editor CLI's installed configuration.

- [ ] **Step 4: Verify idempotency and preservation**

Run: `uv run pytest tests/test_setup.py tests/test_native_setup.py -q`

Expected: collision, rollback, unrelated-key preservation, and repeat-run tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/editor_cli/setup.py tests/test_setup.py tests/test_native_setup.py
git commit -m "fix: update agent configuration atomically"
```

## Task 8: Replace doctor with native capability checks

**Files:**
- Modify: `src/editor_cli/mcp_server.py`
- Modify: `src/editor_cli/config.py`
- Modify: `src/editor_cli/cli.py`
- Modify: `tests/test_mcp_server.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli_smoke.py`

- [ ] **Step 1: Write failing native-doctor tests**

```python
def test_device_report_requires_native_helper_and_no_paid_app(fake_probe):
    fake_probe.return_value = NativeProbe(
        protocol_version=1, helper_sha256="a" * 64,
        final_cut_bundle_id="com.apple.FinalCutApp", final_cut_version="12.3",
        accessibility=True, automation=True, ready=True, dialogs=(),
    )
    report = device_report(config=native_config(), probe=fake_probe)
    assert report["ready"] is True
    assert "commandpost" not in report
    assert "license_app" not in json.dumps(report).lower()


def test_device_report_rejects_wrong_helper_or_final_cut_version(fake_probe):
    fake_probe.return_value = replace(valid_probe(), protocol_version=2)
    assert device_report(config=native_config(), probe=fake_probe)["ready"] is False
    fake_probe.return_value = replace(valid_probe(), final_cut_version="12.4")
    assert device_report(config=native_config(), probe=fake_probe)["ready"] is False


def test_mcp_import_does_not_require_watch_install(monkeypatch):
    monkeypatch.setattr("editor_cli.adapters.watch.WatchAdapter.__init__", lambda *args: (_ for _ in ()).throw(FileNotFoundError()))
    importlib.reload(editor_cli.mcp_server)
    assert editor_cli.mcp_server.device_report(probe=lambda *_: not_ready_probe())
```

- [ ] **Step 2: Run and confirm the old doctor contract**

Run: `uv run pytest tests/test_mcp_server.py tests/test_config.py -q`

Expected: failures reference CommandPost, LateNite, fixed port 27480, or eager watch construction.

- [ ] **Step 3: Implement the native report**

Add configuration fields:

```python
native_helper: Path = Path("~/Library/Application Support/Editor CLI/bin/editor-fcp-bridge")
native_protocol_version: int = 1
native_action_timeout_seconds: int = 120
```

Build services lazily on the first mutating MCP call. `device_report` reads helper metadata, runs `probe`, checks exact helper hash and protocol version, requires Final Cut bundle ID and version 12.3, checks both permissions, and reports blocking dialogs. Remove CommandPost URL and port readiness.

Add `editor-cli permissions request`. That explicit command invokes the helper's permission-request mode and may show the macOS Accessibility and Automation prompts. `doctor` remains read-only and never opens a prompt.

- [ ] **Step 4: Verify MCP and CLI parity**

Run: `uv run pytest tests/test_mcp_server.py tests/test_cli_smoke.py tests/test_config.py -q`

Expected: doctor returns the same native readiness on CLI and MCP without requiring watch during module import.

- [ ] **Step 5: Commit**

```bash
git add src/editor_cli/mcp_server.py src/editor_cli/config.py src/editor_cli/cli.py tests/test_mcp_server.py tests/test_config.py tests/test_cli_smoke.py
git commit -m "feat: diagnose native Final Cut control"
```

## Task 9: Enforce controller-owned acceptance and candidate-derived QC

**Files:**
- Modify: `src/editor_cli/session/models.py`
- Modify: `src/editor_cli/session/controller.py`
- Modify: `src/editor_cli/services.py`
- Modify: `src/editor_cli/verification/review.py`
- Modify: `src/editor_cli/verification/technical.py`
- Modify: `tests/session/test_controller.py`
- Modify: `tests/integration/test_services.py`
- Modify: `tests/verification/test_review.py`
- Modify: `tests/verification/test_technical.py`

- [ ] **Step 1: Write failing acceptance-binding tests**

```python
@pytest.mark.anyio
async def test_review_cannot_replace_controller_required_checks(ready_candidate):
    service = ready_candidate.verify_service
    with pytest.raises(ServiceError, match="exact required checks"):
        await service.dispatch("record", ready_candidate.session_id, pass_number=1,
            report={"required": {"looks_good": True}, "binding": ready_candidate.binding_dict})


@pytest.mark.anyio
async def test_review_rejects_stale_preview_hash(ready_candidate):
    report = ready_candidate.valid_report()
    report["binding"]["preview_sha256"] = "0" * 64
    with pytest.raises(ServiceError, match="preview hash"):
        await ready_candidate.verify_service.dispatch("record", ready_candidate.session_id, pass_number=1, report=report)


def test_technical_qc_uses_candidate_duration(valid_candidate, preview):
    result = inspect_candidate(valid_candidate, preview, expected_source_duration=20)
    assert result.expected_duration == 12.0
    assert result.fcpxml_valid is True
```

- [ ] **Step 2: Confirm the old self-certification path**

Run: `uv run pytest tests/session/test_controller.py tests/integration/test_services.py tests/verification/test_review.py tests/verification/test_technical.py -q`

Expected: a one-key true report is accepted or candidate duration is not used.

- [ ] **Step 3: Derive and persist required checks**

Normalize `EditRequest.required_operations` into controller-owned keys. Always include:

```python
BASE_REQUIRED_CHECKS = frozenset({
    "source_unchanged", "candidate_xml_valid", "preview_rendered", "preview_watched",
})
```

Map each requested operation to a named check such as `gap_removed`, `title_visible`, or `reaction_insert_visible`. Persist the exact sorted tuple at session start. At record time require exact key equality, strict booleans, a matching `EvidenceBinding`, and current state version.

- [ ] **Step 4: Validate candidate XML and diagnostics**

Parse the candidate with the pinned parser, derive its duration and media references, call `diagnose.validate_timeline`, reject missing media, and pass candidate duration to preview inspection. Store candidate, preview, and manifest hashes before returning the candidate for review.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/session/test_controller.py tests/integration/test_services.py tests/verification/test_review.py tests/verification/test_technical.py -q`

Expected: all acceptance, stale-evidence, malformed-XML, changed-duration, and missing-media tests pass.

```bash
git add src/editor_cli/session/models.py src/editor_cli/session/controller.py src/editor_cli/services.py src/editor_cli/verification/review.py src/editor_cli/verification/technical.py tests/session/test_controller.py tests/integration/test_services.py tests/verification/test_review.py tests/verification/test_technical.py
git commit -m "fix: make edit acceptance controller-owned"
```

## Task 10: Add session locking, journal reconciliation, and safe undo

**Files:**
- Create: `src/editor_cli/session/locking.py`
- Create: `src/editor_cli/session/reconcile.py`
- Create: `tests/session/test_locking.py`
- Create: `tests/session/test_reconcile.py`
- Modify: `src/editor_cli/session/controller.py`
- Modify: `src/editor_cli/session/capture.py`
- Modify: `src/editor_cli/services.py`
- Modify: `tests/session/test_controller.py`
- Modify: `tests/session/test_capture.py`

- [ ] **Step 1: Write failing concurrency and recovery tests**

```python
def test_second_process_cannot_lock_same_session(tmp_path):
    first = SessionLock(tmp_path / "session")
    with first:
        with pytest.raises(SessionBusy):
            with SessionLock(tmp_path / "session", blocking=False):
                pass


@pytest.mark.anyio
async def test_resume_reconciles_completed_import_without_replay(crashed_session):
    crashed_session.fcp.projects = (crashed_session.expected_candidate,)
    result = await crashed_session.controller.resume(crashed_session.id)
    assert result.state is SessionState.PREVIEW
    assert crashed_session.fcp.import_calls == 0


@pytest.mark.anyio
async def test_undo_is_journaled_and_creates_new_project(ready_session):
    result = await ready_session.controller.undo(ready_session.id)
    assert result.project_name.endswith("Undo 1")
    assert ready_session.store.events()[-1]["kind"] == "external_action_completed"
```

- [ ] **Step 2: Confirm missing transaction boundaries**

Run: `uv run pytest tests/session/test_locking.py tests/session/test_reconcile.py tests/session/test_controller.py tests/session/test_capture.py -q`

Expected: missing lock and reconciliation APIs or duplicate action calls.

- [ ] **Step 3: Implement the lock and reconcilers**

Use `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on `<session>/.lock`. Hold it across load, validation, external action, receipt, and compare-and-swap save. Implement reconcilers for `export_xml`, `duplicate_project`, `import_xml`, `share_preview`, `open_project`, and `download` using exact identities and hashes.

If capture crashed before identity persisted, call native `probe`, match the journal's expected identity, and continue only when it is exact. Never replay a pending action before reconciliation.

- [ ] **Step 4: Route undo through controller state**

Create an `Undo N` candidate from the prior accepted FCPXML, import it, verify exact identity, open it, and write intent plus receipt. Remove direct `fcp.open_project` calls from `TimelineService`.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/session/test_locking.py tests/session/test_reconcile.py tests/session/test_controller.py tests/session/test_capture.py tests/integration/test_services.py -q`

Expected: concurrent apply fails before external mutation; restart cases reconcile without replay; undo is versioned and journaled.

```bash
git add src/editor_cli/session/locking.py src/editor_cli/session/reconcile.py src/editor_cli/session/controller.py src/editor_cli/session/capture.py src/editor_cli/services.py tests/session/test_locking.py tests/session/test_reconcile.py tests/session/test_controller.py tests/session/test_capture.py tests/integration/test_services.py
git commit -m "feat: reconcile interrupted Final Cut actions"
```

## Task 11: Constrain edit programs and expose complete planning data

**Files:**
- Modify: `src/editor_cli/session/models.py`
- Modify: `src/editor_cli/session/paths.py`
- Modify: `src/editor_cli/adapters/timeline_engine.py`
- Modify: `src/editor_cli/adapters/fcp_assets.py`
- Modify: `src/editor_cli/services.py`
- Modify: `tests/session/test_models.py`
- Modify: `tests/session/test_paths.py`
- Modify: `tests/adapters/test_timeline_engine.py`
- Modify: `tests/adapters/test_fcp_assets.py`
- Modify: `tests/integration/test_services.py`

- [ ] **Step 1: Write failing strict-action and inspection tests**

```python
def test_edit_action_rejects_unknown_and_raw_path_fields():
    with pytest.raises(ValidationError):
        EditOperation.model_validate({"group": "edit", "action": "add_audio", "arguments": {"src": "/Users/me/private.mov"}})


@pytest.mark.anyio
async def test_inspect_returns_structured_planning_data(service):
    result = await service.dispatch("inspect", "abc123")
    assert set(result) >= {"clips", "gaps", "roles", "markers", "effects", "transcript", "pacing", "installed_assets"}


@pytest.mark.anyio
async def test_acquired_asset_id_can_be_inserted(service, acquired_asset):
    registered = await service.media.dispatch("register", service.session_id, asset_path=acquired_asset.path)
    program = insert_program(asset_id=registered["asset_id"])
    assert await service.timeline.dispatch("apply", service.session_id, edit_program=program)
```

- [ ] **Step 2: Confirm raw dictionaries still pass**

Run: `uv run pytest tests/session/test_models.py tests/session/test_paths.py tests/adapters/test_timeline_engine.py tests/adapters/test_fcp_assets.py tests/integration/test_services.py -q`

Expected: arbitrary path arguments are accepted or promised structured fields are missing.

- [ ] **Step 3: Implement action-specific models**

Use Pydantic models with `ConfigDict(extra="forbid")`. Path-bearing actions accept only controller-issued `asset_id`. Resolve IDs through the session asset registry, then pass canonical paths already approved by `SessionPaths.require_read`. Persist exact media references parsed from source FCPXML in session state.

- [ ] **Step 4: Build normalized inspection**

Call pinned grouped inspect, diagnose, mark, transcript, and index reads. Normalize results into lists of JSON records. Include installed asset catalog entries with stable `kind/category/name` identifiers. `editor_media.register` accepts only existing session assets or exact captured references.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/session/test_models.py tests/session/test_paths.py tests/adapters/test_timeline_engine.py tests/adapters/test_fcp_assets.py tests/integration/test_services.py -q`

Expected: strict schemas, exact-reference confinement, structured inspection, catalog exposure, and acquired-media insertion pass.

```bash
git add src/editor_cli/session/models.py src/editor_cli/session/paths.py src/editor_cli/adapters/timeline_engine.py src/editor_cli/adapters/fcp_assets.py src/editor_cli/services.py tests/session/test_models.py tests/session/test_paths.py tests/adapters/test_timeline_engine.py tests/adapters/test_fcp_assets.py tests/integration/test_services.py
git commit -m "feat: constrain and enrich timeline planning"
```

## Task 12: Harden internet media transport and recovery

**Files:**
- Modify: `src/editor_cli/acquire/internet.py`
- Modify: `src/editor_cli/services.py`
- Modify: `src/editor_cli/session/reconcile.py`
- Modify: `tests/acquire/test_internet.py`
- Modify: `tests/integration/test_services.py`
- Modify: `tests/session/test_reconcile.py`

- [ ] **Step 1: Write failing SSRF, timeout, and crash tests**

```python
def test_redirect_to_private_peer_is_rejected(fetcher):
    fetcher.responses = [redirect("https://cdn.example/a.mp4"), connected("127.0.0.1")]
    with pytest.raises(AcquisitionError, match="public address"):
        fetcher.acquire("https://example.com/a.mp4", purpose="reaction")


def test_macho_magic_is_rejected(tmp_path):
    path = tmp_path / "asset"
    path.write_bytes(b"\xfe\xed\xfa\xce" + b"0" * 100)
    with pytest.raises(AcquisitionError, match="executable"):
        reject_executable(path)


def test_timeout_leaves_no_final_asset(fetcher, tmp_path):
    fetcher.runner = timeout_runner
    with pytest.raises(AcquisitionError, match="timed out"):
        fetcher.acquire("https://example.com/a.mp4", purpose="reaction")
    assert not list(tmp_path.glob("*.mp4"))
```

- [ ] **Step 2: Confirm transport checks are incomplete**

Run: `uv run pytest tests/acquire/test_internet.py tests/session/test_reconcile.py -q`

Expected: connected-peer or timeout tests fail, or malformed Mach-O bytes pass.

- [ ] **Step 3: Implement validating transport**

Use an HTTPS client that disables automatic redirects. For each hop, resolve every address, reject private, loopback, link-local, multicast, reserved, and unspecified ranges, connect with bounded timeouts, verify the connected peer address, validate TLS hostname, and repeat for at most five redirects. Hand yt-dlp only a validated direct media URL whose final host is pinned for the download, or download through this transport when the source is a direct file.

Write `download` intent before network access. Download to `<assets>/.partial-<token>`, stream-hash and enforce 500 MB, fsync, scan magic, then atomically rename and write provenance. Reconcile by canonical URL, final path, and hash.

- [ ] **Step 4: Verify content and recovery limits**

Run: `uv run pytest tests/acquire/test_internet.py tests/session/test_reconcile.py tests/integration/test_services.py -q`

Expected: private redirects, DNS rebinding simulations, timeouts, oversized files, executables, partial files, and recovered downloads behave fail-closed.

- [ ] **Step 5: Commit**

```bash
git add src/editor_cli/acquire/internet.py src/editor_cli/services.py src/editor_cli/session/reconcile.py tests/acquire/test_internet.py tests/integration/test_services.py tests/session/test_reconcile.py
git commit -m "fix: harden internet edit asset acquisition"
```

## Task 13: Replace the canary with native Final Cut recovery acceptance

**Files:**
- Modify: `scripts/fcp_live_canary.py`
- Modify: `tests/integration/test_live_canary_contract.py`
- Modify: `tests/fixtures/canary/expected.json`
- Create: `native/final-cut-bridge/Tests/FinalCutBridgeTests/CanaryContractTests.swift`

- [ ] **Step 1: Extend the canary contract tests**

```python
def test_canary_requires_native_render_source_recapture_and_restart():
    expected = load_expected()
    assert set(expected["required_checks"]) == {
        "source_unchanged", "source_project_unchanged", "gap_removed",
        "title_visible", "transition_visible", "reaction_insert_visible",
        "preview_rendered_by_final_cut", "preview_watched", "restart_reconciled",
    }


def test_canary_rejects_proxy_preview(canary_result):
    canary_result["render_kind"] = "ffmpeg_proxy"
    assert not fcp_live_canary.all_required_checks_pass(canary_result)


@pytest.mark.anyio
async def test_second_run_reconciles_pending_share_without_replay(fake_live_canary):
    result = await fake_live_canary.restart_after_share_completed()
    assert result["required_checks"]["restart_reconciled"] is True
    assert fake_live_canary.native.share_calls == 1
```

- [ ] **Step 2: Confirm the old canary lacks native evidence**

Run: `uv run pytest tests/integration/test_live_canary_contract.py -q`

Expected: required checks, source-project recapture, or restart cases fail.

- [ ] **Step 3: Implement the two-run canary**

Use a disposable `.fcpbundle` and generated media only. Export the original through the native helper before editing and hash its FCPXML plus every referenced generated media file. After import, stop the first controller instance immediately after the helper completes `share_preview` but before its receipt is saved. Start a new controller, reconcile the stable movie without another share, watch it, record the exact bound review, re-export the original project, compare hashes, and open the accepted candidate.

- [ ] **Step 4: Verify offline and run live only when doctor is ready**

Run:

```bash
uv run pytest tests/integration/test_live_canary_contract.py -q
swift test --package-path native/final-cut-bridge --filter CanaryContractTests
uv run editor-cli doctor
uv run python scripts/fcp_live_canary.py
```

Expected before permissions: doctor and canary fail before creating a library. Expected after real permissions: every required check is true, render kind is `final_cut_share`, source project hashes match, restart reconciles without replay, and the candidate stays open.

- [ ] **Step 5: Commit**

```bash
git add scripts/fcp_live_canary.py tests/integration/test_live_canary_contract.py tests/fixtures/canary/expected.json native/final-cut-bridge/Tests/FinalCutBridgeTests/CanaryContractTests.swift
git commit -m "test: verify native Final Cut edit recovery"
```

## Task 14: Remove paid-controller code and document the working device

**Files:**
- Delete: `src/editor_cli/adapters/commandpost.py`
- Delete: `commandpost/editor-cli-bridge/init.lua`
- Delete or rewrite: CommandPost-only tests
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `skills/final-cut-editor/SKILL.md`
- Modify: `docs/superpowers/specs/2026-09-05-native-final-cut-controller-design.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Remove stale runtime and dependency references**

Run:

```bash
rg -n -i 'commandpost|latenite|fast collections|27480|websocket bridge' src tests scripts skills README.md CLAUDE.md pyproject.toml docs/superpowers/specs/2026-09-05-native-final-cut-controller-design.md
```

Expected before cleanup: references identify the old adapter, setup, tests, and docs. Remove runtime claims and keep historical migration notes only in the superseded spec.

- [ ] **Step 2: Update operator documentation**

Document these exact commands:

```bash
uv run editor-cli setup
uv run editor-cli permissions request
uv run editor-cli doctor
uv run editor-cli edit-active "remove gaps and add two restrained meme beats"
uv run editor-cli session status <session-id>
uv run editor-cli session resume <session-id>
```

State that the native helper is free, local, signed, networkless, session-confined, and uses Final Cut Share previews. State that the user performs final export.

- [ ] **Step 3: Run the complete verification matrix**

Run:

```bash
swift test --package-path native/final-cut-bridge
uv run pytest -q
uvx ruff check src tests scripts
uvx ruff format --check src tests scripts
uv build
uv run python -m editor_cli.mcp_server --help
git diff --check "$(git merge-base main HEAD)"..HEAD
```

Expected: Swift and Python suites pass, lint and format pass, both distributions build, MCP help exits zero, and the branch range has no whitespace errors.

- [ ] **Step 4: Verify package and both agent hosts**

Install the wheel into a temporary environment, run setup in dry-run mode, build the helper, and initialize the four grouped MCP tools. In fresh Claude Code and Codex sessions, call native doctor and read the same successful canary manifest and preview hash. Record the exact hash and evidence path only after the real canary passes.

- [ ] **Step 5: Commit, push, and review without merging**

```bash
git add -A
git commit -m "docs: ship native Final Cut controller"
git push origin codex/final-cut-closed-loop-controller
gh pr view 18 --json state,isDraft,headRefOid,mergeStateStatus,statusCheckRollup
```

Expected: PR #18 remains open and draft, points to the final commit, contains a meaningful README update, and reports the actual configured checks. Leave it unmerged.
