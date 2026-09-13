# Codex direct video editing

The user approved proceeding with one strategy approval followed by editing,
preview inspection, correction, and final export. Codex is the creative editor.
Final Cut remains a separate optional workflow.

## Session and input contract

`editor-cli direct start --file PATH ... --prompt TEXT` selects exact files. It
does not enumerate their directories. Sources are hashed and copied into a fresh
`edit/<session-id>/` beside the first selected file. All media processing uses
these snapshots; originals remain untouched. Inputs may be videos, stills,
audio, or UTF-8 context documents. Reject symlinks, media playlists, directories,
unknown types, and non-finite timings. Edits refer to asset IDs, never paths.

The persisted session includes the prompt, source inventory, source hashes,
transcripts, strategy and approval, cut decisions, render hashes, and reviews.
Atomic state writes and an exclusive process lock allow retry after interruption.
Each render writes to a fresh attempt directory. No completed review carries to
changed media, changed plans, or a new render.

## Agent workflow

Codex reads the selected context, probes media, requests cached verbatim word
transcripts when captions or word-aware cuts need them, and inspects sampled source frames. It proposes the story, pacing,
format, captions, and optional music or meme inserts once. After the user's
approval, it submits structured edit decisions and runs up to three preview
passes. It can inspect more source windows whenever the narrative needs them.

Rendering supports ordered cuts, still images, silent footage, speed changes,
simple color adjustment, titles, overlays, captions, one finite non-looping
narration track, and a music bed. Narration mixes with footage audio and cannot
extend the video. Timestamped speech edges must avoid words and preserve 30–200
ms padding where silence permits. Captions require transcripts for each audible asset.
Audio receives 30 ms edge fades. Normalize video and audio before concat.
Shift overlay timestamps and apply captions last. Preserve chosen aspect ratio
with letterboxing. Avoid arbitrary FFmpeg expressions in the public interface.

Each preview and final render produces technical checks plus review windows at
every cut, overlay/title boundary, beginning, end, and regular midpoint samples.
Codex must view the images and audio waveforms, read decoded PCM measurements
and transcript timings, and use listening/video perception when available. It
identifies the evidence behind each observation and leaves unresolved perceptual
checks pending. It records observations for each window bound to the render
hash. Export requires a passing preview review; completion requires a passing
final review and unchanged original sources. Review reports are agent
attestations, not independent proof that the model perceived the media.

## Internet media

Public HTTPS acquisition may add session assets, with provenance and bounded
downloads. The direct workflow uses direct public media URLs with a pinned
public IP and checks each redirect. It reads no browser cookies or local
libraries. Website extraction is outside the first direct adapter.

## Interfaces and acceptance

A grouped `editor_direct` MCP tool and `direct` CLI share one service. They work
without the native Final Cut helper or Gemini credentials. Package an editing
skill for both Codex and Claude Code. Existing Final Cut configuration remains
compatible.

Verify deterministic contracts, selected-file isolation, transcript caching,
review/hash invalidation, retry and pass limits, and an offline FFmpeg canary
with generated footage. A real user vlog acceptance requires selected footage;
synthetic checks do not establish creative quality on the user's material.
