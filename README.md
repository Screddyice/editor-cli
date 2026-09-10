# Editor CLI

Edit selected footage with Codex or Claude Code and receive a finished MP4.
The direct workflow handles transcription, cut decisions, rendering, and review
without Final Cut. A separate controller supports projects in Final Cut Pro.

## Direct editing with Codex

Select the exact clips and optional context documents in your conversation, then
ask for an edit: “Make a five-minute vlog. Keep the beach sequence and use this
voice-over.” Codex inventories those files, reads footage samples and any word
transcripts needed for captions, and asks you to approve one editing strategy. It then
renders, inspects the result, makes corrections within three preview passes,
and exports the finished MP4. It also inspects the final render before delivery.

```bash
uv run editor-cli direct setup
uv run editor-cli direct doctor
uv run editor-cli direct start --file /path/to/clip1.mov --file /path/to/clip2.mp4 \
  --file /path/to/brief.md --prompt 'Make a travel vlog with captions'
```

`direct setup` installs the `direct-video-editor` skill for Codex and Claude
Code. Existing Editor CLI MCP registrations expose the new `editor_direct` tool
after an agent restart. The skill also includes the installed Python command,
so the workflow works through the CLI before tools refresh. New MCP clients can
register `editor-cli-mcp` as a stdio server; direct editing needs no native helper.

Inputs are exact selected files, not a directory scan. The first selected file's
parent receives `edit/<session-id>/`, containing snapshots, cached transcripts,
edit decisions, rendered previews, review images/audio, notes, and `final.mp4`.
All decisions use returned asset IDs. Source files remain untouched. The
controller checks source and render hashes on resume and rejects stale reviews.

Available edits include ordered cuts, still inserts, speed changes, simple
grades, titles, full-frame or picture-in-picture overlays, timed word captions,
one finite non-looping narration track, and looping music with ducking. Footage
audio stays in the mix unless its cut volume is zero. Music can duck against the
combined footage and narration. Output dimensions and frame rate are explicit;
mixed aspect ratios receive letterboxing. Overlay audio is muted; use a timeline
cut for an audible meme. Captions appear above inserts. Direct internet imports
accept public HTTPS media URLs (500 MB maximum), validate and pin public IPs on
each redirect, and record provenance. Website extraction and browser cookies
are outside this workflow.

FFmpeg and ffprobe provide local rendering. Optional verbatim word transcription uses
ElevenLabs Scribe: set `ELEVENLABS_API_KEY` in the process or point
`EDITOR_CLI_ENV_FILE` at your credential file. The source checkout also checks
its own `.env` and the machine's `~/projects/.env` for that one key. No Gemini
key is required for direct editing. `direct doctor` reports rendering and
transcription readiness separately. Source audio and narration render without a
key. Word-aware trimming and timed captions require transcripts; caption plans
with missing word coverage fail instead of dropping captions.

The agent inspects returned filmstrips, waveforms, decoded audio measurements,
and transcript timing. It can also listen with the host's audio/video tools.
Review entries are the agent's observations, not an
automatic claim of visual or audio quality. If the host cannot inspect audio,
the agent identifies its use of signal/transcript checks and leaves any unresolved
perceptual judgment pending. HDR tone mapping
and automatic loudness normalization are not included in the first renderer.

The shared CLI/MCP sequence is `start → inspect/transcribe → approve → render →
review → export → review → finish`. Resume with `editor-cli direct run resume
/absolute/edit/session-id`. For large arguments, use `direct run ACTION SESSION
--data-file /absolute/session/arguments.json`. See the packaged
[agent workflow](src/editor_cli/resources/skills/direct-video-editor/SKILL.md)
for edit and review schemas.

Run `uv run pytest tests/direct tests/test_mcp_server.py` for file isolation,
review/recovery contracts, and offline FFmpeg tests using generated media. These
checks verify the software; a creative acceptance edit still needs user footage.
You can use your recorded speech or selected voice-over without an ASR service.
Captions default off; enable them after obtaining word timestamps.

## Original Final Cut workflow

Given raw footage (imported into Final Cut Pro), an editing **prompt**, and
optional **video style references**:

1. Analyze the footage — word-level transcript + on-demand visual analysis.
2. Extract the desired **editing style** from reference videos. Gemini *watches*
   them (pacing, cut rhythm, color look, titles, transitions, music feel) and
   emits a structured style profile.
3. Produce a real Final Cut Pro timeline via **FCPXML** that reflects the prompt
   + style.
4. Refine in FCP; FCP exports the **final `.mp4`**.
5. Gemini evaluates the export against the prompt + style and suggests
   iterations; regenerate and repeat.

This workflow retains an editable Final Cut timeline. The user makes the final
delivery export in Final Cut; the direct workflow above exports its own MP4.

## Status

The original three phases remain available alongside the direct editor and
native Final Cut controller:

- **Phase 1 — spine:** acquire → Gemini style → transcribe/probe → reason EDL →
  ffmpeg mp4 + FCPXML → Gemini eval loop. EDL→FCPXML validated against Final
  Cut's **v1.14 DTD**.
- **Phase 2 — discovery:** `--genre "<query>"` finds trending comparable videos
  (yt-dlp search), extracts sound/title metadata, feeds them as extra Gemini
  references + trend context.
- **Phase 3 — social:** Instagram/TikTok reference URLs via yt-dlp cookie auth
  (`--cookies-from-browser` / `--cookies`), retry hardening, actionable errors.

### Legacy Gemini and ffmpeg workflow gates

These gates apply to the original Gemini and ffmpeg workflow. They do not
determine Final Cut controller readiness:

1. **API keys required** — set `GEMINI_API_KEY` (or `CLIQK_GEMINI_API_KEY`) and
   `ELEVENLABS_API_KEY`. Check your selected credential configuration before use.
2. **Manual FCP import** — import a generated `timeline.fcpxml` into Final Cut
   Pro 12.3 once to confirm it opens with media linked (DTD-valid, GUI-import
   pending).

### Final Cut closed-loop controller

Editor CLI includes a closed-loop controller for Final Cut Pro 12.3. Claude
Code and Codex use the same four grouped MCP tools: `editor_session`,
`editor_timeline`, `editor_media`, and `editor_verify`. An edit session captures
the selected project, preserves its exported source, creates up to three
working candidates, renders each candidate through Final Cut, and stores
`watch` evidence before it opens a candidate for the editor.

The controller accepts only the selected project's media references, installed
Final Cut and Motion assets, and files inside the current session. Public media
goes through `editor_media.acquire`; it requires public HTTPS, rejects private
addresses and executable content, caps downloads at 500 MB, and writes
provenance to the session. It does not search unrelated folders, libraries,
Photos, drives, browser profiles, or credential stores.

The editor performs the final export in Final Cut Pro. Editor CLI renders
temporary previews and never exports, publishes, or uploads the final master.

#### Host setup and readiness

Run setup once to install the pinned dependencies and register the MCP server
for both hosts:

```bash
uv run editor-cli setup
uv run editor-cli doctor
```

The native setup builds and signs the project-owned Swift helper and registers
the MCP server. Run `editor-cli permissions request` to request macOS
Accessibility and Automation permissions, then check `editor-cli doctor`.
Doctor is read-only. The native controller does not require CommandPost or a
paid LateNite application. Its live Final Cut acceptance remains pending; direct
editing has its own readiness checks and does not use those permissions.

If you already configured your agent hosts, install the helper without changing
their MCP registrations or skills:

```bash
uv run editor-cli setup --native-only
uv run editor-cli permissions request
uv run editor-cli doctor
```

Both setup modes offer `--dry-run`. Full setup refuses unmanaged name collisions;
`--native-only` leaves those entries untouched. A successful helper build does
not mean macOS granted control permissions or that Final Cut passed a live edit.

#### Session workflow and recovery

```bash
uv run editor-cli doctor
uv run editor-cli edit-active "remove pauses and add a restrained title" \
  --operation remove_gaps \
  --operation add_title
uv run editor-cli session status <session-id>
uv run editor-cli session resume <session-id>
```

Each `edit-active` request must name every required operation with a repeatable
`--operation` option. The MCP `editor_session` start action uses the matching
`required_operations` list. The controller rejects missing or unknown operations
and persists the corresponding acceptance checks before capture begins.

Sessions live under `~/Movies/Editor CLI Sessions/<session-id>/` by default and
use mode `0700`. Each session contains `state.json`, `journal.jsonl`, `source/`,
`assets/`, `candidates/`, `previews/`, and `evidence/`; each rendered pass keeps
its own FCPXML, preview, and evidence manifest. On restart, run `status`, then
`resume` only after reopening the captured project. The controller refuses to
replay an uncertain external action or proceed if the source export changed.

Native Share writes an atomic completion receipt with the exact output path,
project identity, size, and SHA-256. Restart recovery validates that receipt and
the exact candidate XML before it accepts an existing render. Undo makes the
restored checkpoint the next editing source and clears the superseded ready
result. Downloads use the same intent/receipt workflow and preserve provenance
across interruption; a retry does not replay uncertain network activity.

Timeline inspection returns structured clips, gaps, roles, markers, effects,
upstream transcript/pacing data when available, and installed Motion assets.
Title text is separate from speech. Register acquired session media through
`editor_media.register`, then refer to its asset ID in typed edit operations.
The controller rejects arbitrary source paths and unregistered asset IDs.

#### Measured automated verification

Run the full verification gates from the checkout:

```bash
uv run pytest -q
swift test --package-path native/final-cut-bridge
uvx ruff check src tests scripts
uvx ruff format --check src tests scripts
uv build
uv run python -m editor_cli.mcp_server --help
```

The `Check` GitHub workflow runs the Python and native tests, lint, distribution
build, and MCP entry-point check on macOS. A separate Python 3.10 job checks
the minimum supported version. Offline tests include generated-media
renders and simulated crash recovery. They do not prove live Final Cut control.

Local verification on 2026-09-07: 546 Python tests, 105 Swift tests, and 22
Python 3.10 setup/locking tests passed. Ruff lint/format, wheel/sdist builds,
and MCP startup checks passed. Code review covered interruption recovery,
media provenance and probing, and the optional-transcription voice-over path.

On the development Mac, the native helper is installed and direct rendering is
ready. Live checks against Final Cut Pro Creator Studio 12.3 confirm Accessibility
and Automation permission. The library-inspection probe now encodes the Apple
Event `all` selector as an absolute ordinal in host byte order; malformed
inspection replies no longer masquerade as permission denial. The MCP adapter
supports both v1 wire-alias fields and v2 snake-case result fields.

Creator Studio renamed and moved the mutation menus. A live read of the running
app's accessibility menu bar on 2026-09-07 gives the three exact titles the
bridge presses: `Edit > Duplicate Project As…` (Final Cut 11 kept it under
File), `File > Export XML…`, and `File > Share > Export File (default)…`.
All three carry the U+2026 ellipsis, not three periods, so the old ASCII
constants matched nothing and every mutation timed out. `FinalCutMenu` now holds
the live strings, and menu traversal walks direct children at each hop instead of
searching the whole subtree.

Browser navigation follows the same tree. The controller discloses the library
row, selects the event row, then presses the project tile that appears in the
browser. `inspect_active_project` reads the open timeline from the toolbar's
`projectNamePopUpButton`, reveals that project in the browser, and returns the
library, event, and project it lands on.

macOS keys the Automation grant to the terminal that launches the helper, not to
the helper binary, so a run from a different terminal prompts again. Approve
`editor-cli permissions request` there once and `editor-cli doctor` shows five
checks passing.

Three faults kept native navigation dark, and a live run on 2026-09-08 found all
three. Final Cut publishes its accessibility window tree only while it is the
active application, so a helper launched from a background terminal read an empty
`AXWindows` list and timed out on every traversal; each action now asks Final
Cut to activate and waits for it to become the active application. A menu command that does not apply to the open timeline
reads as disabled rather than missing, which surfaced as a role mismatch or a
bare timeout, so `disabledControl` now names it and a failed traversal closes the
menu it opened. Writing an empty `AXSelectedChildren` to the browser reports
success and clears nothing, so the reveal still deselects through
`Edit > Deselect All` with the browser focused.

`inspect_active_project` now answers from a background terminal in under three
seconds with the full identity: library, event, project, and duration.

Export XML reaches its panel. Creator Studio floats the save panel as its own
dialog window (`AXWindow` / `AXDialog`, identifier `save-panel`) instead of
attaching a sheet to the main window, so the controller accepts both shapes.

Two panel behaviors then needed decisions rather than patches, and both are
settled. A path written into the name field is saved as a literal filename
(`AXConfirm` on that field reports success and navigates nowhere), so the
controller opens the panel's Go to Folder sheet with a single `Cmd+Shift+G`
chord, writes the folder into its `PathTextField` over accessibility, and
refuses to continue until the `Where` popup shows that folder. That chord is
the entire keyboard surface; everything else stays accessibility-only. The XML
version popup offers 1.14, 1.13 and 1.12, and all three write a `.fcpxmld`
**bundle**, so the bundle is now the export artifact and the controller reads
`Info.fcpxml` inside it.

An open menu or save panel owns Final Cut's event stream, so a run that failed
and left one on screen blocked every later Apple Event, and `doctor` reported
that as `Final Cut Pro not running` with every permission failing. A failed
mutation now closes what it opened, and `doctor` says `unknown` rather than
claiming the app is dead when its own probe is what failed.

`File > Reveal Project in Browser` is disabled whenever no view holds focus,
which is where Final Cut sits after a click on empty timeline space or after a
run that never touched the browser. The reveal used to grab focus only on its
way to clearing a selection, so with an empty browser it pressed a disabled
command. It now focuses the browser first, every time, and proves the focus
landed before pressing.

Activation cannot be taken, only given. A run on 2026-09-10 measured every route
on macOS 26 against Creator Studio 12.3: `activate()`, `AXFrontmost`, `AXRaise`,
`AXMain` and `AXFocused` each report success and publish the window tree, and
Final Cut still reports `isActive` false with every File menu command disabled.
Gating on the window count alone therefore let an action walk into a disabled
command and spend its whole deadline there. The foreground gate now requires a
real window count and real activation, and `finalCutNotForeground` asks for Final
Cut to be brought to the front instead of reporting a bare timeout.

That message only reaches you because the helper now outlives its own deadline.
The adapter used to kill the subprocess on the same number it put in the
request, so the helper was cut off in the instant it was answering, and every
foreground refusal and disabled command arrived as `Native Final Cut action
timed out`. It now keeps `VERDICT_GRACE_SECONDS` past the action deadline to
serialize its verdict and exit. The live canary run on 2026-09-10 failed exactly
this way: focus moved to the terminal mid-run, and the real reason was thrown
away with the process.

The canary drives Final Cut through its real UI, so **Final Cut must stay the
frontmost application for the whole run**. Typing into another window takes
activation away and fails the run.

`open_project` proves its own selection instead of revealing. Final Cut disables
`Reveal Project in Browser` once the project is already showing, which is the
state that action creates for itself, so its postcondition now checks that the
tile it selected is the selected one and that the timeline holds that project.
The action navigated to the library row and the event row by exact name before
selecting, so the location is already proven. `inspect_active_project` cannot
take the same shortcut: the timeline it reports on was opened by someone else,
and the project name alone does not say which library it came from, which is
exactly what several identically named projects would get wrong.

**Still open.** Selecting a tile does not open the project into the timeline,
measured on 2026-09-10 across separate runs, so that postcondition is not
satisfiable yet for a project that is not already open. The tile publishes
`AXPress` and nothing else, `AXPress` selects nothing, `Reveal Project in
Browser` stays disabled, and `Open Clip` does not move the timeline. Opening a
selected project looks like it needs Return on the focused browser, which would
widen the keyboard surface from one chord to two; that is not implemented and
not decided.

Selecting a project in the browser is a write, not a press. `AXPress` on a
project tile returns success and selects nothing, exactly as the empty
`AXSelectedChildren` write returns success and clears nothing, so
`open_project` was selecting no project at all: measured on 2026-09-10 the
browser held focus with `selected_count=0`, no timeline opened, and every
command that needs a selection stayed disabled, which surfaced as a reveal
`disabledControl` and read like a menu fault. Writing `AXSelectedChildren`
non-empty is honoured, so the controller now writes the selection and polls
until Final Cut confirms it.

The canary also closes the library it opened. It used to create a disposable
library per run and never close it, so four `Editor CLI Canary *` libraries had
piled up in Final Cut next to real ones. `close_library` closes one library by
exact name, matched against the whole `Close Library “…”` menu title
so it cannot close a library nobody asked about, and the canary calls it in a
`finally` block. A library that is already gone counts as success, because that
path runs after failures too.

The live end-to-end export has not run since that fix: the screen was locked,
and a locked Mac publishes no accessibility windows at all. PR #18 stays draft
until that run passes. No real user footage has been edited.

See
[`docs/superpowers/specs/2026-09-05-native-final-cut-controller-design.md`](docs/superpowers/specs/2026-09-05-native-final-cut-controller-design.md)
for the approved architecture, access boundaries, recovery model, and live
Final Cut 12.3 acceptance test.

## Legacy workflow setup

```bash
uv sync --extra dev            # install deps + dev tools
export GEMINI_API_KEY=...      # or CLIQK_GEMINI_API_KEY
export ELEVENLABS_API_KEY=...  # https://elevenlabs.io/app/settings/api-keys
uv run pytest -q
```

## Usage

```bash
# Edit a folder of footage in the style of a reference video (local or YouTube URL):
uv run editor-cli edit ./footage \
    --prompt "punchy 30s launch teaser" \
    --ref https://youtu.be/SOME_ID \
    --ref ./refs/style.mp4 \
    --out edit/

# Learn the style from trending videos in a genre (auto-discovered):
uv run editor-cli edit ./footage --prompt "..." --genre "tech product launch reel" --trend-count 5

# Instagram/TikTok reference (reads your browser login cookies):
uv run editor-cli edit ./footage --prompt "..." \
    --ref "https://www.instagram.com/reel/SOME_ID/" --cookies-from-browser chrome

# Outputs: edit/final.mp4 (ffmpeg) and edit/timeline.fcpxml (import into FCP).
```

## Stack

- Python — orchestrator + FCPXML generation
- Gemini API — video understanding + style evaluation
- ElevenLabs — word-level transcription (reused from `video-use`)
- Final Cut Pro 12.3 — editing and preview rendering through the signed native
  Swift helper
- ffmpeg — preprocessing and fast preview renders

## References (vendored, gitignored)

- `references/video-use` — [browser-use/video-use](https://github.com/browser-use/video-use):
  headless transcript-driven editor. We borrow its transcription + EDL concepts.
  Note: it renders mp4 directly via ffmpeg and does **not** emit FCPXML — that
  bridge is new work here.
- `references/hyperframes` — [heygen-com/hyperframes](https://github.com/heygen-com/hyperframes):
  HTML→video overlay engine for optional motion graphics.

## Bundled tools (git submodule)

- `vendor/OpenMontage` — [calesthio/OpenMontage](https://github.com/calesthio/OpenMontage):
  agentic video-production system, included as a git submodule and invoked as a
  **separate tool/process**. Fetch it with `git submodule update --init`.

### Motion-graphics overlays (OpenMontage / HyperFrames)

editor-cli drives OpenMontage's HyperFrames engine **at arm's length** (a
subprocess — never imported, so the repo stays MIT) to render animated overlays
(titles, lower-thirds, audio-reactive captions), then composites them onto
footage with ffmpeg.

```bash
editor-cli motion-doctor                 # check the runtime (Node >= 22, ffmpeg, hyperframes)
npx hyperframes --version                # warm the hyperframes CLI on first use
editor-cli overlay clip.mp4 title.mov -o out.mp4 --x 40 --y 40 --start 1.0
```

The bridge lives in `editor_cli/render/overlays.py` (subprocess only) and the
compositor is `ffmpeg.overlay_onto`. Both are our own MIT code.

**Titles are applied automatically during `edit`** (from the EDL — no manual
step). Pick the engine with `--titles`:

```bash
editor-cli edit ./footage -p "..." --titles auto         # HyperFrames if warm, else Pillow (default)
editor-cli edit ./footage -p "..." --titles hyperframes  # force rich animated overlays
editor-cli edit ./footage -p "..." --titles pillow       # force the portable path
```

`hyperframes`/`auto` author a transparent HyperFrames composition, render it to
an alpha webm (`npx hyperframes render --format webm`), and composite it onto
the cut. `pillow` renders text PNGs + ffmpeg overlay and works without Node.

## License

`editor-cli`'s own code is [MIT](LICENSE) © Screddyice.

`vendor/OpenMontage` is a git submodule that **remains under its own
[AGPL-3.0](https://github.com/calesthio/OpenMontage/blob/main/LICENSE)** license.
It is bundled as a separate, independently-licensed program (mere aggregation)
and is **not** linked into or imported by editor-cli's code — so it does not
change editor-cli's MIT license. If you ever import OpenMontage as a library
rather than shelling out to it, AGPL's copyleft would extend to the combined
work; keep the boundary at the process level to stay MIT.
