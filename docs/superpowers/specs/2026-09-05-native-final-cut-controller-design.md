# Native Final Cut Controller Design

**Date:** 2026-09-05
**Status:** Approved direction; awaiting written-spec review
**Owner:** Shawn
**Target:** Final Cut Pro Creator Studio 12.3 on Apple silicon

## Summary

Editor CLI will control Final Cut Pro through a project-owned Swift helper built
on Apple's Accessibility and Apple Event frameworks. This replaces CommandPost
and the paid LateNite license dependency. No third-party controller app, local
network listener, subscription, or App Store purchase is required.

The user opens Final Cut, selects a project, and makes any initial cuts. Claude
Code or Codex then asks Editor CLI to preserve the source, apply edits to a
working copy, import the candidate, render a temporary preview with Final Cut's
Share command, watch that render, and correct defects. The selected candidate
stays open in Final Cut. The user performs the final delivery export.

## Decisions

- A signed native Swift executable replaces CommandPost and its WebSocket
  bridge.
- The executable exposes a fixed JSON command protocol over stdin and stdout.
  It does not open a network port.
- The helper uses `AXUIElement` to press named Final Cut controls and set values
  in known dialogs. It does not provide arbitrary AppleScript, shell, menu,
  keystroke, or accessibility execution.
- Final Cut's own Share render is the only visual artifact that can satisfy a
  candidate review. An FFmpeg FCPXML render may support diagnostics but cannot
  prove acceptance.
- The controller owns required checks and binds every review to the exact
  session, pass, candidate XML, Final Cut render, and evidence manifest.
- The source project, working candidate, temporary render, and captured media
  references use exact identities and content hashes.
- The user performs the final delivery export in Final Cut Pro.

## Native helper

The repository will contain a small Swift package under
`native/final-cut-bridge/`. Setup compiles it with the installed Apple toolchain
and installs a stable, ad-hoc signed executable under
`~/Library/Application Support/Editor CLI/bin/`. The executable carries a
stable signing identifier and reports its protocol version and source hash.

The helper links only Apple frameworks:

- ApplicationServices for Accessibility;
- AppKit for application and window discovery;
- Foundation for JSON, files, processes, and timeouts; and
- Apple Events through `NSAppleScript` for Final Cut's read-only library
  inspection dictionary.

Each invocation accepts one JSON request and emits one JSON response. The
request schema uses `additionalProperties: false`. Supported actions are:

- `probe`: report helper version, Final Cut bundle identity and version,
  Accessibility trust, Automation capability, blocking dialogs, and active
  project identity;
- `duplicate_project`: invoke `Duplicate Project As...`, set the exact generated
  name, confirm the dialog, and poll for that project in the same library and
  event;
- `export_xml`: invoke Final Cut's XML export, constrain the save location to
  the active session, wait for the file, parse it, and verify project identity;
- `import_xml`: open the candidate FCPXML and poll for the exact generated
  project, duration, library, and event while rejecting missing-media dialogs;
- `open_project`: select an existing session candidate by exact library, event,
  and project identity;
- `share_preview`: invoke `Share > Export File`, write to the active session,
  wait for Final Cut's background task to finish, and return the completed file
  identity; and
- `inspect_dialogs`: report only dialogs that can block the current operation.

The Python adapter validates the same enum before starting the helper. Unknown
actions fail before any Apple API call.

## Final Cut interaction rules

The helper identifies Final Cut by bundle ID `com.apple.FinalCutApp` and
requires version 12.3. It rejects another application with a matching process
name. Accessibility actions start from the verified Final Cut process and walk
only the menu, sheet, browser, and background-task controls required for the
requested action.

The helper does not send blind keyboard shortcuts. It uses accessibility roles,
identifiers, titles, and enabled state. If a control is missing or ambiguous,
the action stops and records the observed window and dialog metadata. English
control labels are the initial supported locale for this device.

Every state-changing action has a postcondition:

- duplication requires the exact new project identity;
- export requires a stable FCPXML file whose project and duration match;
- import requires the exact candidate identity and no missing-media dialog;
- open requires the exact project to become active; and
- share requires a stable movie file, a completed Final Cut background task,
  and matching candidate identity.

A menu press or dialog confirmation is not completion.

## Permissions and setup

`editor-cli setup` builds and signs the helper, installs it at the stable path,
stores its expected hash, and configures both agent hosts to use the same MCP
server. Setup preserves unrelated configuration and creates atomic, one-time
backup files before its first edit.

macOS must grant the installed helper:

- Accessibility permission to control Final Cut's user interface; and
- Automation permission to read Final Cut's library, event, and project
  identities.

Doctor reports the exact missing permission and opens no dialog unless the user
runs a dedicated permission-request command. Doctor does not require
CommandPost, Fast Collections, or any LateNite application.

## Session and access boundaries

The existing session allowlist remains binding. The controller may read:

- files under the active Editor CLI session;
- exact media paths already referenced by the captured FCPXML; and
- explicit installed Final Cut and Motion asset roots.

The controller cannot enumerate a referenced file's parent directory. Every
nested edit-program path passes through the same canonical allowlist. Strict
action-specific models reject unknown arguments.

The Swift helper accepts one resolved session root per invocation. Output paths
must remain below that root after symlink resolution. It cannot read arbitrary
paths or derive its allowlist from the home directory.

Internet media remains optional. The acquisition layer accepts public HTTPS,
checks each redirect and connected address against private-network ranges,
uses bounded timeouts and a temporary download, enforces the 500 MB cap, and
writes provenance before the asset enters a timeline. It never receives browser
cookies or searches local media.

## Controller-owned verification

At session creation, the controller derives and persists required checks from
the normalized edit request. The required set includes source preservation,
requested structural changes, requested visible changes, rendered-preview
integrity, and watched evidence. A review must contain that exact set.

Each review binds to:

- session ID and state version;
- pass number and generated project identity;
- candidate FCPXML hash;
- Final Cut preview path and hash;
- evidence-manifest path and hash;
- evidence frame timestamps; and
- controller-owned required checks.

The controller rejects missing, additional, stale, or non-boolean checks. It
also rejects a review after another process advances the session.

Technical verification parses the candidate FCPXML, derives its duration,
runs the pinned FCPXML diagnostic checks, confirms media references, and probes
the Final Cut render. Creative verification reads frames and transcript from
the matching evidence manifest. XML changes alone cannot satisfy a visible
check.

## Transactions and recovery

One interprocess lock protects each session. State snapshots include a monotonic
version. Mutating commands compare the expected version before and after each
external action.

The journal records intent before an external action and a receipt after it.
Intent contains the action, parameters, expected Final Cut identity, allowed
paths, and idempotency evidence. On resume, the controller reconciles the real
postcondition:

- an existing matching export completes `export_xml`;
- an exact duplicate completes `duplicate_project`;
- an exact imported project completes `import_xml`;
- a matching stable render completes `share_preview`;
- an active exact project completes `open_project`; and
- a temporary or final asset with the expected URL and hash reconciles a
  download.

The controller does not replay an uncertain action. A mismatch moves the
session to `BLOCKED` with one concrete operator action.

## Timeline and media planning

Timeline inspection returns normalized clips, gaps, roles, markers, effects,
transcript references, pacing statistics, and installed asset identifiers.
Acquired media receives a session asset ID before an edit program can insert
it. Edit operations refer to that ID instead of a caller-provided filesystem
path.

Candidate names include the short session ID and pass number so concurrent
sessions cannot collide. Undo creates and opens another journaled candidate; it
does not rewrite a prior project.

## Packaging

The wheel and source distribution include:

- Swift helper source and build manifest;
- the Final Cut editing skill;
- native protocol schemas;
- live canary source; and
- setup metadata.

Packaged setup loads these files through `importlib.resources`. A clean virtual
environment must be able to build the helper and run setup without a repository
checkout.

## Live canary

The disposable canary creates generated color cards, tones, titles, and a new
Final Cut library. It never opens a user library. The test:

1. imports the generated source project;
2. exports and hashes the original Final Cut project;
3. creates a named duplicate;
4. removes a one-second gap;
5. adds a title, cross-dissolve, and reaction card;
6. imports the candidate and verifies its exact identity;
7. restarts the controller and reconciles the journal;
8. shares a temporary preview through Final Cut;
9. watches frames at the edited ranges;
10. re-exports the original Final Cut project and compares its XML and media
    hashes; and
11. leaves the accepted candidate open for the user's manual export.

All required checks must pass. The project cannot claim device readiness from
unit tests, FCPXML import, an Accessibility action, or an FFmpeg proxy.

## Error handling

- Missing Accessibility or Automation permission blocks before mutation.
- An ambiguous Final Cut control or project identity blocks the action.
- A dialog, timeout, missing media item, or background-task failure stays in
  the session journal for resume.
- Final Cut version drift blocks live control until its accessibility contract
  passes again.
- A render mismatch keeps the candidate for inspection but cannot mark it
  ready.
- Three failed visual passes leave the strongest candidate open and mark the
  session blocked.

## Acceptance criteria

- No paid controller application or LateNite license is required.
- Doctor verifies the signed native helper, Final Cut 12.3, both macOS
  permissions, and live accessibility postconditions.
- The helper exposes only the seven documented actions and opens no network
  listener.
- Every edit preserves a verified Final Cut source project.
- Every candidate import and open action proves exact identity.
- Every accepted candidate uses a preview rendered by Final Cut and watched by
  the shared evidence adapter.
- Edit paths cannot escape session and exact-reference allowlists.
- Reviews cannot replace or weaken the controller-owned acceptance contract.
- Interrupted writes reconcile without blind replay.
- Packaged setup works outside a repository checkout.
- The disposable canary passes on this Mac.
- The user retains control of the final export.

## Migration

The existing CommandPost bridge and LateNite license checks will be removed
from setup, doctor, runtime adapters, tests, and documentation. Existing
CommandPost installation files on the device remain untouched. The migration
does not uninstall applications or change their configuration.

PR #18 remains draft during the migration. The branch cannot be marked ready or
merged until the native helper passes the live canary and both Claude Code and
Codex read the same preview hash from its evidence manifest.
