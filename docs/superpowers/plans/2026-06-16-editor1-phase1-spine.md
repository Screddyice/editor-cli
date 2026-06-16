# Editor1 Phase 1 (Spine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working `editor1 edit` command that turns a footage folder + prompt + local/YouTube reference videos into `final.mp4` + `timeline.fcpxml`, with a Gemini style→cut→eval loop.

**Architecture:** Python CLI (typer). Deterministic core (domain models, EDL→FCPXML, ffmpeg render) is unit-tested with fixtures; API adapters (Gemini, ElevenLabs, yt-dlp) sit behind thin interfaces and are mocked in unit tests with one live smoke test each. An orchestrator wires the stages and runs the capped eval loop.

**Tech Stack:** Python ≥3.10, uv, typer, google-genai (Gemini), requests (ElevenLabs), ffmpeg/ffprobe, yt-dlp. Reuses `references/video-use` helpers for transcription + render concepts.

---

## File Structure

```
pyproject.toml                       # uv project, [project.scripts] editor1
src/editor1/__init__.py
src/editor1/cli.py                   # typer app — thin controllers
src/editor1/config.py                # keys + paths, .env loading
src/editor1/domain/edl.py            # EDL + Segment dataclasses, JSON (de)ser + validation
src/editor1/domain/style_profile.py  # StyleProfile dataclass, JSON (de)ser + validation
src/editor1/render/fcpxml.py         # EDL -> FCPXML (NEW core), DTD-valid
src/editor1/render/ffmpeg.py         # EDL -> mp4 (extract/concat), ffprobe manifest
src/editor1/analysis/transcribe.py   # ElevenLabs adapter (interface + impl)
src/editor1/analysis/gemini.py       # Gemini adapter: style(), reason_edl(), evaluate()
src/editor1/acquire/fetch.py         # yt-dlp wrapper (URL -> local file)
src/editor1/acquire/local.py         # local path passthrough + validation
src/editor1/pipeline/orchestrator.py # run stages + eval loop
tests/...                            # mirror of src tree
tests/fixtures/                      # golden fcpxml, sample edl json, tiny clip
```

Convention: `src/` layout, tests mirror `src/`. Each module one responsibility.

---

## Task 1: Project scaffold + `editor1 --help`

**Files:**
- Create: `pyproject.toml`, `src/editor1/__init__.py`, `src/editor1/cli.py`
- Test: `tests/test_cli_smoke.py`

- [ ] **Step 1: Write failing test** — `tests/test_cli_smoke.py`:
```python
from typer.testing import CliRunner
from editor1.cli import app

def test_help_lists_edit_command():
    res = CliRunner().invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "edit" in res.output
```
- [ ] **Step 2: Run, verify fail** — `uv run pytest tests/test_cli_smoke.py -v` → FAIL (no module `editor1`).
- [ ] **Step 3: Implement** — `pyproject.toml` with `[project] name="editor1"`, deps `typer`, `requests`, `google-genai`, `yt-dlp`; `[project.scripts] editor1="editor1.cli:app"`; `[tool.setuptools] packages=["editor1"]` + `package-dir={"":"src"}`. `cli.py`:
```python
import typer
app = typer.Typer(help="Editor1 — AI video editor (FCP + Gemini)")

@app.command()
def edit(footage_dir: str, prompt: str = typer.Option(...)):
    """Edit footage into final.mp4 + timeline.fcpxml."""
    typer.echo(f"edit {footage_dir}: {prompt}")
```
- [ ] **Step 4: `uv sync && uv run pytest tests/test_cli_smoke.py -v`** → PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(cli): scaffold editor1 typer app"`.

## Task 2: EDL domain model

**Files:** Create `src/editor1/domain/edl.py`; Test `tests/domain/test_edl.py`

- [ ] **Step 1: Failing test** — round-trip + validation:
```python
from editor1.domain.edl import EDL, Segment

def test_edl_roundtrip():
    edl = EDL(fps=30.0, resolution=(1080,1920),
              segments=[Segment(src="a.mp4", in_=0.0, out=2.5)])
    assert EDL.from_json(edl.to_json()) == edl

def test_segment_rejects_negative_duration():
    import pytest
    with pytest.raises(ValueError):
        Segment(src="a.mp4", in_=3.0, out=1.0)
```
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — `@dataclass` `Segment(src, in_, out, grade=None, overlays=None)` with `__post_init__` raising `ValueError` if `out <= in_`; `EDL(fps, resolution, segments, titles=[], subtitles=False, music=None)` with `to_json`/`from_json` (json module; tuple↔list for resolution). `eq=True`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(domain): EDL + Segment model`.

## Task 3: StyleProfile domain model

**Files:** Create `src/editor1/domain/style_profile.py`; Test `tests/domain/test_style_profile.py`

- [ ] **Step 1: Failing test** — round-trip from the spec's JSON shape; assert required keys (`pacing`, `transitions`, `color`, `captions`, `sound`, `vibe`) and that `from_json` of a dict missing `pacing` raises `ValueError`.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — nested dataclasses `Pacing(cuts_per_min, avg_shot_len_s)`, `Color(description, lut=None)`, `Captions(style, position, font=None)`, `Sound(name, energy, genre, bpm=None)`, `StyleProfile(pacing, transitions, automations, color, captions, sound, vibe)` with `to_json`/`from_json` validating required keys.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(domain): StyleProfile model`.

## Task 4: EDL → FCPXML bridge (NEW core)

**Files:** Create `src/editor1/render/fcpxml.py`; Test `tests/render/test_fcpxml.py`; Fixture `tests/fixtures/expected_basic.fcpxml`

- [ ] **Step 1: Determine target DTD** — Run `ls "/Applications/Final Cut Pro.app/Contents/PlugIns" 2>/dev/null; mdls -name kMDItemVersion "/Applications/Final Cut Pro.app"`. Open FCP once, create an empty project, File→Export XML, inspect the `<fcpxml version="…">` it writes. Record the version (e.g. `1.13`) as `FCPXML_VERSION` in the module. (This step verifies the real import target rather than guessing.)
- [ ] **Step 2: Failing test**:
```python
from editor1.domain.edl import EDL, Segment
from editor1.render.fcpxml import edl_to_fcpxml

def test_single_clip_timeline():
    edl = EDL(fps=30.0, resolution=(1080,1920),
              segments=[Segment(src="/abs/a.mov", in_=1.0, out=3.0)])
    xml = edl_to_fcpxml(edl, project_name="t")
    assert 'version="' in xml
    assert "asset-clip" in xml and "<spine>" in xml
    # 2.0s at 30fps spine duration
    assert "200/100s" in xml or "60/30s" in xml
```
- [ ] **Step 3: Run, verify fail.**
- [ ] **Step 4: Implement** — build `<fcpxml>` with `<resources>` (`<format>` from fps/resolution, one `<asset>` per unique `src` with `ffprobe` duration), `<library>/<event>/<project>/<sequence>/<spine>` of `<asset-clip>` elements. Use FCP rational time (`numerator/denominators` where denom = fps-derived timebase). Emit absolute `file://` media refs. Use `xml.etree.ElementTree` or hand-built strings; pretty-print.
- [ ] **Step 5: Run → PASS.**
- [ ] **Step 6: Golden test** — write the produced XML to `tests/fixtures/expected_basic.fcpxml`; add a test asserting byte-equality of regenerated output (regression lock). Add a `validate_dtd` test if a DTD is available, else mark `xfail` with reason.
- [ ] **Step 7: Manual gate (record in PR)** — import the generated fcpxml into FCP 12.2; confirm the clip lands on the timeline with media linked. Note result in the PR.
- [ ] **Step 8: Commit** — `feat(render): EDL→FCPXML bridge`.

## Task 5: ffmpeg render + ffprobe manifest

**Files:** Create `src/editor1/render/ffmpeg.py`; Test `tests/render/test_ffmpeg.py`; Fixture: generate a tiny clip in the test.

- [ ] **Step 1: Failing test** — generate a 3s color test clip via `ffmpeg -f lavfi -i testsrc=duration=3:size=320x240:rate=30`, build an EDL cutting 0.5–1.5s, call `render_edl(edl, out)`, assert `out` exists and `ffprobe` duration ≈ 1.0s (±0.1).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — `probe(path)->dict` wrapping `ffprobe -v quiet -print_format json -show_format -show_streams`. `render_edl(edl, out, preview=False)`: per-segment `-ss in -to out` extract to temp, concat via concat demuxer, scale to preview if requested. Shell via `subprocess.run(check=True)`. (Reference: `references/video-use/helpers/render.py` for the PTS/concat approach.)
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(render): ffmpeg EDL render + ffprobe manifest`.

## Task 6: Config + key loading

**Files:** Create `src/editor1/config.py`; Test `tests/test_config.py`

- [ ] **Step 1: Failing test** — `load_config(env={"GEMINI_API_KEY":"x","ELEVENLABS_API_KEY":"y"})` returns keys; missing key raises `ConfigError` naming the variable.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — read `GEMINI_API_KEY` (fallback `CLIQK_GEMINI_API_KEY`) and `ELEVENLABS_API_KEY` from passed env / `os.environ` / project `.env`; `ConfigError` with the missing name. Provide `Config` dataclass.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(config): key + path loading`.

## Task 7: Gemini adapter (style / reason / evaluate)

**Files:** Create `src/editor1/analysis/gemini.py`; Test `tests/analysis/test_gemini.py` (mocked) + `tests/analysis/test_gemini_live.py` (smoke, skipped without key)

- [ ] **Step 1: Verify SDK call shape** — Run `uv run python -c "import google.genai as g; print(g.__version__)"`; confirm the upload-video + `generate_content` call shape against the installed `google-genai`. Record the exact call used.
- [ ] **Step 2: Failing unit test** — with a fake client injected, `analyze_style(client, [path])` returns a `StyleProfile`; `reason_edl(client, footage_manifest, transcript, style, prompt)` returns an `EDL`; `evaluate(client, render_path, style, prompt)` returns `EvalResult(score: float, issues: list[str])`. Assert the adapter parses the JSON the (faked) model returns into the domain types and raises on malformed JSON after one retry.
- [ ] **Step 3: Run, verify fail.**
- [ ] **Step 4: Implement** — `GeminiClient` wrapping `google.genai.Client`; upload video files, prompt with a JSON-schema instruction, parse response into `StyleProfile`/`EDL`/`EvalResult`; one corrective retry on `json.JSONDecodeError`/schema-validation error. Prompt templates in `src/editor1/prompts/`.
- [ ] **Step 5: Run mocked tests → PASS.** Live smoke test in `test_gemini_live.py` guarded by `@pytest.mark.skipif(no key)`.
- [ ] **Step 6: Commit** — `feat(analysis): Gemini style/reason/eval adapter`.

## Task 8: ElevenLabs transcription adapter

**Files:** Create `src/editor1/analysis/transcribe.py`; Test `tests/analysis/test_transcribe.py` (mocked)

- [ ] **Step 1: Failing test** — `transcribe(path, api_key, http=fake)` returns `Transcript` with word-level `(word, start, end)` entries parsed from a canned ElevenLabs Scribe JSON response.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — port the request shape from `references/video-use/helpers/transcribe.py`; inject the HTTP client for testability; return a `Transcript` dataclass. Cache by file hash under `<out>/transcripts/`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(analysis): ElevenLabs transcription`.

## Task 9: yt-dlp fetch + local passthrough

**Files:** Create `src/editor1/acquire/fetch.py`, `src/editor1/acquire/local.py`; Test `tests/acquire/test_acquire.py`

- [ ] **Step 1: Failing test** — `resolve_reference("/tmp/x.mp4")` (exists) returns the path; `resolve_reference("https://youtu.be/ID", out_dir, runner=fake)` invokes the fake yt-dlp runner with the URL and returns the downloaded path; a non-existent local path raises `FileNotFoundError`.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — `local.resolve(path)` validates existence; `fetch.download(url, out_dir, runner=subprocess.run)` shells `yt-dlp -o <out_dir>/%(id)s.%(ext)s <url>` and returns the resulting file; `resolve_reference` dispatches on URL vs path. (HyperCrawl/IG deferred to Phase 2/3.)
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(acquire): yt-dlp fetch + local passthrough`.

## Task 10: Orchestrator + `editor1 edit` wiring + eval loop

**Files:** Create `src/editor1/pipeline/orchestrator.py`; Modify `src/editor1/cli.py`; Test `tests/pipeline/test_orchestrator.py`

- [ ] **Step 1: Failing test** — with all adapters faked (gemini returns a fixed style + EDL, then an `EvalResult(score=0.9, issues=[])`; render writes a stub file), `run_edit(footage_dir, prompt, refs, out, deps)` produces `out/final.mp4` and `out/timeline.fcpxml` and stops after 1 eval pass when score ≥ threshold. A second test: eval returns score<threshold twice then ≥ → asserts re-cut called twice and loop caps at `max_eval`.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — `run_edit`: resolve refs → analyze_style → probe+transcribe footage → reason_edl → render mp4 + emit fcpxml → evaluate → if score<threshold and passes<max_eval, feed issues into reason_edl and re-render; else stop. Dependencies passed as a struct for injection. Wire `cli.edit` to build real deps from `Config` and call `run_edit`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Live end-to-end smoke (manual, record in PR)** — run `editor1 edit` on a few-second sample with one YouTube ref + a real key; confirm `final.mp4` + `timeline.fcpxml` produced and the fcpxml imports into FCP.
- [ ] **Step 6: Commit** — `feat(pipeline): orchestrator + editor1 edit + eval loop`.

---

## Self-Review

- **Spec coverage:** acquisition (T9), style analysis (T7), footage analysis (T5 probe + T8 transcribe), edit reasoning (T7), render mp4 (T5) + FCPXML (T4), eval loop (T10), CLI (T1/T10), config (T6), domain contracts (T2/T3). Phase 2 (HyperCrawl genre/sound) and Phase 3 (IG/TikTok) are intentionally out of this plan.
- **Placeholder scan:** none — API-shape-verification steps (T4.1, T7.1) are concrete instructions, not deferrals.
- **Type consistency:** `EDL`/`Segment`/`StyleProfile`/`EvalResult`/`Transcript`/`Config` names used consistently across T2–T10; `edl_to_fcpxml`, `render_edl`, `probe`, `analyze_style`, `reason_edl`, `evaluate`, `transcribe`, `resolve_reference`, `run_edit` referenced as defined.
