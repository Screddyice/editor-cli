# CLAUDE.md — editor-cli

AI-assisted video editing. Two engines live here:

- **editor-cli** (this project's `src/`) — Final Cut Pro is the editing engine, Gemini is the visual brain, deliverable is FCPXML → `.mp4`.
- **video-use** (`vendor/video-use/`) — a conversation-driven, agent-native editor: transcribe → cut → grade → subtitle → overlay → render `.mp4` entirely from the command line (no FCP round-trip).

## video-use skill — auto-invoke for editing requests

A registered skill lives at **`.claude/skills/video-use/`** (a symlink to `vendor/video-use/`, so `SKILL.md` and `helpers/` stay siblings). **Invoke it automatically — without being asked — whenever the user wants to edit video in this project**, e.g.:

- "edit these takes into a launch video", "cut this down", "make a reel from this footage"
- remove filler words / dead space, color grade, burn subtitles, add an overlay animation
- "inventory these clips and propose an edit strategy"

When invoked, read `.claude/skills/video-use/SKILL.md` and follow it. It is the source of truth for the editing flow (ask → confirm → execute → iterate → persist). All outputs land in `<videos_dir>/edit/` — never edit source files in place.

### Running the helpers

video-use's helpers have their own dependencies in a dedicated venv. Run them with that interpreter (helpers self-locate via `__file__`, so cwd doesn't matter):

```bash
vendor/video-use/.venv/bin/python vendor/video-use/helpers/<name>.py ...
# e.g.
vendor/video-use/.venv/bin/python vendor/video-use/helpers/timeline_view.py <video> <start> <end>
vendor/video-use/.venv/bin/python vendor/video-use/helpers/transcribe.py <video>
vendor/video-use/.venv/bin/python vendor/video-use/helpers/render.py <edl.json> -o final.mp4 --build-subtitles
```

Do **not** rewrite these as bare `python helpers/x.py` — that uses the wrong interpreter and the imports (librosa/numpy/matplotlib/requests) will fail.

### Environment (already wired)

- **Keys:** `ELEVENLABS_API_KEY` (Scribe transcription) and `GEMINI_API_KEY` are in `editor-cli/.env`. `vendor/video-use/.env` is a symlink to it, so the helpers' key lookup resolves with no duplication. Never echo or commit keys.
- **ffmpeg / ffprobe:** required, present on this machine.
- **yt-dlp:** optional, only for pulling sources from URLs (in editor-cli's deps).
- **Animation engines** (HyperFrames / Remotion / Manim): installed lazily per animation slot — don't install globally. `manim` is an optional extra of video-use; the `manim-video` sub-skill is at `vendor/video-use/skills/manim-video/`.

## vendor/ provenance

`vendor/video-use/` is a **copied-in MIT snapshot** of `browser-use/video-use` (not a submodule). See `vendor/video-use/VENDOR.md` for the upstream commit and re-sync procedure. It's ours to adapt; if you want clean upstream merges later, keep local edits documented.

`vendor/OpenMontage/` is a git submodule (reference only).

## Tests

`pytest` is scoped to `tests/` and excludes `vendor/`, `.venv/`, `references/` (`norecursedirs`). video-use's own tests are not collected.
