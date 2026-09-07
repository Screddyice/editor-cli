"""Bounded FFmpeg rendering of path-free, validated edit decisions.

Only session snapshots are inputs. Filter expressions and concat filenames are
generated here, never accepted from the plan. Pillow supplies the text layer on
builds without libass as well as builds with it, keeping both paths identical.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .models import EditPlan


class RenderError(RuntimeError):
    """A render failed; its attempt directory remains available for diagnosis."""


def _run(args: list[str], directory: Path) -> None:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
             "-threads", "2", "-filter_complex_threads", "1", *args],
            cwd=directory, capture_output=True, text=True, timeout=7200,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderError(f"FFmpeg could not finish: {exc}") from exc
    if result.returncode:
        raise RenderError(f"FFmpeg failed: {result.stderr[-6000:]}")


def _input(path: Path) -> list[str]:
    # No network, concat:, subfile:, or protocol chains in input filenames.
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("Render inputs must be regular absolute snapshot paths")
    return ["-protocol_whitelist", "file", "-format_whitelist",
            "mov,matroska,mp3,wav,flac,aac,ogg,image2,png_pipe,jpeg_pipe,webp_pipe",
            "-i", str(path)]


def _video(preview: bool) -> list[str]:
    return ["-c:v", "libx264", "-preset", "ultrafast" if preview else "fast",
            "-crf", "25" if preview else "20", "-pix_fmt", "yuv420p"]


def _fit(width: int, height: int, fps: int) -> str:
    return (f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
            f"force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={fps}")


def _tempo(speed: float) -> str:
    factors = []
    while speed > 2:
        factors.append("atempo=2")
        speed /= 2
    while speed < 0.5:
        factors.append("atempo=0.5")
        speed /= 0.5
    return ",".join([*factors, f"atempo={speed:.9f}"])


def _has_audio(asset: dict) -> bool:
    return any(s.get("codec_type") == "audio"
               for s in asset.get("metadata", {}).get("streams", []))


def _caption_events(plan: dict, transcripts: dict) -> list[dict]:
    events: list[dict] = []
    offset = 0.0
    for cut in plan["cuts"]:
        if cut["volume"] > 0:
            for word in transcripts.get(cut["asset_id"], {}).get("words", []):
                if word.get("type", "word") != "word":
                    continue
                try:
                    start, end = float(word["start"]), float(word["end"])
                except (KeyError, TypeError, ValueError):
                    raise ValueError("Caption words require numeric start/end timings") from None
                if not math.isfinite(start) or not math.isfinite(end) or end < start:
                    raise ValueError("Caption word timing is invalid")
                start, end = max(start, cut["start"]), min(end, cut["end"])
                text = str(word.get("text", "")).strip()
                if start < end and text:
                    if len(text) > 1000:
                        raise ValueError("Caption word text exceeds 1000 characters")
                    events.append({"text": text, "start": offset + (start-cut["start"])/cut["speed"],
                                   "duration": (end-start)/cut["speed"], "position": "bottom"})
        offset += (cut["end"]-cut["start"])/cut["speed"]
    if len(events) > 20000:
        raise ValueError("A render supports at most 20000 caption words")
    return events


def _font(size: int):
    for path in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow 10.0 predates the scalable bundled default font.
        return ImageFont.load_default()


def _draw_text(canvas: Image.Image, event: dict, caption: bool = False) -> None:
    width, height = canvas.size
    draw = ImageDraw.Draw(canvas)
    size = max(12, round(min(width, height) * (0.055 if caption else 0.07)))
    # Wrap, then shrink exceptionally long text to remain inside the frame.
    while True:
        font = _font(size)
        lines, line = [], ""
        for character in event["text"]:
            candidate = line + character
            if character == "\n" or draw.textlength(candidate, font=font) > width * 0.88:
                lines.append(line)
                line = "" if character == "\n" else character
            else:
                line = candidate
        lines.append(line)
        text = "\n".join(lines)
        bounds = draw.multiline_textbbox((0, 0), text, font=font, spacing=4, stroke_width=2)
        if bounds[3]-bounds[1] <= height * 0.55:
            break
        if size <= 4:
            raise ValueError("Text is too long to fit the selected output dimensions")
        size -= 1
    text_height = bounds[3] - bounds[1]
    top = {"top": height * 0.09, "center": (height-text_height)/2,
           "bottom": height * (0.76 if caption else 0.88)-text_height}[event["position"]]
    draw.multiline_text(((width-(bounds[2]-bounds[0]))/2, top-bounds[1]), text,
                        font=font, fill="white", stroke_width=2, stroke_fill="black",
                        spacing=4, align="center")


def _text_layer(titles: list[dict], captions: list[dict], duration: float,
                width: int, height: int, fps: int, directory: Path) -> Path:
    # Sweep boundaries once; no per-frame Python rendering or giant filter graph.
    total_frames = math.ceil(duration * fps)
    changes: dict[int, list[tuple[int, bool]]] = {0: [], total_frames: []}
    events = [(event, False) for event in titles] + [(event, True) for event in captions]
    for index, (event, _) in enumerate(events):
        start = max(0, math.ceil(event["start"] * fps - 1e-7))
        end = min(total_frames, math.ceil((event["start"] + event["duration"]) * fps - 1e-7))
        if end > start:
            changes.setdefault(start, []).append((index, True))
            changes.setdefault(end, []).append((index, False))
    active: set[int] = set()
    boundaries = sorted(changes)
    manifest = ["ffconcat version 1.0"]
    for number, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        for index, add in changes[start]:
            active.add(index) if add else active.discard(index)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for index in sorted(active):
            _draw_text(canvas, *events[index])
        filename = f"text-{number:06d}.png"
        canvas.save(directory / filename)
        # safe=0 is required by concat's option directive. Every name and option
        # is generated above, contains no user text, and stays in this directory.
        manifest += [f"file {filename}", f"option framerate {fps}",
                     f"duration {(end-start)/fps:.9f}"]
    manifest += [f"file {filename}", f"option framerate {fps}"]
    (directory / "text.ffconcat").write_text("\n".join(manifest)+"\n")
    out = directory / "text.mov"
    _run(["-protocol_whitelist", "file", "-f", "concat", "-safe", "0",
          "-i", "text.ffconcat", "-vf", f"fps={fps}", "-t", str(duration),
          "-c:v", "qtrle", "-pix_fmt", "argb", str(out)], directory)
    return out


def render(plan: dict, assets: dict[str, dict], transcripts: dict[str, dict],
           work_dir: Path, preview: bool = False) -> Path:
    """Render into a fresh attempt directory and return its ``render.mp4``.

    Preview preserves requested geometry and timing, using a faster encode.
    Caption words are shown individually at their mapped output timestamps.
    Overlay audio is intentionally excluded; the music bed loops as necessary.
    """
    plan = EditPlan.model_validate(plan).model_dump()
    if not shutil.which("ffmpeg"):
        raise RenderError("ffmpeg is required for direct renders")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="render-", dir=work_dir)).resolve()
    width, height, fps = plan["width"], plan["height"], plan["fps"]
    duration = sum((c["end"]-c["start"])/c["speed"] for c in plan["cuts"])
    segments = []
    for index, cut in enumerate(plan["cuts"]):
        asset = assets[cut["asset_id"]]
        if asset["kind"] not in {"video", "image", "audio"}:
            raise ValueError("Context documents cannot appear on the video timeline")
        length = (cut["end"]-cut["start"])/cut["speed"]
        args = []
        if asset["kind"] == "image":
            args += ["-loop", "1", "-framerate", str(fps)]
        else:
            args += ["-ss", str(cut["start"])]
        args += _input(Path(asset["path"]))
        audio_index = 0
        if not _has_audio(asset):
            args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            audio_index = 1
        video_index = 0
        if asset["kind"] == "audio":
            video_index = 1 if audio_index == 0 else 2
            args += ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}"]
        grade = {"none": "", "neutral": ",eq=contrast=1.03:saturation=0.98",
                 "warm": ",eq=contrast=1.03:saturation=1.05,colorbalance=rs=0.04:bs=-0.03"}[cut["grade"]]
        source_duration = cut["end"]-cut["start"]
        vf = (f"[{video_index}:v]trim=duration={source_duration},setpts=(PTS-STARTPTS)/{cut['speed']},"
              f"{_fit(width,height,fps)}{grade},tpad=stop_mode=clone:stop_duration=1,"
              f"trim=duration={length}[v]")
        af = (f"[{audio_index}:a]atrim=duration={source_duration},asetpts=PTS-STARTPTS,"
              f"aformat=sample_fmts=fltp,{_tempo(cut['speed'])},aresample=48000,aformat=channel_layouts=stereo,"
              f"volume={cut['volume']},apad,atrim=duration={length},"
              + ("alimiter=limit=0.95:level=0:latency=1," if cut["volume"] > 1 else "") +
              f"afade=t=in:d=0.03,afade=t=out:st={max(0,length-0.03)}:d=0.03[a]")
        name = f"segment-{index:04d}.mp4"
        _run([*args, "-filter_complex", vf+";"+af, "-map", "[v]", "-map", "[a]",
              *_video(preview), "-c:a", "aac", "-ar", "48000", "-ac", "2",
              "-video_track_timescale", "90000", "-t", str(length), name], directory)
        segments.append(f"file {name}\nduration {length:.9f}")
    (directory / "segments.ffconcat").write_text("ffconcat version 1.0\n"+"\n".join(segments)+"\n")
    current = directory / "base.mp4"
    _run(["-protocol_whitelist", "file", "-f", "concat", "-safe", "1", "-i",
          "segments.ffconcat", "-c", "copy", "-t", str(duration), str(current)], directory)

    args = _input(current)
    graph: list[str] = []
    video_label = "0:v"
    input_index = 1
    for index, overlay in enumerate(plan["overlays"]):
        asset = assets[overlay["asset_id"]]
        if asset["kind"] not in {"video", "image"}:
            raise ValueError("Overlays require video or image assets")
        args += (["-loop", "1", "-framerate", str(fps)] if asset["kind"] == "image"
                 else ["-ss", str(overlay["source_start"])])
        args += _input(Path(asset["path"]))
        ow, oh = (width, height) if overlay["layout"] == "full" else (width//3//2*2, height//3//2*2)
        start, end = overlay["start"], overlay["start"]+overlay["duration"]
        position = "0:0" if overlay["layout"] == "full" else "W-w-16:16"
        graph.append(f"[{input_index}:v]trim=duration={overlay['duration']},setpts=PTS-STARTPTS,"
                     f"{_fit(ow,oh,fps)},setpts=PTS+{start}/TB[over{index}]")
        graph.append(f"[{video_label}][over{index}]overlay={position}:eof_action=pass:repeatlast=0:"
                     f"enable='gte(t,{start})*lt(t,{end})'[composed{index}]")
        video_label = f"composed{index}"
        input_index += 1

    music = plan["music"]
    if music:
        asset = assets[music["asset_id"]]
        if not _has_audio(asset):
            raise ValueError("Music asset must contain an audio stream")
        args += ["-stream_loop", "-1", *_input(Path(asset["path"]))]
        graph.append(f"[{input_index}:a]aresample=48000,aformat=channel_layouts=stereo,"
                     f"volume={music['volume']},atrim=duration={duration},"
                     f"afade=t=in:d=0.03,afade=t=out:st={max(0,duration-0.03)}:d=0.03[bed]")
        input_index += 1
        if music["duck"]:
            graph.append("[0:a]asplit[voice][side];[bed][side]sidechaincompress="
                         "threshold=0.02:ratio=8:attack=15:release=250[ducked];"
                         "[voice][ducked]amix=inputs=2:duration=first:normalize=0,"
                         "alimiter=limit=0.95:level=0:latency=1[a]")
        else:
            graph.append("[0:a][bed]amix=inputs=2:duration=first:normalize=0,"
                         "alimiter=limit=0.95:level=0:latency=1[a]")

    captions = _caption_events(plan, transcripts) if plan["captions"] else []
    (directory / "captions.json").write_text(json.dumps(captions, ensure_ascii=False, indent=2))
    output = directory / "render.mp4"
    if plan["titles"] or captions:
        layer = _text_layer(plan["titles"], captions, duration, width, height, fps, directory)
        args += _input(layer)
        graph.append(f"[{video_label}][{input_index}:v]overlay=0:0:"
                     "eof_action=pass:repeatlast=0[text_composed]")
        video_label = "text_composed"
    if graph:
        args += ["-filter_complex", ";".join(graph)]
    args += ["-map", "0:v" if video_label == "0:v" else f"[{video_label}]",
             "-map", "[a]" if music else "0:a"]
    args += ["-c:v", "copy"] if video_label == "0:v" else _video(preview)
    args += ["-c:a", "aac" if music else "copy", "-t", str(duration),
             "-movflags", "+faststart", str(output)]
    _run(args, directory)
    return output
