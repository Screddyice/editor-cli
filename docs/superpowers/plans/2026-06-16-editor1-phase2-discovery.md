# Editor1 Phase 2 (Discovery) Implementation Plan

> **For agentic workers:** TDD, frequent commits. Builds on the Phase 1 spine.

**Goal:** A `--genre "<query>"` capability that discovers trending comparable
videos, extracts their sound/title metadata, and feeds them (as extra Gemini
references + trend context) into the existing edit pipeline.

**Architecture:** A `Discoverer` over yt-dlp's `ytsearch` (works headlessly for
YouTube now; HyperCrawl/Instagram is Phase 3). Discovered URLs join the user's
reference set so Gemini's style analysis already reflects the genre trend; sound
metadata is summarized into a text context string passed to style analysis.

**Tech Stack:** yt-dlp (`ytsearchN:`, `--dump-json`), reuses Phase 1 Gemini +
orchestrator.

---

## Task 1: Discovery module

**Files:** Create `src/editor1/acquire/discover.py`; Test `tests/acquire/test_discover.py`

- `discover_genre(query, n=5, runner) -> list[str]` — `yt-dlp "ytsearch{n}:{query}" --print webpage_url --no-download`, returns URLs.
- `fetch_sound_meta(url, runner) -> SoundMeta(title, track, artist, url)` — `yt-dlp --dump-json --no-download`.
- `trend_summary(metas) -> str` — human-readable trend context block.
- Tests inject a fake runner with canned stdout; assert command shape + parsing.

## Task 2: Gemini style context

**Files:** Modify `src/editor1/analysis/gemini.py`; Test `tests/analysis/test_gemini.py`

- `analyze_style(refs, context="")` — append `context` to the style prompt when present. Test: context text reaches the generate call.

## Task 3: Orchestrator genre integration

**Files:** Modify `src/editor1/pipeline/orchestrator.py`; Test `tests/pipeline/test_orchestrator.py`

- `Deps` gains optional `discover`, `sound_meta` (default None).
- `run_edit(..., genre=None, trend_count=5)`: when `genre`, discover URLs → append to `refs`, fetch sound metas → `trend_summary`, pass as context to `analyze_style`.
- Update existing fakes to `analyze_style(files, context="")`.
- New test: genre path adds discovered refs and passes trend context.

## Task 4: CLI flags + build_deps wiring

**Files:** Modify `src/editor1/cli.py`, `src/editor1/pipeline/orchestrator.py`

- `editor1 edit ... --genre "<query>" --trend-count N`.
- `build_deps` binds `discover_genre`/`fetch_sound_meta`.
