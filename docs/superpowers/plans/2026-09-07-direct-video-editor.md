# Direct Video Editor Implementation Plan

**Goal:** Let Codex edit selected footage into a reviewed final MP4 without Final Cut.

**Architecture:** A direct-session service owns selected inputs, transcripts,
strategy approval, render attempts, and review evidence. The FFmpeg adapter
consumes validated asset IDs and a structured plan. CLI and MCP use the same
service; the installed skill guides creative decisions and media inspection.

**Tech Stack:** Python, Pydantic, FFmpeg/ffprobe, Pillow, existing MCP server.

**Spec:** `docs/superpowers/specs/2026-09-07-direct-video-editor-design.md`

## Global constraints

- Exact selected files only; all outputs under `edit/<session-id>/`.
- One strategy approval, at most three preview passes, reviewed final export.
- No paid application dependency. No Final Cut actions in the direct workflow.
- Preserve the existing dirty Final Cut recovery changes.

## Task 1: Direct renderer

Create `src/editor_cli/direct/render.py` and `tests/direct/test_render.py`.
Interface: `render(plan: dict, assets: dict, transcripts: dict, work_dir: Path,
preview: bool = False) -> Path`. Plans use the models in `direct/models.py`;
assets map IDs to snapshot paths and metadata. Render a fresh `render.mp4`.

- [x] Build generated-media tests covering ordered mixed audio/silent sources,
  target dimensions, title/caption/overlay timing, speed, and music duration.
- [x] Implement extract, normalized concat, composition, and subtitle generation.
- [x] Verify the produced video using ffprobe and decoded frames.

## Task 2: Session and context

Create `direct/models.py`, `direct/session.py`, `direct/media.py`, and tests.
Interface: `DirectSession.start(files, prompt) -> dict` and
`dispatch(action, session_dir, **kwargs) -> dict`.

- [x] Validate selection and snapshot each exact source without directory scans.
- [x] Persist source IDs, hashes, metadata, context, and cached word transcripts.
- [x] Store approved strategy and validate cut ranges against source/transcripts.
- [x] Render, produce review artifacts, verify review coverage and hashes, export,
  and finish. Exercise stale reviews, source mutation, restart, and pass limits.
- [x] Add bounded direct HTTPS downloads with per-hop public-IP pinning.

## Task 3: Agent integration and verification

Modify `cli.py`, `mcp_server.py`, package metadata, README, and CLAUDE guidance;
add the packaged `direct-video-editor` skill and focused integration tests.

- [x] Expose direct CLI and MCP calls without constructing Final Cut services.
- [x] Register the skill through a direct-only setup command for both hosts.
- [x] Run the generated-footage canary and inspect output images and decoded audio evidence.
- [x] Review the feature, fix defects, commit only direct-workflow files, push
  the existing draft PR, and report exact validation and remaining acceptance.

## Verification record

On 2026-09-07, the direct workflow, MCP, and package tests passed: 58 tests.
The generated-media canary completed preview, review, export, final review, and
delivery. Rendered frames were inspected; audio checks used decoded PCM and
synthetic fixtures, not claimed listening. The caption-timing oracle passed on
the implemented function. The verifier council returned a verified GPT-5.6 Sol
candidate after its local attempt and unavailable OpenRouter credit.

A clean checkout with its own environment and the existing OpenMontage reference
passed 478 tests and failed four unchanged native Final Cut canary tests at the
preserved-project identity check. Those tests and native recovery work remain
outside this direct-workflow change. The original checkout's pre-existing dirty
Final Cut files remain intact, including future tests awaiting implementation.

Both host skills are installed. Direct doctor confirms FFmpeg/ffprobe readiness;
speech transcription is not configured because no ElevenLabs key was found.
No real user footage has been selected for creative acceptance. PR #18 remains
draft, with no merge or live Final Cut acceptance claim.
