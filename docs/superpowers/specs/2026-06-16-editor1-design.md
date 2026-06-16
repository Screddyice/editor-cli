# Editor1 — Design Spec

**Date:** 2026-06-16
**Status:** Approved (shape) → ready for implementation planning
**Owner:** Shawn (Screddyice)

## Overview

Editor1 is a **terminal CLI** that turns a folder of raw footage plus an editing
**prompt** and one or more **reference videos** (local files *or* URLs) into:

1. a finished **`.mp4`** (rendered headlessly via ffmpeg), and
2. an editable **Final Cut Pro timeline** (`.fcpxml`, FCP 12.2) referencing the
   same media.

Both outputs derive from a single edit-decision list (EDL), so they stay in
sync. FCP is for optional manual tweaking; the automated deliverable is the
ffmpeg render. The target editing **style** is learned by having Gemini *watch*
reference reels and (later) genre-trending reels from the web.

## Goals

- One command: footage + prompt + refs → `final.mp4` + `timeline.fcpxml`.
- Style transfer from reference videos the user provides (local or URL).
- Gemini is the visual brain: it watches references, footage, and the rendered
  output (video-use's model only reads transcripts; Gemini sees pixels).
- Self-correcting: render → Gemini eval → re-cut, capped passes.
- Reuse `references/video-use` for transcription, the EDL concept, and ffmpeg
  render/grade helpers. The **EDL→FCPXML bridge** and **Gemini integration** are
  the new work.

## Non-goals / explicit risk acknowledgements

- **FCP does not render the deliverable.** Getting an `.mp4` out of FCP is not
  reliably automatable; ffmpeg renders it. FCP is the editable-timeline output
  only. (Decision: ffmpeg render + FCPXML, both from one EDL.)
- **Instagram scraping is best-effort, not guaranteed.** IG blocks bots and
  needs logged-in cookies; it is ToS-gray and will break periodically. YouTube
  / TikTok via yt-dlp are far more reliable. Build YouTube-first.
- **Trending sound: analyze ≠ use.** We identify and characterize a reel's sound
  (energy/genre/bpm) and *match* it with a licensed/royalty-free track. We do
  not rip and re-publish copyrighted IG/TikTok audio — that is a licensing
  problem, not a technical one.

## Engine decision

**Gemini-centric.** Gemini is the only model in the stack that natively watches
video, so it does style analysis, footage analysis, cut reasoning, and output
evaluation. (A future upgrade may route cut-reasoning to Claude if quality
lags; the EDL contract makes that swap localized.)

## Architecture — pipeline stages

1. **Reference acquisition** (`acquire/`)
   - Local files pass through.
   - URLs: **yt-dlp** downloads the media (YouTube/TikTok solid; IG via cookies,
     Phase 3). **HyperCrawl** supplies discovery + page metadata (sound name,
     hashtags, engagement, embedded media URLs) — Phase 2.
2. **Genre trend research** (`acquire/hypercrawl.py`, Phase 2) — discover
   comparable reels in a genre, sample, download, analyze in aggregate.
3. **Style/trend analysis** (`analysis/gemini.py`) — Gemini watches refs → a
   structured **StyleProfile**.
4. **Footage analysis** (`analysis/`) — `ffprobe` manifest + ElevenLabs
   word-level transcription (reused from video-use) + Gemini watching footage.
5. **Edit reasoning** (`analysis/gemini.py`) → an **EDL** applying the
   StyleProfile to the user's footage + prompt.
6. **Render** (`render/`) — ffmpeg → `final.mp4` (+ fast `preview.mp4`); EDL →
   **FCPXML** → editable FCP timeline.
7. **Eval loop** (`pipeline/orchestrator.py`) — Gemini watches the render,
   scores vs prompt + StyleProfile, returns fixes → revise EDL → re-render.
   Capped (default 3 passes); flag remaining issues to the user.

## Data contracts

**StyleProfile** (JSON):
```
{
  "pacing": {"cuts_per_min": float, "avg_shot_len_s": float},
  "transitions": [str],            // hard cut, crossfade, whip, ...
  "automations": [str],            // auto-captions, zoom punches, b-roll inserts
  "color": {"description": str, "lut": str|null},
  "captions": {"style": str, "position": str, "font": str|null},
  "sound": {"name": str|null, "energy": str, "genre": str, "bpm": int|null},
  "vibe": str
}
```

**EDL** (JSON, extends video-use's `edl.json`):
```
{
  "fps": float, "resolution": [w, h],
  "segments": [
    {"src": path, "in": float, "out": float,
     "grade": str|null, "overlays": [..]|null}
  ],
  "titles": [..], "subtitles": bool, "music": {..}|null
}
```

**FCPXML mapping:** EDL segments → spine `asset-clip`s with `offset`/`duration`/
`start`; titles → `title` elements; grade → color `adjust-color`. Target the
FCPXML DTD version FCP 12.2 imports (confirm exact version at build, likely
1.11–1.13); validate generated files against the DTD and a real FCP import.

## CLI surface

```
editor1 edit <footage_dir> --prompt "..." [--ref <file|url> ...]
            [--genre "..."] [--out edit/] [--max-eval 3] [--preview]
            [--no-fcpxml]
editor1 fetch <url> -o <dir>            # acquire a reference (yt-dlp/HyperCrawl)
editor1 style <file|url> ...            # print StyleProfile JSON
editor1 transcribe <footage_dir>        # cache transcripts
editor1 eval <render.mp4> --profile <style.json> --prompt "..."
editor1 fcpxml <edl.json> -o <out.fcpxml>
editor1 config                          # show/validate keys & paths
```

## Module layout (DDD, per workspace pattern)

```
editor1/
  cli.py            # typer app, thin controllers
  config.py         # GEMINI/ELEVENLABS keys, paths
  pipeline/orchestrator.py
  acquire/{local,fetch,hypercrawl}.py
  analysis/{probe,transcribe,gemini}.py
  domain/{style_profile,edl}.py
  render/{ffmpeg,fcpxml}.py
  prompts/          # Gemini prompt templates
tests/
pyproject.toml      # [project.scripts] editor1 = "editor1.cli:app"
```

## Tech stack

- Python ≥3.10, `uv`-managed; `typer` for the CLI.
- Gemini API (`CLIQK_GEMINI_API_KEY` for now; dedicated key later).
- ElevenLabs (`ELEVENLABS_API_KEY`) for transcription.
- ffmpeg/ffprobe (installed); yt-dlp (Phase 1 for YouTube).
- HyperCrawl MCP (Phase 2 discovery + IG cookie sync).

## Error handling

- Missing API keys → explicit, actionable error before any network call.
- yt-dlp failures → report source + reason; continue with remaining refs.
- Gemini responses validated against the StyleProfile/EDL schemas with one
  corrective retry; hard fail with the raw response on second failure.
- Eval loop capped; on cap, surface remaining issues rather than looping.

## Testing

- Unit: EDL→FCPXML against **golden `.fcpxml` fixtures** + DTD validation;
  StyleProfile/EDL schema round-trips; CLI arg parsing.
- Integration: a few-second sample clip end-to-end → `mp4` + `fcpxml`
  (Gemini/ElevenLabs mocked); one optional live smoke test behind a flag.
- Manual gate: import a generated `.fcpxml` into FCP 12.2 and confirm the
  timeline opens with media linked.

## Build phases

- **Phase 1 (now):** local + YouTube refs → Gemini style → footage analysis →
  EDL → ffmpeg `mp4` + FCPXML + eval loop. The full spine, end-to-end.
- **Phase 2:** HyperCrawl genre discovery + trending-sound metadata.
- **Phase 3:** Instagram/TikTok cookie auth + scraping hardening.

## Open items to confirm during planning

- Exact FCPXML DTD version for FCP 12.2 (verify against the installed app).
- Whether footage analysis sends full clips to Gemini or sampled
  filmstrips/segments (cost vs fidelity).
