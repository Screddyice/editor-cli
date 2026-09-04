# Final Cut Closed-Loop Controller Design

**Date:** 2026-09-05  
**Status:** Approved for implementation planning  
**Owner:** Shawn  
**Target:** Final Cut Pro 12.3 on Apple silicon

## Summary

Editor CLI will become a local controller for an editor who works in Final Cut
Pro. The editor opens a library, selects a project, and makes any initial cuts.
Claude Code or Codex then receives a natural-language edit request and runs a
closed loop:

1. capture the active project;
2. preserve the source as a timestamped version;
3. inspect the FCPXML and a Final Cut preview;
4. apply the complete request to a working version;
5. render that version through Final Cut;
6. watch the result and correct defects; and
7. leave the best verified timeline open for the editor.

The editor reviews the timeline and performs the final export. Editor CLI may
create temporary preview renders, but it will not export or publish the final
master.

## Decisions

- Final Cut Pro remains the editing and preview-rendering engine.
- Every edit starts from a duplicate. The source project stays untouched.
- The agent applies the full requested edit without a second approval prompt.
- The agent must watch its rendered result before it reports completion.
- The closed loop stops after three candidate passes.
- Claude Code and Codex share the same timeline and video evidence artifacts.
- The agent may use media already referenced by the active project, installed
  Final Cut assets, and files in the current Editor CLI session folder.
- The agent may acquire media from public internet sources and must record the
  source URL for each downloaded asset.
- The agent must not search unrelated local folders, Photos, other Final Cut
  libraries, attached drives, or cloud-sync folders.
- The editor performs the final delivery export in Final Cut Pro.

## Goals

- Let an editor request timeline changes in plain language from Claude Code or
  Codex.
- Support structural edits such as gap removal, trims, splits, reordering,
  silence removal, captions, markers, and role changes.
- Support creative edits such as punch-ins, reaction inserts, meme clips,
  sound effects, titles, transitions, generators, and installed effects.
- Ground edits in the active timeline, rendered pictures, speech, music, and
  timing.
- Verify the result from a Final Cut render after each candidate pass.
- Preserve a complete operation journal and source provenance for downloaded
  assets.
- Work on this Mac with Final Cut Pro 12.3, `uv`, FFmpeg, and Apple silicon.

## Non-goals

- Editor CLI will not replace Final Cut's timeline interface.
- Editor CLI will not perform the final share, upload, or publication step.
- Editor CLI will not patch or modify the Final Cut application binary.
- Editor CLI will not index the device, Photos library, unrelated Final Cut
  libraries, or external drives.
- Editor CLI will not bypass DRM, paywalls, authentication, or site controls to
  obtain internet media.
- The first release will not automate color-critical grading decisions or a
  final audio mix without an explicit edit request.

## Device baseline

The design review measured this Mac before implementation:

- MacBook Pro `Mac17,7` with an Apple M5 Max and 36 GB of memory;
- macOS 26.5.1;
- Final Cut Pro Creator Studio 12.3, build 450152;
- `uv` 0.11.8;
- FFmpeg 8.1.1;
- Python 3.14.7;
- Node.js 26.4.0; and
- CommandPost not installed.

Implementation must repeat these probes. The values above record the design
baseline and do not replace the setup doctor.

## Architecture

```text
Claude Code / Codex
        |
        v
Editor CLI session controller
        |
        +--> Final Cut adapter ------> CommandPost ------> Final Cut Pro 12.3
        |          |
        |          +---------------> FCPXML MCP
        |
        +--> perception adapter ----> claude-video /watch evidence bundle
        |
        +--> media acquisition -----> public web sources -> session assets
        |
        +--> verification runner ---> FFmpeg checks + visual review rubric
```

Editor CLI owns the workflow and state. Third-party tools sit behind adapters
so the project can pin versions, test contracts, and replace one integration
without changing the rest of the system.

### Session controller

The session controller creates one workspace per edit request and advances a
persisted state machine. It never treats a successful command, XML write, or
Final Cut import as proof that the edit looks correct.

States:

```text
IDLE -> CAPTURE -> PRESERVE -> ANALYZE -> APPLY -> IMPORT
     -> PREVIEW -> VERIFY -> READY
                         \-> CORRECT -> IMPORT
                         \-> BLOCKED
```

Each transition records its inputs, outputs, timestamps, tool version, and
result. A process restart resumes from the last completed transition. It does
not repeat an uncertain import, menu action, or media download.

### Final Cut adapter

The Final Cut adapter presents a small internal interface:

- locate the running Final Cut instance;
- identify the active library, event, and project;
- duplicate or preserve the active project;
- export the active project as FCPXML;
- import a candidate FCPXML as a new working project;
- open the imported project;
- discover installed titles, effects, transitions, generators, and sound
  effects;
- render a temporary preview to the session folder; and
- report blocking dialogs, missing media, and automation failures.

CommandPost drives the Final Cut UI for active-project selection, menu actions,
project duplication, XML export, opening a working project, installed-asset
discovery, and preview sharing. Editor CLI will connect to CommandPost 2.1's
built-in WebSocket control surface through a narrow command allowlist. The
setup doctor must prove that the listener binds only to loopback before it
sends a command. Editor CLI will not expose a general Lua or keystroke
execution endpoint to agents.

FCPXML MCP parses, validates, edits, journals, diffs, and imports timeline XML.
It provides rational time arithmetic and the timeline operations that the
current `render/fcpxml.py` generator lacks. Editor CLI will pin and wrap the
dependency rather than exposing its full tool catalog to every agent session.

Apple does not provide a complete programmatic export API for Final Cut. The
adapter therefore treats CommandPost's UI-driven export as a capability that
must pass a live Final Cut 12.3 canary. If that canary fails, the session stops
at `CAPTURE` and gives the editor one specific manual export action. It does not
guess which timeline it captured.

### Perception adapter

The project will install `bradautomates/claude-video` as the shared `watch`
skill for Claude Code and Codex. The adapter writes a durable evidence bundle
for each preview:

```text
evidence/pass-01/
  manifest.json
  transcript.vtt
  frames/
  technical.json
  review.json
```

The manifest contains preview identity, source project identity, duration,
frame rate, frame timestamps, and the edit regions that changed. Claude Code
and Codex can inspect the same bundle without rerunning extraction.

The first review uses scene-aware balanced sampling. Follow-up reviews focus on
the time ranges changed in the latest pass. This keeps the evidence dense
around jokes, cuts, captions, and transitions while controlling image-token
cost.

### Media acquisition

The acquisition service receives a structured request such as "short confused
reaction," "record scratch," or "dramatic zoom sound." It searches public web
sources, inspects candidate metadata, and downloads selected assets into the
current session.

Every acquired asset records:

- original URL;
- retrieval time;
- displayed author or publisher when available;
- license or usage note when the source provides one;
- content hash;
- local session path; and
- the timeline operations that use it.

The service rejects executable files, DRM workflows, paywall bypasses, and
requests that require a new account login. It prefers reusable stock, public
domain, Creative Commons, and creator-provided downloads. The editor remains
responsible for the final rights decision before publication.

### Verification runner

The verification runner uses two forms of evidence.

Technical checks use FFmpeg and FCPXML inspection to detect:

- an unreadable or missing preview;
- duration or frame-rate mismatch;
- offline media and black frames;
- unexpected silence or clipped audio;
- gaps, flash frames, and invalid time ranges; and
- missing titles, transitions, or imported resources.

Creative checks use the rendered preview, transcript, changed-region map, and
the user's request to verify:

- each requested edit appears in the output;
- joke setup and payoff timing remain understandable;
- captions stay legible and synchronized;
- inserted media matches the intended moment;
- transitions and effects do not hide important content; and
- unchanged regions did not regress.

The verifier returns evidence for each pass, not one opaque score. The session
controller may attempt two corrections after the first candidate. If no pass
meets all required checks, it leaves the strongest candidate open, marks the
session `BLOCKED`, and names the failed checks. It does not call the edit done.

## Access boundaries

Editor CLI uses an explicit filesystem allowlist for each session.

Allowed:

- FCPXML references already present in the active project's export;
- the active Final Cut library selected for the session;
- Final Cut and Motion asset catalogs needed to enumerate installed titles,
  effects, transitions, generators, and sound effects;
- the Editor CLI session root;
- temporary files created under that session root; and
- executables resolved from the configured toolchain allowlist.

Denied:

- recursive searches from the home directory or disk root;
- Documents, Desktop, Downloads, Photos, Music, or Movies outside the active
  project's existing references and the session root;
- other Final Cut libraries;
- removable and network volumes unless the active project already references a
  file there; and
- browser profiles, credential stores, and cloud-sync directories.

An active project may reference media stored elsewhere on the device. Editor
CLI may read those exact paths because Final Cut already attached them to the
project. It may not enumerate their parent folders or discover neighboring
media.

## Versioning and recovery

The controller names projects and artifacts with stable session and pass IDs.

```text
Original project:  Travel Vlog
Preserved version: Travel Vlog - Before AI - 2026-09-05 14-32
Working versions:  Travel Vlog - AI - pass 01
                   Travel Vlog - AI - pass 02
Final candidate:   Travel Vlog - AI - verified
```

The controller never overwrites the original FCPXML. Candidate XML files use
new paths. The journal stores every XML diff and action in execution order.
Undo creates another project version from the prior accepted XML; it does not
destructively rewrite the library.

Session layout:

```text
Editor CLI Sessions/<session-id>/
  request.json
  source/
  assets/
  candidates/
  previews/
  evidence/
  journal.jsonl
  result.json
```

## Agent interface

Claude Code and Codex receive a grouped MCP surface instead of direct access to
CommandPost and all FCPXML operations:

- `editor_session`: doctor, start, status, resume, finish;
- `editor_timeline`: inspect, apply an edit program, diff, undo;
- `editor_media`: search, inspect, acquire, list provenance;
- `editor_verify`: render preview, watch, inspect a time range, compare passes.

`editor_timeline.apply` accepts a typed edit program. The controller validates
the full program before it runs any operation. This prevents half-applied edits
when one instruction is invalid.

The CLI exposes the same controller for direct diagnosis:

```text
editor-cli doctor
editor-cli session start
editor-cli edit-active --prompt "remove gaps and add two restrained meme beats"
editor-cli session status
editor-cli session resume <session-id>
```

## User workflow

1. The editor opens Final Cut Pro and selects the intended project.
2. The editor makes any manual cuts.
3. The editor asks Claude Code or Codex for a complete edit.
4. Editor CLI shows the captured library, event, and project identity before it
   starts the first mutation.
5. The controller preserves the source and completes up to three closed-loop
   passes.
6. The controller leaves the best verified working project open in Final Cut.
7. The editor reviews the timeline and performs the final export.

The captured-project identity display is informational. The user approved full
execution in advance, so the controller does not pause for another edit-plan
approval.

## Error handling

- **Final Cut is closed:** stop before session creation and ask the editor to
  open it.
- **No active project:** report the active library and event, then ask the
  editor to select a project.
- **Automation permission missing:** identify the exact macOS permission and
  stop before mutation.
- **CommandPost unavailable:** fail the live-control doctor check. Do not use
  blind AppleScript or raw keystroke automation as an untracked fallback.
- **Blocking Final Cut dialog:** capture the dialog title, stop the action, and
  preserve the current state for resume.
- **Ambiguous export:** reject it unless project identity and timeline duration
  match the captured source.
- **Missing media:** stop candidate import and list the unresolved resource
  paths.
- **Internet source unavailable:** try another candidate that satisfies the
  same media request. Omit the optional asset only when the edit program marks
  it optional.
- **Preview render failure:** retain the candidate and diagnostics, but do not
  verify it from XML alone.
- **Agent or process restart:** resume from the journal and avoid replaying any
  uncertain write.
- **Three failed passes:** leave the strongest candidate open and mark the
  session blocked with timestamped evidence.

## Installation and configuration

The first implementation target pins these reviewed upstream releases:

| Dependency | Release | Purpose |
|---|---:|---|
| `DareDev256/fcp-mcp-server` | 0.22.1 | FCPXML parsing, edits, journal, preview, and import |
| `bradautomates/claude-video` | 0.2.0 | Shared Claude Code and Codex video perception |
| `CommandPost/CommandPost` | 2.1.0 | Final Cut UI control and installed-asset access |

All three projects use the MIT license. The setup path verifies release hashes,
the CommandPost code signature, a loopback-only WebSocket listener, and adapter
contract tests before enabling live control. Dependency upgrades require the
same checks and the live canary.

The implementation will provide an idempotent setup command that:

1. checks Final Cut Pro 12.3, FFmpeg, `uv`, Python, and Node.js;
2. installs the pinned, signed CommandPost release when it is absent;
3. installs and pins the FCPXML MCP dependency in the project environment;
4. installs the `watch` skill for Claude Code and Codex from
   `bradautomates/claude-video`;
5. enables CommandPost's built-in WebSocket control surface on loopback;
6. configures the Editor CLI MCP server for both agent hosts;
7. creates the session root with restrictive permissions; and
8. reports the macOS Automation and Accessibility permissions that need user
   approval.

The setup command creates timestamped backups before it changes an existing
Claude Code, Codex, or CommandPost configuration. Repeated runs produce the
same effective configuration.

## Testing

### Unit tests

- state-machine transitions and restart recovery;
- filesystem allowlist and exact-reference access;
- typed edit-program validation and atomic rejection;
- project naming and source preservation;
- XML diff, journal, and undo behavior;
- media provenance and content hashing;
- verification rubric aggregation; and
- config backup and idempotent setup.

### Contract tests

- pinned FCPXML MCP tool schemas and representative responses;
- `watch` evidence-manifest parsing;
- CommandPost loopback binding and command allowlist;
- installed-asset catalog normalization; and
- preview-render completion and identity matching.

### Integration tests

- synthetic FCPXML with gaps, titles, connected clips, roles, and fractional
  frame rates;
- web media acquisition into a temporary session root;
- candidate import and source-media relinking;
- preview extraction, transcript generation, and focused review; and
- forced failures at each state transition followed by resume.

### Live Final Cut 12.3 canary

The canary uses generated color cards, tones, and captions. It does not touch a
real library.

1. Open a disposable canary library and project.
2. Capture its identity and export its XML.
3. Preserve the source project.
4. Remove a known gap, add a title, add a transition, and insert a generated
   reaction card.
5. Import and open the candidate.
6. Render a temporary preview through Final Cut.
7. Watch the preview and verify every expected time range.
8. Confirm the source project and media remain unchanged.
9. Restart the controller during a second run and verify journal recovery.

The project will not claim device readiness until this live canary passes.

## Acceptance criteria

- Claude Code and Codex can both start an edit against the active Final Cut
  project.
- Each run preserves the source and creates versioned working projects.
- The controller can remove gaps and silence, add titles and transitions, and
  insert a downloaded meme asset in a disposable library.
- The controller renders each candidate through Final Cut and watches the
  resulting preview.
- A successful result includes timestamped evidence for every required edit.
- The controller never reports success after only changing XML or driving the
  UI.
- The allowlist prevents discovery of unrelated local files and libraries.
- Downloaded assets include source provenance.
- The editor retains control of the final export.
- Unit, contract, integration, and live Final Cut 12.3 canary checks pass on
  this device.

## Delivery sequence

1. Add session contracts, storage, allowlist enforcement, and the state
   machine.
2. Integrate FCPXML MCP behind a pinned adapter.
3. Build and live-test the narrow CommandPost adapter on Final Cut 12.3.
4. Install and integrate the shared `watch` skill.
5. Add media acquisition and provenance.
6. Add the Final Cut preview and visual correction loop.
7. Expose the grouped MCP and CLI surfaces.
8. Run the disposable-library canary and document the verified device setup.
