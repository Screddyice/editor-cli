#!/usr/bin/env python3
"""Run a disposable, rendered Final Cut 12.3 controller acceptance test."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fcpxml.parser import FCPXMLParser
from PIL import Image, ImageDraw, ImageFont

from editor_cli.adapters.fcpxml_mcp import FCPXMLMCPClient
from editor_cli.config import ControllerConfig
from editor_cli.mcp_server import device_report
from editor_cli.services import build_services
from editor_cli.session.models import EditOperation, EditProgram, EditRequest
from editor_cli.verification.review import ReviewReport
from editor_cli.verification.technical import inspect_preview

EXPECTED_CHECKS = (
    "source_unchanged",
    "gap_removed",
    "title_visible",
    "transition_visible",
    "reaction_insert_visible",
    "preview_rendered",
    "preview_watched",
)


@dataclass(frozen=True)
class CanaryWorkspace:
    root: Path
    source: Path
    sessions: Path
    library: Path
    result_path: Path


def create_canary_workspace(root: Path) -> CanaryWorkspace:
    root = root.expanduser().resolve()
    if root.exists():
        raise FileExistsError(f"Canary workspace already exists: {root}")
    source = root / "source"
    sessions = root / "sessions"
    source.mkdir(mode=0o700, parents=True)
    sessions.mkdir(mode=0o700)
    return CanaryWorkspace(
        root=root,
        source=source,
        sessions=sessions,
        library=root / "Editor CLI Canary.fcpbundle",
        result_path=root / "result.json",
    )


def hash_tree(root: Path) -> str:
    """Return a stable digest for every path and byte in a generated source tree."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Canary source tree is not a directory: {root}")
    digest = hashlib.sha256()
    for path in sorted(
        (root, *root.rglob("*")), key=lambda item: item.relative_to(root).as_posix()
    ):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"Canary source tree cannot contain symlinks: {relative}")
        if path.is_dir():
            digest.update(f"directory\\0{relative}\\0".encode())
            continue
        if not path.is_file():
            raise ValueError(
                f"Canary source tree contains an unsupported path: {relative}"
            )
        digest.update(f"file\\0{relative}\\0".encode())
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def _run(args: list[str], timeout: int = 180) -> None:
    completed = subprocess.run(
        args, capture_output=True, text=True, check=False, timeout=timeout
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        raise RuntimeError(detail[-1] if detail else f"Command failed: {args[0]}")


def _render_card(
    destination: Path,
    *,
    color: str,
    label: str,
    duration: float,
    tone: int | None,
) -> None:
    font = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    caption = destination.with_suffix(".caption.png")
    image = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    text_font = ImageFont.truetype(font, size=96)
    bounds = draw.textbbox((0, 0), label, font=text_font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    x = (1920 - width) // 2
    y = (1080 - height) // 2
    draw.rounded_rectangle(
        (x - 48, y - 32, x + width + 48, y + height + 32),
        radius=24,
        fill=(0, 0, 0, 160),
    )
    draw.text((x, y), label, font=text_font, fill="white")
    image.save(caption)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=1920x1080:r=30:d={duration}",
        "-loop",
        "1",
        "-framerate",
        "30",
        "-i",
        str(caption),
    ]
    if tone is not None:
        command += [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={tone}:sample_rate=48000:duration={duration}",
        ]
    command += [
        "-filter_complex",
        "[0:v][1:v]overlay=shortest=1[video]",
        "-map",
        "[video]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
    ]
    if tone is None:
        command += ["-an"]
    else:
        command += ["-map", "2:a", "-c:a", "aac", "-shortest"]
    command.append(str(destination))
    try:
        _run(command)
    finally:
        caption.unlink(missing_ok=True)


def _asset(
    parent: ET.Element,
    asset_id: str,
    name: str,
    media: Path,
    duration: str,
    *,
    audio: bool,
) -> None:
    asset = ET.SubElement(
        parent,
        "asset",
        id=asset_id,
        name=name,
        start="0s",
        duration=duration,
        hasVideo="1",
        hasAudio="1" if audio else "0",
        format="r1",
    )
    ET.SubElement(asset, "media-rep", kind="original-media", src=media.as_uri())


def create_source(workspace: CanaryWorkspace) -> Path:
    red = workspace.source / "red.mp4"
    blue = workspace.source / "blue.mp4"
    title = workspace.source / "title.mp4"
    reaction = workspace.source / "reaction.mp4"
    _render_card(red, color="red", label="SOURCE A", duration=3, tone=440)
    _render_card(blue, color="blue", label="SOURCE B", duration=5, tone=660)
    _render_card(
        title, color="0x111111", label="EDITOR CLI CANARY", duration=2, tone=None
    )
    _render_card(reaction, color="purple", label="REACTION!", duration=1, tone=None)

    root = ET.Element("fcpxml", version="1.11")
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources,
        "format",
        id="r1",
        name="FFVideoFormat1080p30",
        frameDuration="1/30s",
        width="1920",
        height="1080",
    )
    _asset(resources, "r2", "Red", red, "3s", audio=True)
    _asset(resources, "r3", "Blue", blue, "5s", audio=True)
    _asset(resources, "r4", "Canary Title", title, "2s", audio=False)
    _asset(resources, "r5", "Canary Reaction", reaction, "1s", audio=False)
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="Canary Event")
    project = ET.SubElement(event, "project", name="Editor CLI Canary Source")
    sequence = ET.SubElement(project, "sequence", format="r1", duration="8s")
    spine = ET.SubElement(sequence, "spine")
    ET.SubElement(
        spine,
        "asset-clip",
        name="Red",
        ref="r2",
        offset="0s",
        start="0s",
        duration="2s",
    )
    ET.SubElement(
        spine,
        "gap",
        name="One Second Gap",
        offset="2s",
        duration="1s",
    )
    ET.SubElement(
        spine,
        "asset-clip",
        name="Blue",
        ref="r3",
        offset="3s",
        start="0s",
        duration="5s",
    )
    source_xml = workspace.source / "canary-source.fcpxml"
    ET.ElementTree(root).write(source_xml, encoding="utf-8", xml_declaration=True)
    validate_fcpxml(source_xml)
    return source_xml


def validate_fcpxml(path: Path) -> None:
    """Reject source and candidate XML that the pinned FCPXML parser cannot read."""
    parsed = FCPXMLParser().parse_file(str(path))
    if len(parsed.timelines) != 1:
        raise RuntimeError("Canary FCPXML must contain exactly one timeline")
    timeline = parsed.timelines[0]
    if timeline.duration.seconds != 8.0:
        raise RuntimeError("Canary FCPXML must have an eight-second timeline")


def canary_program() -> EditProgram:
    return EditProgram(
        operations=(
            EditOperation("edit", "fill_gaps", {"mode": "extend_previous"}),
            EditOperation(
                "edit",
                "add_transition",
                {
                    "clip_id": "Red",
                    "position": "end",
                    "transition_type": "cross-dissolve",
                    "duration": "15/30s",
                },
            ),
            EditOperation(
                "edit",
                "add_connected_clip",
                {
                    "parent_clip_id": "Red",
                    "asset_id": "r4",
                    "offset": "1s",
                    "duration": "2s",
                    "lane": 1,
                },
            ),
            EditOperation(
                "edit",
                "add_connected_clip",
                {
                    "parent_clip_id": "Blue",
                    "asset_id": "r5",
                    "offset": "5s",
                    "duration": "1s",
                    "lane": 2,
                },
            ),
        ),
        changed_ranges=((1.0, 3.0), (5.0, 6.0)),
    )


async def _wait_for_project(control, library: Path, timeout_seconds: int = 60) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    error: Exception | None = None
    expected_library = library.resolve()
    while asyncio.get_running_loop().time() < deadline:
        try:
            projects = await control.active_projects()
            active_library = getattr(control, "_active_library_path", None)
            if (
                len(projects) == 1
                and projects[0].project == "Editor CLI Canary Source"
                and active_library == expected_library
            ):
                return
        except Exception as exc:  # noqa: BLE001 - any bridge failure blocks the canary
            error = exc
        await asyncio.sleep(1)
    raise RuntimeError(f"Final Cut did not open the canary project: {error}")


async def run_canary(
    workspace: CanaryWorkspace, report: dict, source_xml: Path, program: EditProgram
) -> dict:
    config = ControllerConfig(session_root=workspace.sessions)
    services = build_services(config, doctor=lambda: report)
    controller = services.session.controller

    bootstrap = FCPXMLMCPClient(
        config.fcpxml_command,
        journal_root=workspace.root / "bootstrap-journal",
        allowed_roots=(workspace.root,),
    )
    await bootstrap.call(
        "deliver",
        {
            "action": "push_to_fcp",
            "args": {
                "filepath": str(source_xml),
                "library_location": str(workspace.library),
                "suppress_warnings": True,
                "copy_assets": False,
                "confirm_unreviewed": True,
            },
        },
    )
    await _wait_for_project(controller.deps.fcp, workspace.library)

    session = await controller.start(
        EditRequest(
            "Remove the one-second gap, add the canary title, cross-dissolve, "
            "and reaction card."
        )
    )
    candidate = await controller.apply(session.id, program)
    validate_fcpxml(candidate.fcpxml_path)
    tree = ET.parse(candidate.fcpxml_path)
    connected_refs = {
        item.get("ref")
        for item in tree.getroot().findall(".//asset-clip")
        if item.get("lane")
    }
    technical = inspect_preview(
        candidate.preview_path,
        expected_duration=8.0,
        fcpxml_qc=True,
        allow_black=False,
        allow_silence=False,
        expected_audio=True,
    )
    manifest = json.loads(candidate.evidence_manifest.read_text(encoding="utf-8"))
    required = {
        "source_unchanged": False,
        "gap_removed": not tree.getroot().findall(".//gap"),
        "title_visible": "r4" in connected_refs and technical.verified,
        "transition_visible": bool(tree.getroot().findall(".//transition"))
        and technical.verified,
        "reaction_insert_visible": "r5" in connected_refs and technical.verified,
        "preview_rendered": technical.verified,
        "preview_watched": bool(manifest.get("frames")),
    }
    result = await controller.record_review(
        session.id,
        candidate.number,
        ReviewReport(
            required=required,
            observations=technical.observations,
            changed_ranges=program.changed_ranges,
        ),
    )
    return {
        "session_id": session.id,
        "state": result.state.value,
        "passes": result.passes,
        "preview_sha256": manifest["preview"]["sha256"],
        "preview_path": str(candidate.preview_path),
        "evidence_manifest": str(candidate.evidence_manifest),
        "required_checks": required,
        "final_export": "user",
    }


def require_device() -> dict:
    report = device_report()
    version = report["final_cut"].get("version")
    if version != "12.3":
        raise RuntimeError(f"Final Cut Pro 12.3 is required; found {version or 'none'}")
    if not report["ready"]:
        raise RuntimeError("Run editor-cli doctor and resolve failed checks first")
    return report


def write_result(path: Path, result: dict) -> None:
    path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _all_required_checks_pass(result: dict) -> bool:
    checks = result.get("required_checks")
    return (
        isinstance(checks, dict)
        and set(checks) == set(EXPECTED_CHECKS)
        and all(checks.values())
        and result.get("state") == "ready"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("canary-output")
        / datetime.now(timezone.utc).strftime("final-cut-12.3-%Y%m%d-%H%M%S"),
    )
    args = parser.parse_args(argv)
    try:
        report = require_device()
        workspace = create_canary_workspace(args.workspace)
        source_xml = create_source(workspace)
        program = canary_program()
        program.validate_for({"duration_seconds": 8.0})
        source_hashes = hash_tree(workspace.source)
        result = asyncio.run(run_canary(workspace, report, source_xml, program))
        result["required_checks"]["source_unchanged"] = source_hashes == hash_tree(
            workspace.source
        )
        result["source_tree_sha256"] = source_hashes
        write_result(workspace.result_path, result)
    except Exception as exc:  # noqa: BLE001 - any failure must fail the live canary
        print(f"Canary failed: {exc}", file=sys.stderr)
        return 1
    if not _all_required_checks_pass(result):
        print(f"Canary checks failed; inspect {workspace.result_path}", file=sys.stderr)
        return 1
    print(workspace.result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
