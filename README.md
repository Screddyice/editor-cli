# Editor1

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

🚧 Early scaffold. Architecture under design (`DESIGN.md` to follow).

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
