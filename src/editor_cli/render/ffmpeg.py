"""ffmpeg render — turn an EDL into an mp4, plus an ffprobe media manifest.

Each segment is seek-extracted and re-encoded to a uniform format/resolution so
the parts concat cleanly (copy concat). The frame is derived from the source
footage so the output keeps the clips' real aspect ratio (9:16 or 16:9);
segments are letterbox-padded to fit, never stretched. Final renders encode
near-visually-lossless (CRF 18); preview fits within a 1280x720 box, fast.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

from editor_cli.domain.edl import EDL


class RenderError(RuntimeError):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RenderError(f"{cmd[0]} failed (exit {res.returncode}): {res.stderr[-2000:]}")
    return res


def probe(path: str) -> dict:
    """ffprobe manifest: format + streams as a dict."""
    res = _run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path]
    )
    return json.loads(res.stdout)


def duration_of(path: str) -> float:
    return float(probe(path)["format"]["duration"])


def sample_frames(src: str, n: int, out_dir: str) -> list[tuple[float, str]]:
    """Extract ``n`` JPEG frames sampled evenly across ``src``.

    Returns ``[(timestamp_seconds, image_path), ...]`` in time order — the input
    the shot-moment selector needs to choose the most engaging in-point. Samples
    span the inner 5–95% of the clip so we never land on a black lead frame or a
    trailing fade.
    """
    if n < 1:
        return []
    dur = duration_of(src)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]
    span = dur * 0.90
    frames: list[tuple[float, str]] = []
    for i in range(n):
        t = dur * 0.05 + (span * i / (n - 1) if n > 1 else span / 2)
        img = os.path.join(out_dir, f"{stem}_{i:02d}.jpg")
        _run(["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", src,
              "-frames:v", "1", "-q:v", "3", img])
        frames.append((t, img))
    return frames


def _stream_dims(path: str) -> tuple[int, int]:
    for s in probe(path)["streams"]:
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise RenderError(f"no video stream in {path}")


def _even(n: int) -> int:
    """x264 with yuv420p needs even dimensions."""
    return max(2, int(n) - (int(n) % 2))


def _target_resolution(edl: EDL, preview: bool) -> tuple[int, int]:
    """Output resolution derived from the SOURCE footage so the render keeps the
    clips' real aspect ratio (9:16 or 16:9 — whatever the originals are), never
    a model-guessed frame that would stretch them.

    The largest-area source is the reference frame. Preview scales that down to
    fit a 1280x720 box for speed; final keeps full source resolution.
    """
    srcs = list(dict.fromkeys(seg.src for seg in edl.segments))
    sw, sh = max((_stream_dims(s) for s in srcs), key=lambda wh: wh[0] * wh[1])
    if preview:
        scale = min(1280 / sw, 720 / sh, 1.0)
        sw, sh = round(sw * scale), round(sh * scale)
    return _even(sw), _even(sh)


def _atempo_chain(factor: float) -> str:
    """ffmpeg's atempo only accepts 0.5–2.0; chain steps for anything outside."""
    parts: list[str] = []
    f = factor
    while f > 2.0:
        parts.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        parts.append("atempo=0.5")
        f /= 0.5
    parts.append(f"atempo={f:.6g}")
    return ",".join(parts)


def _out_duration(seg: Segment) -> float:
    """Timeline duration of a segment after its own motion (speed) is applied."""
    motion = seg.motion or {}
    if motion.get("type") == "speed":
        factor = float(motion.get("factor", 1.0))
        if factor > 0:
            return seg.duration / factor
    return seg.duration


def _crossfade(seg: Segment) -> tuple[float, str]:
    """(duration_seconds, xfade_style) for a segment's crossfade INTO it from the
    previous segment. (0.0, ...) means a hard cut. Style is any ffmpeg xfade
    transition name (fade, wipeleft, slideup, dissolve, ...)."""
    t = seg.transition or {}
    c = t.get("crossfade")
    if not c:
        return 0.0, "fade"
    return float(c), str(t.get("crossfade_style", "fade"))


def _segment_filters(
    seg: Segment, tw: int, th: int, fps: float
) -> tuple[str, str | None]:
    """Per-segment (video_filter, audio_filter|None).

    Base = aspect-preserving scale+pad (never stretches; letterbox a mismatched
    AR). Layered on top, all opt-in and ffmpeg-only:
      - motion ken_burns: slow zoompan push/pull
      - motion speed: setpts + tempo-matched audio (>1 faster, <1 slow-mo)
      - transition fade_in/fade_out: matched video + audio fades
    A segment with no motion/transition yields exactly the base filter.
    """
    vparts = [
        f"scale={tw}:{th}:force_original_aspect_ratio=decrease",
        f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black",
    ]
    aparts: list[str] = []
    out_dur = _out_duration(seg)

    motion = seg.motion or {}
    mtype = motion.get("type")
    if mtype == "ken_burns":
        zoom = float(motion.get("zoom", 1.12))
        n = max(1, round(seg.duration * fps))
        if motion.get("direction") == "out":
            z = f"max({zoom}-({zoom}-1)*on/{n},1)"
        else:
            z = f"min(1+({zoom}-1)*on/{n},{zoom})"
        vparts.append(
            f"zoompan=z='{z}':d=1:"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={tw}x{th}:fps={fps:.6g}"
        )
    elif mtype == "speed":
        factor = float(motion.get("factor", 1.0))
        if factor <= 0:
            raise RenderError(f"speed factor must be > 0, got {factor}")
        vparts.append(f"setpts={1.0 / factor:.6g}*PTS")
        aparts.append(_atempo_chain(factor))

    transition = seg.transition or {}
    fin = transition.get("fade_in")
    fout = transition.get("fade_out")
    if fin:
        vparts.append(f"fade=t=in:st=0:d={float(fin):.6g}")
        aparts.append(f"afade=t=in:st=0:d={float(fin):.6g}")
    if fout:
        st = max(0.0, out_dur - float(fout))
        vparts.append(f"fade=t=out:st={st:.6g}:d={float(fout):.6g}")
        aparts.append(f"afade=t=out:st={st:.6g}:d={float(fout):.6g}")

    return ",".join(vparts), (",".join(aparts) if aparts else None)


def _render_with_transitions(
    edl: EDL, out: str, tw: int, th: int, fps: float, venc: list[str]
) -> str:
    """Single-graph render for edits that use crossfades. Segments overlap in
    time, so they can't be encoded independently and concat-copied — the whole
    timeline is built in one filter_complex (xfade for video, acrossfade for
    audio; hard-cut boundaries use concat). Per-segment motion/fades still apply
    to each input before it joins the graph.
    """
    segs = edl.segments
    inputs: list[str] = []
    for seg in segs:
        inputs += ["-ss", str(seg.in_), "-t", str(seg.duration), "-i", seg.src]

    chains: list[str] = []
    for j, seg in enumerate(segs):
        vf, af = _segment_filters(seg, tw, th, fps)
        # normalize fps/format/SAR so xfade & concat see uniform inputs
        chains.append(f"[{j}:v]{vf},fps={fps:.6g},format=yuv420p,setsar=1[v{j}]")
        a = f"[{j}:a]" + (f"{af}," if af else "")
        chains.append(a + f"aformat=channel_layouts=stereo:sample_rates=48000[a{j}]")

    cur_v, cur_a = "[v0]", "[a0]"
    timeline = _out_duration(segs[0])
    prev_d = timeline
    for j in range(1, len(segs)):
        d = _out_duration(segs[j])
        c, style = _crossfade(segs[j])
        # clamp overlap so it never exceeds either clip (no negative offset)
        c = min(c, prev_d * 0.9, d * 0.9) if c > 0 else 0.0
        if c > 0:
            offset = timeline - c
            nv, na = f"[vx{j}]", f"[ax{j}]"
            chains.append(
                f"{cur_v}[v{j}]xfade=transition={style}:"
                f"duration={c:.6g}:offset={offset:.6g}{nv}"
            )
            chains.append(f"{cur_a}[a{j}]acrossfade=d={c:.6g}{na}")
            timeline += d - c
        else:
            nv, na = f"[vc{j}]", f"[ac{j}]"
            chains.append(f"{cur_v}[v{j}]concat=n=2:v=1:a=0{nv}")
            chains.append(f"{cur_a}[a{j}]concat=n=2:v=0:a=1{na}")
            timeline += d
        cur_v, cur_a = nv, na
        prev_d = d

    _run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(chains),
        "-map", cur_v, "-map", cur_a,
        *venc, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "2", "-ar", "48000",
        out,
    ])
    return out


def render_edl(edl: EDL, out: str, preview: bool = False) -> str:
    fps = edl.fps
    tw, th = _target_resolution(edl, preview)
    # Preview trades quality for speed; final encodes near-visually-lossless so
    # the output matches the source clips.
    if preview:
        venc = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28"]
    else:
        venc = ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
    # Crossfades need overlapping segments -> single filter_complex graph.
    # Hard-cut-only edits keep the proven encode-per-part + concat-copy path.
    if any(_crossfade(s)[0] > 0 for s in edl.segments[1:]):
        return _render_with_transitions(edl, out, tw, th, fps, venc)
    tmp = tempfile.mkdtemp(prefix="editor_cli_render_")
    parts: list[str] = []
    for i, seg in enumerate(edl.segments):
        part = os.path.join(tmp, f"part{i:04d}.mp4")
        vf, af = _segment_filters(seg, tw, th, fps)
        cmd = [
            # input-side trim so motion filters (e.g. speed/setpts) re-time the
            # output freely instead of -t clamping it as an output limit
            "ffmpeg", "-y",
            "-ss", str(seg.in_), "-t", str(seg.duration), "-i", seg.src,
            "-vf", vf, "-r", str(fps),
            *venc, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2", "-ar", "48000",
        ]
        if af:
            cmd += ["-af", af]
        cmd.append(part)
        _run(cmd)
        parts.append(part)
    list_file = os.path.join(tmp, "concat.txt")
    with open(list_file, "w") as fh:
        for p in parts:
            fh.write(f"file '{p}'\n")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out])
    return out


def overlay_onto(
    base: str,
    overlay: str,
    out: str,
    *,
    x: str = "0",
    y: str = "0",
    start: float = 0.0,
    preview: bool = False,
) -> str:
    """Composite an (alpha) overlay clip onto base footage.

    The overlay is delayed to appear at ``start`` seconds (transparent before
    that) and removed when it ends, with the base continuing underneath. Base
    audio is preserved. This is how HyperFrames-rendered motion graphics (see
    editor_cli.render.overlays) get laid onto footage — ffmpeg-only, MIT.
    """
    venc = (
        ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28"]
        if preview
        else ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
    )
    # delay the overlay's own timeline to `start`; format=auto keeps alpha;
    # eof_action=pass lets the base run on after the overlay ends.
    fc = (
        f"[1:v]setpts=PTS+{start:.6g}/TB[ov];"
        f"[0:v][ov]overlay={x}:{y}:eof_action=pass:format=auto[v]"
    )
    _run([
        "ffmpeg", "-y", "-i", base, "-i", overlay,
        "-filter_complex", fc,
        "-map", "[v]", "-map", "0:a?",
        *venc, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "2", "-ar", "48000",
        out,
    ])
    return out
