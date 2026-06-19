"""HyperFrames title authoring + the title-engine dispatcher (auto/pillow/hf)."""

import subprocess
from types import SimpleNamespace

import pytest

from editor_cli.render import hyperframes, overlays, titles
from editor_cli.render.ffmpeg import _title_layout, duration_of, probe


# --- composition authoring (pure) -------------------------------------------

def _layouts(*specs):
    return [_title_layout(s) for s in specs]


def test_author_html_has_composition_contract_and_text():
    lays = _layouts({"text": "Hello & welcome", "start": 0.0, "end": 2.0, "position": "top"})
    html = hyperframes.author_titles_html(lays, 1080, 1920, 5.0)
    assert 'data-composition-id="titles"' in html
    assert 'data-width="1080"' in html and 'data-height="1920"' in html
    assert 'class="clip title"' in html
    assert 'data-start="0"' in html and 'data-duration="2"' in html
    assert 'window.__timelines["titles"]' in html
    assert "gsap.timeline" in html
    assert "Hello &amp; welcome" in html  # HTML-escaped
    assert "background:transparent" in html  # alpha render


def test_author_html_one_clip_per_title_with_animation():
    lays = _layouts(
        {"text": "A", "start": 0, "end": 1},
        {"text": "B", "start": 2, "end": 3, "position": "center"},
    )
    html = hyperframes.author_titles_html(lays, 640, 360, 4.0)
    assert html.count('class="clip title"') == 2
    assert html.count("tl.fromTo") == 2 and html.count("tl.to(") == 2


# --- render command shape (mocked runtime) ----------------------------------

def _runner(ok=True):
    calls = []

    def run(cmd, **kw):
        calls.append((list(cmd), kw))
        j = " ".join(cmd)
        if "hyperframes --version" in j:
            return SimpleNamespace(returncode=0 if ok else 1, stdout="1.0.0", stderr="")
        if any(p in j for p in ("node --version", "ffmpeg -version", "npx --version")):
            return SimpleNamespace(returncode=0, stdout="v22", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    run.calls = calls
    return run


def test_render_titles_overlay_webm_command(tmp_path):
    run = _runner(ok=True)
    out = tmp_path / "titles.webm"
    res = hyperframes.render_titles_overlay(
        [{"text": "Hi", "start": 0, "end": 1}], 1080, 1920, 3.0, str(out), runner=run
    )
    assert res == str(out)
    render = next(c for c, kw in run.calls if "render" in c)
    assert render[:3] == ["npx", "hyperframes", "render"]
    assert "--format" in render and "webm" in render
    assert "--output" in render


def test_render_titles_overlay_none_when_no_text(tmp_path):
    run = _runner(ok=True)
    assert hyperframes.render_titles_overlay(
        [{"start": 0}], 100, 100, 2.0, str(tmp_path / "o.webm"), runner=run
    ) is None


def test_render_titles_overlay_raises_without_runtime(tmp_path):
    run = _runner(ok=False)
    with pytest.raises(overlays.OverlayError):
        hyperframes.render_titles_overlay(
            [{"text": "x", "start": 0, "end": 1}], 100, 100, 2.0, str(tmp_path / "o.webm"),
            runner=run,
        )


# --- dispatcher --------------------------------------------------------------

def _clip(path, size="640x360", seconds=2):
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def test_auto_falls_back_to_pillow_when_runtime_cold(tmp_path):
    v = tmp_path / "v.mp4"
    _clip(v)
    out = tmp_path / "o.mp4"
    # cold runtime -> auto must still produce a titled video via Pillow
    titles.render(
        str(v), [{"text": "Hi", "start": 0.2, "end": 1.5}], str(out),
        engine="auto", runner=_runner(ok=False),
    )
    assert out.exists()
    assert duration_of(str(out)) == pytest.approx(2.0, abs=0.3)


def test_explicit_pillow_engine_renders(tmp_path):
    v = tmp_path / "v.mp4"
    _clip(v)
    out = tmp_path / "o.mp4"
    titles.render(str(v), [{"text": "Yo", "start": 0, "end": 1}], str(out), engine="pillow")
    info = probe(str(out))
    assert any(s["codec_type"] == "audio" for s in info["streams"])
