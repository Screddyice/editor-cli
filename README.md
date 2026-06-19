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

**Phases 1–3 built + unit-tested (52 tests).**
- **Phase 1 — spine:** acquire → Gemini style → transcribe/probe → reason EDL →
  ffmpeg mp4 + FCPXML → Gemini eval loop. EDL→FCPXML validated against FCP 12.2's
  own **v1.14 DTD**.
- **Phase 2 — discovery:** `--genre "<query>"` finds trending comparable videos
  (yt-dlp search), extracts sound/title metadata, feeds them as extra Gemini
  references + trend context.
- **Phase 3 — social:** Instagram/TikTok reference URLs via yt-dlp cookie auth
  (`--cookies-from-browser` / `--cookies`), retry hardening, actionable errors.

Two gates remain before a live run:
1. **API keys required** — set `GEMINI_API_KEY` (or `CLIQK_GEMINI_API_KEY`) and
   `ELEVENLABS_API_KEY`. They are currently empty in `~/projects/.env`.
2. **Manual FCP import** — import a generated `timeline.fcpxml` into FCP 12.2
   once to confirm it opens with media linked (DTD-valid, GUI-import pending).

## Setup

```bash
uv sync --extra dev            # install deps + dev tools
export GEMINI_API_KEY=...      # or CLIQK_GEMINI_API_KEY
export ELEVENLABS_API_KEY=...  # https://elevenlabs.io/app/settings/api-keys
uv run pytest -q               # 52 passing
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

## Stack (proposed — not yet finalized)

- Python — orchestrator + FCPXML generation
- Gemini API — video understanding + style evaluation
- ElevenLabs — word-level transcription (reused from `video-use`)
- Final Cut Pro 12.2 — editing + render, via FCPXML import / export
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
