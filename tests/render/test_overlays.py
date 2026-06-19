"""Arms-length OpenMontage/HyperFrames bridge + ffmpeg overlay compositing.

The bridge only ever shells out (subprocess), so command construction and the
doctor are tested with an injected runner. The compositor is real ffmpeg.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from editor_cli.render import overlays
from editor_cli.render.ffmpeg import duration_of, overlay_onto, probe


def _runner(table):
    """Fake runner: maps a substring of the command to (returncode, stdout)."""
    calls = []

    def run(cmd, **kw):
        calls.append((list(cmd), kw))
        joined = " ".join(cmd)
        for key, (rc, outp) in table.items():
            if key in joined:
                return SimpleNamespace(returncode=rc, stdout=outp, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="not found")

    run.calls = calls
    return run


# --- doctor ------------------------------------------------------------------

def test_runtime_status_ok_when_all_present():
    run = _runner({
        "node --version": (0, "v22.3.0"),
        "ffmpeg -version": (0, "ffmpeg version 8"),
        "npx --version": (0, "10.0.0"),
        "hyperframes --version": (0, "1.2.3"),
    })
    st = overlays.runtime_status(runner=run)
    assert st["ok"] is True
    assert st["node"] == "v22.3.0" and st["hyperframes"] == "1.2.3"


def test_runtime_status_not_ok_without_hyperframes():
    run = _runner({
        "node --version": (0, "v22.3.0"),
        "ffmpeg -version": (0, "ffmpeg version 8"),
        "npx --version": (0, "10.0.0"),
        # hyperframes probe falls through -> rc 1
    })
    st = overlays.runtime_status(runner=run)
    assert st["ok"] is False and st["hyperframes"] is None


# --- submodule ---------------------------------------------------------------

def test_submodule_root_resolves_to_checked_out_vendor():
    root = overlays.submodule_root()
    assert root.name == "OpenMontage" and root.is_dir()


# --- render command shape ----------------------------------------------------

def test_render_overlay_shells_npx_hyperframes_render(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    run = _runner({
        "node --version": (0, "v22.3.0"),
        "ffmpeg -version": (0, "ffmpeg version 8"),
        "npx --version": (0, "10.0.0"),
        "hyperframes --version": (0, "1.2.3"),
        "hyperframes render": (0, "rendered"),
    })
    overlays.render_overlay(proj, strict=True, runner=run)
    render_call = next(c for c, kw in run.calls if "render" in c)
    assert render_call[:3] == ["npx", "hyperframes", "render"]
    assert "--strict" in render_call
    # runs inside the project dir (process boundary, never imported)
    render_kw = next(kw for c, kw in run.calls if "render" in c)
    assert render_kw.get("cwd") == str(proj)


def test_render_overlay_raises_without_runtime(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    run = _runner({"node --version": (0, "v22")})  # no hyperframes
    with pytest.raises(overlays.OverlayError, match="runtime unavailable"):
        overlays.render_overlay(proj, runner=run)


def test_render_overlay_raises_on_missing_project(tmp_path):
    run = _runner({"hyperframes --version": (0, "1.2.3")})
    with pytest.raises(overlays.OverlayError, match="project dir not found"):
        overlays.render_overlay(tmp_path / "nope", runner=run)


# --- real ffmpeg compositing -------------------------------------------------

def _base(path, size="640x360", seconds=2):
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def _alpha_overlay(path, seconds=1):
    # a semi-transparent box with alpha (mov/qtrle preserves alpha)
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"color=c=red@0.5:s=120x80:d={seconds}:r=30,format=rgba",
         "-c:v", "qtrle", str(path)],
        check=True, capture_output=True,
    )


def test_overlay_onto_keeps_base_dims_duration_and_audio(tmp_path):
    base = tmp_path / "base.mp4"
    ov = tmp_path / "ov.mov"
    _base(base)
    _alpha_overlay(ov)
    out = tmp_path / "out.mp4"
    overlay_onto(str(base), str(ov), str(out), x="20", y="20", start=0.5)
    info = probe(str(out))
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (int(v["width"]), int(v["height"])) == (640, 360)
    assert duration_of(str(out)) == pytest.approx(2.0, abs=0.3)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
