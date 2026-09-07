---
name: final-cut-editor
description: Edit the selected Final Cut Pro project through Editor CLI, then render, watch, and verify every candidate before leaving the best project open.
---

# Final Cut Editor

Use this skill when the user asks you to change a project that is open in Final Cut Pro.

## Control loop

1. Call `editor_session` with `action: doctor`. Stop if Final Cut Pro 12.3, the signed native helper, permissions, or the shared `watch` skill is unavailable. Doctor never prompts for permissions; the user can run `editor-cli permissions request`. No CommandPost or paid bridge is required.
2. Call `editor_session` with `action: start`, the user's complete edit request as `prompt`, and `required_operations` naming the requested edits. Persist the returned `session_id`. This preserves the original project before any edit. Confirm the returned library, event, and project. Stop if the selection is ambiguous.
3. Call `editor_timeline` with `action: inspect`. Review clips, gaps, roles, markers, effects, transcript, and pacing before preparing edits.
4. Use active-project media references, installed Final Cut or Motion assets, and files created in the current Editor CLI session. Timeline inspection returns `installed_assets`; `editor_media` with `action: list` returns downloaded session assets. Use `action: acquire` for public HTTPS media; register the returned session file with `action: register`, `asset_path`, `name`, and `duration_seconds`. The controller verifies provenance and probes audio/video streams. Reference returned asset IDs in edit programs, never raw source paths or guessed asset names. Do not search unrelated local folders, libraries, Photos, or drives. Do not bypass DRM, logins, or paywalls.
5. Submit the complete typed edit program with `editor_timeline` and `action: apply`. The user's request authorizes the full edit; do not ask for another approval inside the session.
6. Call `editor_verify` with `action: preview`, then `action: watch`. Read the rendered frames and transcript returned by the shared `watch` evidence bundle. XML change is not proof that the edit worked.
7. Record every requested and technical check through `editor_verify` with `action: record`, `session_id`, `pass_number`, and `report`. Include the candidate's exact returned evidence binding and required checks. Mark a check true only when the rendered evidence supports it; do not invent bindings or treat title text as a speech transcript.
8. If a required check fails, inspect the observation, submit a correction, render again, and review the new evidence. Stop after three passes.
9. Leave the verified project open. If all three passes fail, leave the highest-scoring candidate open and list the failed checks.

Never modify or delete the original project. Never perform the final export. The user reviews the open project and exports it from Final Cut Pro.

After interruption, call `editor_session` with `action: status`, then `action: resume`
for the existing session. Recovery requires exact durable receipts; do not replay
an uncertain import, Share, or download by hand. Native preview recovery requires
the helper's completion receipt, not merely an output file. Timeline `undo` restores
the previous checkpoint as the editing source. It does not approve an old review.

For selected-file editing without Final Cut, use `direct-video-editor` instead.
The user's own speech or selected narration does not require transcription;
request word timestamps only for word-aware cuts or captions.
