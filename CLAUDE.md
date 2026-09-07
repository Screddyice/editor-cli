# CLAUDE.md — editor-cli

AI-assisted video editing. Use the first-class direct workflow for selected files:

- **Direct sessions** (`src/editor_cli/direct/`) use Codex or Claude as the
  creative editor and FFmpeg to render final MP4. Invoke
  `src/editor_cli/resources/skills/direct-video-editor/SKILL.md` for selected-file
  edits. Use `editor_direct` or `editor-cli direct`; no Final Cut or Gemini
  dependency. Never substitute directory-scanning legacy helpers. All outputs
  belong in the returned `edit/<session-id>/`. One strategy approval permits
  preview correction and final export; review the final video before finishing.

The older engines remain available:

- **editor-cli** (this project's `src/`) — Final Cut Pro is the editing engine, Gemini is the visual brain, deliverable is FCPXML → `.mp4`.
- **video-use** (`vendor/video-use/`) — a conversation-driven, agent-native editor: transcribe → cut → grade → subtitle → overlay → render `.mp4` entirely from the command line (no FCP round-trip).

## video-use skill — auto-invoke for editing requests

A reference skill lives at **`.claude/skills/video-use/`** (a symlink to `vendor/video-use/`, so `SKILL.md` and `helpers/` stay siblings). Use its cut and composition guidance; route selected-file requests through the direct-session skill above. Examples:

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

## Final Cut controller — auto-invoke for an open Final Cut project

Use `skills/final-cut-editor/SKILL.md` when the user asks to change a project
that is open in Final Cut Pro. The skill calls the grouped `editor-cli` MCP
surface, preserves the source project, renders and watches each candidate, and
leaves the selected working project open. Do not substitute raw CommandPost
commands, AppleScript, or keystrokes.

Start with `editor_session` using `action: doctor`. Stop when the report is not
ready. The current native controller uses the project-owned signed Swift helper
and does not require a paid LateNite app or CommandPost. Do not claim a live
canary result or dual-host comparison until the disposable canary completes.

Treat branch delivery and CI as separate evidence. Do not call PR #18 delivered
or its checks green until the branch is pushed and GitHub reports a completed
check run.

Use only active-project references, installed Final Cut or Motion assets, and
the session directory. Route public media through `editor_media.acquire`; it
records provenance and accepts public HTTPS media only. The editor performs the
final export in Final Cut Pro.

### Environment (already wired)

- **Keys:** direct editing uses `ELEVENLABS_API_KEY` for Scribe speech transcription; check `direct doctor`, do not assume a key exists. `EDITOR_CLI_ENV_FILE` selects an exact credential file. Legacy Gemini workflows also need `GEMINI_API_KEY`. Never echo or commit keys.
- **Voice-over:** direct plans accept one finite, non-looping selected audio asset.
  Source audio and narration do not require transcription. Transcribe for
  word-aware cuts or captions, and never claim word precision without timestamps.
- **ffmpeg / ffprobe:** required, present on this machine.
- **yt-dlp:** optional, only for pulling sources from URLs (in editor-cli's deps).
- **Animation engines** (HyperFrames / Remotion / Manim): installed lazily per animation slot — don't install globally. `manim` is an optional extra of video-use; the `manim-video` sub-skill is at `vendor/video-use/skills/manim-video/`.

## vendor/ provenance

`vendor/video-use/` is a **copied-in MIT snapshot** of `browser-use/video-use` (not a submodule). See `vendor/video-use/VENDOR.md` for the upstream commit and re-sync procedure. It's ours to adapt; if you want clean upstream merges later, keep local edits documented.

`vendor/OpenMontage/` is a git submodule (reference only).

## Tests

`pytest` is scoped to `tests/` and excludes `vendor/`, `.venv/`, `references/` (`norecursedirs`). video-use's own tests are not collected.
