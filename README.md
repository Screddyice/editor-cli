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

## License

[MIT](LICENSE) © Screddyice
