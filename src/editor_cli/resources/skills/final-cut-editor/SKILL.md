---
name: final-cut-editor
description: Edit the selected Final Cut Pro project through Editor CLI, then render, watch, and verify every candidate before leaving the best project open.
---

# Final Cut Editor

Use this skill when the user asks you to change a project that is open in Final Cut Pro.

## Control loop

1. Call `editor_session` with `action: doctor`. Stop if Final Cut, the loopback CommandPost bridge, permissions, or the shared `watch` skill is unavailable.
2. Call `editor_session` with `action: start` and the user's complete edit request. This preserves the original project before any edit. Confirm the returned library, event, and project. Stop if the selection is ambiguous.
3. Call `editor_timeline` with `action: inspect`. Review clips, gaps, roles, markers, effects, transcript, and pacing before preparing edits.
4. Use active-project media references, installed Final Cut or Motion assets, and files created in the current Editor CLI session. Use `editor_media` for public HTTPS media. Do not search unrelated local folders, libraries, Photos, or drives. Do not bypass DRM, logins, or paywalls.
5. Submit the complete typed edit program with `editor_timeline` and `action: apply`. The user's request authorizes the full edit; do not ask for another approval inside the session.
6. Call `editor_verify` with `action: preview`, then `action: watch`. Read the rendered frames and transcript returned by the shared `watch` evidence bundle. XML change is not proof that the edit worked.
7. Record every requested and technical check through `editor_verify` with `action: record`. Mark a check true only when the rendered evidence supports it.
8. If a required check fails, inspect the observation, submit a correction, render again, and review the new evidence. Stop after three passes.
9. Leave the verified project open. If all three passes fail, leave the highest-scoring candidate open and list the failed checks.

Never modify or delete the original project. Never perform the final export. The user reviews the open project and exports it from Final Cut Pro.
