# Editor CLI

AI-assisted video editing where **Final Cut Pro is the editing engine** and the
intelligence comes from an LLM orchestrator plus **Gemini's native video
understanding**.

## Goal

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

The deliverable is always an `.mp4`. Final Cut Pro stays the editor, so every
decision remains hand-tweakable — not locked inside a flattened render.

## Status

**Phases 1–3 are built; the full repository suite reports 240 passed on
2026-09-05.**

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
   `ELEVENLABS_API_KEY`. They are currently empty in `~/projects/.env`.
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

When macOS prompts, approve CommandPost's Automation access to Final Cut Pro
and its Accessibility access for UI control. Keep CommandPost's WebSocket
bridge bound to `127.0.0.1` or `::1`. The doctor refuses to start sessions
until Final Cut, CommandPost, an eligible LateNite license app, the loopback
bridge, and the shared `watch` skill are present.

`editor-cli setup` installs the pinned CommandPost release when it is absent,
installs the shared watch skill when needed, links the Final Cut skill and
CommandPost bridge, writes the two MCP registrations with backups, and verifies
that the MCP server lists tools. It does not start or validate the CommandPost
listener. Configure the listener and macOS permissions, then use
`editor-cli doctor` for readiness.

Measured on 2026-09-05: Final Cut Pro 12.3 (build 450152), CommandPost 2.1.0,
and `watch` 0.2.0 are installed for Codex and Claude Code. Device readiness is
**false** because no eligible LateNite license app is installed and no
CommandPost listener exists on port 27480. The disposable live canary and the
fresh Claude Code/Codex evidence-manifest comparison remain pending until both
prerequisites are resolved.

#### Session workflow and recovery

```bash
uv run editor-cli doctor
uv run editor-cli edit-active "remove pauses and add a restrained title"
uv run editor-cli session status <session-id>
uv run editor-cli session resume <session-id>
```

Sessions live under `~/Movies/Editor CLI Sessions/<session-id>/` by default and
use mode `0700`. Each session contains `state.json`, `journal.jsonl`, `source/`,
`assets/`, `candidates/`, `previews/`, and `evidence/`; each rendered pass keeps
its own FCPXML, preview, and evidence manifest. On restart, run `status`, then
`resume` only after reopening the captured project. The controller refuses to
replay an uncertain external action or proceed if the source export changed.

#### Measured automated verification

On 2026-09-05, `uv run pytest -q` reported **240 passed** and
`uv build` produced the source distribution and wheel. A bounded stdio MCP
probe initialized `editor-cli`, listed all four grouped tools, and returned the
same not-ready doctor report. The canary preflight exited before it created a
workspace: `Canary failed: Run editor-cli doctor and resolve failed checks
first`. No preview hash or live evidence manifest exists yet.

PR delivery remains pending until this branch is pushed. PR #18 has no
configured check run in the latest remote state, so this document makes no
green-check claim.

See
[`docs/superpowers/specs/2026-09-05-final-cut-closed-loop-controller-design.md`](docs/superpowers/specs/2026-09-05-final-cut-closed-loop-controller-design.md)
for the approved architecture, access boundaries, recovery model, and live
Final Cut 12.3 acceptance test.

## Setup

```bash
uv sync --extra dev            # install deps + dev tools
export GEMINI_API_KEY=...      # or CLIQK_GEMINI_API_KEY
export ELEVENLABS_API_KEY=...  # https://elevenlabs.io/app/settings/api-keys
uv run pytest -q               # 240 passed on 2026-09-05
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
- Final Cut Pro 12.3 — editing + preview render through the loopback-only
  CommandPost bridge
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
