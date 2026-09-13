"""Persisted direct-edit workflow shared by the CLI and agent tools."""

from __future__ import annotations

import fcntl
import json
import math
import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from editor_cli.direct.media import (
    KINDS,
    LOCAL_INPUT,
    digest,
    download,
    duration,
    has_audio,
    plain_path,
    probe,
    publish_file,
    run,
    window,
)
from editor_cli.direct.models import EditPlan


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict) -> None:
    plain_path(path, exists=False)
    temp = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    with temp.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def scribe_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    # Read only the editor's configured credential file, never a footage .env.
    configured = os.environ.get("EDITOR_CLI_ENV_FILE")
    env_path = (
        Path(configured).expanduser()
        if configured
        else Path(__file__).resolve().parents[3] / ".env"
    )
    candidates = (
        [env_path] if configured else [env_path, Path.home() / "projects" / ".env"]
    )
    for candidate in candidates:
        if not key and candidate.is_file():
            for line in candidate.read_text().splitlines():
                name, sep, value = line.partition("=")
                if sep and name.strip() == "ELEVENLABS_API_KEY":
                    key = value.strip().strip("\"'")
                    break
    if not key:
        raise ValueError(
            "Set ELEVENLABS_API_KEY or EDITOR_CLI_ENV_FILE for word transcription"
        )
    return key


def validate_transcript(data: dict, total: float) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("words"), list):
        raise ValueError("Transcription must contain verbatim word timestamps")
    for word in data["words"]:
        if not isinstance(word, dict):
            raise ValueError("Invalid transcript word")
        if word.get("type") == "spacing":
            continue
        a, b = word.get("start"), word.get("end")
        if (
            type(a) not in (int, float)
            or type(b) not in (int, float)
            or not math.isfinite(a)
            or not math.isfinite(b)
            or not 0 <= a <= b <= total + 0.25
        ):
            raise ValueError("Transcript timestamps exceed source bounds")
    return data


def speech_edges(cut: dict, transcript: dict, total: float) -> None:
    words = sorted(
        (w for w in transcript["words"] if w.get("type", "word") == "word"),
        key=lambda w: w["start"],
    )
    a, b = cut["start"], cut["end"]
    if any(
        w["start"] + 0.001 < edge < w["end"] - 0.001 for w in words for edge in (a, b)
    ):
        raise ValueError("A speech cut falls inside a word; use padded word boundaries")
    kept = [w for w in words if w["end"] > a and w["start"] < b]
    if not kept:
        return
    first, last = kept[0], kept[-1]
    # Preserve padding where source/neighboring speech permits it. Larger gaps
    # are allowed for deliberate pauses and complete source clips.
    previous = max((w["end"] for w in words if w["end"] <= first["start"]), default=0)
    following = min(
        (w["start"] for w in words if w["start"] >= last["end"]), default=total
    )
    if a > 0 and first["start"] - previous >= 0.03 and first["start"] - a < 0.029:
        raise ValueError("Add at least 30 ms padding before the first word")
    if b < total and following - last["end"] >= 0.03 and b - last["end"] < 0.029:
        raise ValueError("Add at least 30 ms padding after the last word")


class DirectSession:
    def __init__(self, root: Path):
        self.root = plain_path(root)
        if not self.root.is_dir():
            raise ValueError("Expected a direct session directory")

    @staticmethod
    def doctor() -> dict:
        binaries = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
        try:
            scribe_key()
            transcription = True
        except ValueError:
            transcription = False
        return {
            "engine": "direct",
            "ready": all(binaries.values()),
            "binaries": binaries,
            "transcription_ready": transcription,
            "final_cut_required": False,
        }

    @classmethod
    def start(cls, files: list[str], prompt: str) -> dict:
        if not files or not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Select at least one file and provide an editing prompt")
        if len(files) > 500:
            raise ValueError("Select at most 500 files per session")
        selected = list(dict.fromkeys(plain_path(f) for f in files))
        for path in selected:
            if not path.is_file() or path.suffix.lower() not in KINDS:
                raise ValueError(
                    f"Select a supported regular media or context file: {path}"
                )
            if (
                KINDS[path.suffix.lower()] == "context"
                and path.stat().st_size > 1_000_000
            ):
                raise ValueError("Context documents must be at most 1 MB")
        parent = plain_path(selected[0].parent / "edit", exists=False)
        parent.mkdir(mode=0o700, exist_ok=True)
        root = parent / uuid.uuid4().hex
        root.mkdir(mode=0o700)
        (root / "sources").mkdir(mode=0o700)
        (root / "transcripts").mkdir(mode=0o700)
        state = {
            "version": 1,
            "engine": "direct",
            "id": root.name,
            "created_at": now(),
            "prompt": prompt,
            "state": "inventory",
            "assets": {},
            "strategy": None,
            "attempts": [],
            "plan": None,
            "transcripts": {},
        }
        for path in selected:
            asset_id = "asset_" + uuid.uuid4().hex[:12]
            target = root / "sources" / (asset_id + path.suffix.lower())
            before = digest(path)
            with path.open("rb") as source, target.open("xb") as dest:
                shutil.copyfileobj(source, dest, length=1024 * 1024)
            if digest(target) != before or digest(path) != before:
                raise ValueError(f"Selected file changed while copying: {path.name}")
            kind = KINDS[path.suffix.lower()]
            item = {
                "path": str(target),
                "original": str(path),
                "name": path.name,
                "sha256": before,
                "kind": kind,
            }
            if kind == "context":
                item["context"] = target.read_text(encoding="utf-8")
                item["metadata"] = {}
            else:
                item["metadata"] = probe(target)
                cls._valid_media(item)
            state["assets"][asset_id] = item
        atomic_json(root / "session.json", state)
        (root / "project.md").write_text(
            f"# Direct edit\n\n{prompt}\n", encoding="utf-8"
        )
        return cls(root).status()

    @staticmethod
    def _valid_media(asset: dict) -> None:
        streams = asset["metadata"].get("streams", [])
        if asset["kind"] in ("video", "image") and not any(
            s.get("codec_type") == "video" for s in streams
        ):
            raise ValueError("Selected media has no video/image stream")
        if asset["kind"] == "audio" and not has_audio(asset["metadata"]):
            raise ValueError("Selected audio has no audio stream")
        if asset["kind"] != "image" and duration(asset["metadata"]) <= 0:
            raise ValueError("Media must have a finite positive duration")

    @contextmanager
    def locked(self):
        lock = plain_path(self.root / ".direct.lock", exists=False)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Another process is working on this session") from exc
            state = json.loads(plain_path(self.root / "session.json").read_text())
            if state.get("engine") != "direct" or state.get("id") != self.root.name:
                raise ValueError("Not a valid direct editing session")
            self.check_sources(state)
            yield state
        finally:
            os.close(fd)

    def owned(self, path: str | Path) -> Path:
        result = plain_path(path)
        if not result.is_relative_to(self.root):
            raise ValueError("Artifact is outside the direct session")
        return result

    def check_sources(self, state: dict) -> None:
        for asset in state["assets"].values():
            if digest(self.owned(asset["path"])) != asset["sha256"]:
                raise ValueError("Session source snapshot changed")
            if (
                asset.get("original")
                and digest(Path(asset["original"])) != asset["sha256"]
            ):
                raise ValueError(
                    "A selected original source changed; start a new session"
                )

    def save(self, state: dict) -> None:
        atomic_json(self.root / "session.json", state)

    def status(self) -> dict:
        with self.locked() as state:
            if state["state"] in (
                "review",
                "preview_accepted",
                "final_accepted",
                "finished",
            ):
                self.check_attempt(state["attempts"][-1])
            if state["state"] == "finished":
                if digest(self.owned(state["final_path"])) != state["final_sha256"]:
                    raise ValueError(
                        "Delivered final video changed; cannot resume as finished"
                    )
            return {
                **state,
                "session_dir": str(self.root),
                "next": self.next_action(state),
            }

    @staticmethod
    def next_action(state: dict) -> str:
        return {
            "inventory": "Inspect selected assets and propose a strategy for user approval.",
            "approved": "Submit an edit plan and render a preview.",
            "rendering": "Retry render; unfinished attempt remains recorded.",
            "review": "Inspect the latest render's review windows and record observations.",
            "preview_accepted": "Export the approved plan at final quality.",
            "needs_changes": "Correct the plan and render again within the three-pass limit.",
            "final_accepted": "Finish to return the verified final video.",
            "finished": "Deliver final.mp4 and the edit decisions.",
        }.get(state["state"], "Inspect session.")

    def inspect(
        self, asset_id: str, start: float = 0, end: float | None = None
    ) -> dict:
        with self.locked() as state:
            asset = self.asset(state, asset_id)
            if asset["kind"] == "context":
                return {"asset_id": asset_id, "context": asset["context"]}
            if asset["kind"] == "image":
                return {"asset_id": asset_id, "images": [asset["path"]]}
            end = (
                end if end is not None else min(start + 10, duration(asset["metadata"]))
            )
            result = window(
                self.owned(asset["path"]),
                asset["metadata"],
                start,
                end,
                self.root / "inspect" / uuid.uuid4().hex,
            )
            result["asset_id"] = asset_id
            return result

    @staticmethod
    def asset(state: dict, asset_id: str) -> dict:
        if asset_id not in state["assets"]:
            raise ValueError(f"Unknown asset ID: {asset_id}")
        return state["assets"][asset_id]

    def transcribe(self, asset_id: str) -> dict:
        with self.locked() as state:
            asset = self.asset(state, asset_id)
            cached = state["transcripts"].get(asset_id)
            if cached:
                path = self.owned(cached["path"])
                if digest(path) != cached["sha256"]:
                    raise ValueError("Cached transcript changed")
                return {
                    "asset_id": asset_id,
                    "cached": True,
                    "transcript": json.loads(path.read_text()),
                }
            if not has_audio(asset["metadata"]):
                payload = {"text": "", "words": []}
            else:
                import requests

                key = scribe_key()
                work = self.root / "transcripts" / uuid.uuid4().hex
                work.mkdir(mode=0o700)
                audio = work / "audio.wav"
                run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        *LOCAL_INPUT,
                        "-i",
                        asset["path"],
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        str(audio),
                    ]
                )
                with requests.Session() as client, audio.open("rb") as handle:
                    client.trust_env = False
                    response = client.post(
                        "https://api.elevenlabs.io/v1/speech-to-text",
                        headers={"xi-api-key": key},
                        files={"file": ("audio.wav", handle, "audio/wav")},
                        data={
                            "model_id": "scribe_v1",
                            "diarize": "true",
                            "tag_audio_events": "true",
                            "timestamps_granularity": "word",
                        },
                        timeout=(30, 1800),
                    )
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Word transcription failed: HTTP {response.status_code}"
                    )
                payload = validate_transcript(
                    response.json(), duration(asset["metadata"])
                )
            target = self.root / "transcripts" / f"{asset_id}.json"
            atomic_json(target, payload)
            state["transcripts"][asset_id] = {
                "path": str(target),
                "sha256": digest(target),
            }
            self.save(state)
            return {"asset_id": asset_id, "cached": False, "transcript": payload}

    def approve(self, strategy: str) -> dict:
        if not isinstance(strategy, str) or not strategy.strip():
            raise ValueError("Record the plain-English strategy the user approved")
        with self.locked() as state:
            if state["attempts"]:
                raise ValueError(
                    "Strategy is fixed after the first render; start a new session to change it"
                )
            state.update(
                strategy={"text": strategy, "approved_at": now()}, state="approved"
            )
            self.save(state)
            project = plain_path(self.root / "project.md")
            with project.open("a") as handle:
                handle.write(f"\n## Approved strategy\n\n{strategy}\n")
            return {"state": state["state"], "strategy": state["strategy"]}

    def transcripts(self, state: dict) -> dict:
        result = {}
        for asset_id, cached in state["transcripts"].items():
            path = self.owned(cached["path"])
            if digest(path) != cached["sha256"]:
                raise ValueError("Cached transcript changed")
            result[asset_id] = validate_transcript(
                json.loads(path.read_text()),
                duration(self.asset(state, asset_id)["metadata"]),
            )
        return result

    def validate_plan(self, state: dict, plan: dict) -> tuple[dict, dict]:
        value = EditPlan.model_validate(plan).model_dump()
        transcripts = self.transcripts(state)
        for cut in value["cuts"]:
            asset = self.asset(state, cut["asset_id"])
            if asset["kind"] not in ("video", "image"):
                raise ValueError("Timeline cuts need a video or image asset")
            total = duration(asset["metadata"])
            if asset["kind"] != "image" and cut["end"] > total + 0.001:
                raise ValueError("Cut exceeds source duration")
            if asset["kind"] == "image" and cut["start"] != 0:
                raise ValueError("Still-image cuts start at zero")
            if (
                has_audio(asset["metadata"])
                and cut["volume"] > 0
                and cut["asset_id"] in transcripts
            ):
                speech_edges(cut, transcripts[cut["asset_id"]], total)
        for overlay in value["overlays"]:
            asset = self.asset(state, overlay["asset_id"])
            if asset["kind"] not in ("video", "image"):
                raise ValueError("Overlays need video or image assets")
            if (
                asset["kind"] == "video"
                and overlay["source_start"] + overlay["duration"]
                > duration(asset["metadata"]) + 0.001
            ):
                raise ValueError("Overlay exceeds source duration")
        if value["music"] and not has_audio(
            self.asset(state, value["music"]["asset_id"])["metadata"]
        ):
            raise ValueError("Music asset must contain audio")
        narration = value["narration"]
        if narration:
            asset = self.asset(state, narration["asset_id"])
            if asset["kind"] != "audio" or not has_audio(asset["metadata"]):
                raise ValueError("Narration asset must be an audio file")
            if (
                narration["source_start"] + narration["duration"]
                > duration(asset["metadata"]) + 0.001
            ):
                raise ValueError("Narration exceeds source duration")
            if narration["asset_id"] in transcripts:
                speech_edges(
                    {
                        "start": narration["source_start"],
                        "end": narration["source_start"] + narration["duration"],
                    },
                    transcripts[narration["asset_id"]],
                    duration(asset["metadata"]),
                )
        if value["captions"]:
            needed = {
                cut["asset_id"]
                for cut in value["cuts"]
                if cut["volume"] > 0
                and has_audio(self.asset(state, cut["asset_id"])["metadata"])
            }
            if narration and narration["volume"] > 0:
                needed.add(narration["asset_id"])
            missing = sorted(needed - transcripts.keys())
            if missing:
                raise ValueError(
                    "Captions require word transcripts for: " + ", ".join(missing)
                )
        return value, transcripts

    def render(self, plan: dict | None = None, *, final: bool = False) -> dict:
        from editor_cli.direct.render import render

        with self.locked() as state:
            if not state["strategy"]:
                raise ValueError("Approve a strategy before rendering")
            if state["state"] == "finished":
                raise ValueError(
                    "Session is finished; start a new session for further edits"
                )
            if final:
                if plan is not None:
                    raise ValueError(
                        "Final export uses the reviewed plan; no replacement plan allowed"
                    )
                preview = next(
                    (a for a in reversed(state["attempts"]) if not a["final"]), None
                )
                if (
                    not preview
                    or not preview.get("review", {}).get("passed")
                    or preview["plan"] != state["plan"]
                ):
                    raise ValueError(
                        "Final export requires a passing review of the current preview"
                    )
                self.check_attempt(preview)
                plan = state["plan"]
            else:
                if (
                    sum(
                        not a["final"] and a["status"] == "rendered"
                        for a in state["attempts"]
                    )
                    >= 3
                ):
                    raise ValueError(
                        "Three preview passes reached; report remaining issues"
                    )
                if plan is None:
                    raise ValueError("Provide an edit plan")
            value, transcripts = self.validate_plan(state, plan)
            attempt_id = uuid.uuid4().hex
            work = self.root / "renders" / attempt_id
            plain_path(work, exists=False).mkdir(mode=0o700, parents=True)
            attempt = {
                "id": attempt_id,
                "final": final,
                "status": "rendering",
                "plan": value,
                "created_at": now(),
            }
            audible = {
                cut["asset_id"]
                for cut in value["cuts"]
                if cut["volume"] > 0
                and has_audio(self.asset(state, cut["asset_id"])["metadata"])
            }
            if value["narration"] and value["narration"]["volume"] > 0:
                audible.add(value["narration"]["asset_id"])
            missing = sorted(audible - transcripts.keys())
            attempt["word_timing_checks"] = {
                "checked_assets": sorted(audible & transcripts.keys()),
                "unchecked_assets": missing,
                "limitation": (
                    "No word transcript was available for these audible assets; "
                    "the review cannot confirm word-safe cuts or word timing: "
                    + ", ".join(missing)
                )
                if missing
                else None,
            }
            state["attempts"].append(attempt)
            state.update(plan=value, state="rendering")
            self.save(state)
            try:
                path = self.owned(
                    render(value, state["assets"], transcripts, work, preview=not final)
                )
                metadata = probe(path)
                expected = sum(
                    (c["end"] - c["start"]) / c["speed"] for c in value["cuts"]
                )
                actual = duration(metadata)
                if abs(actual - expected) > max(
                    0.15, len(value["cuts"]) / value["fps"]
                ):
                    raise ValueError(
                        f"Render duration {actual:.3f}s differs from plan {expected:.3f}s"
                    )
                if not has_audio(metadata):
                    raise ValueError("Rendered video has no audio stream")
                # Decode the entire output, not just the container headers.
                run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-xerror",
                        *LOCAL_INPUT,
                        "-i",
                        str(path),
                        "-f",
                        "null",
                        "-",
                    ],
                    timeout=7200,
                )
                self.check_sources(state)
                attempt.update(
                    status="rendered",
                    path=str(path),
                    sha256=digest(path),
                    metadata=metadata,
                    expected_duration=expected,
                )
                attempt["windows"] = self.review_windows(value, path, metadata, work)
                state["state"] = "review"
                self.save(state)
                return attempt
            except Exception as exc:
                attempt.update(status="failed", error=str(exc))
                state["state"] = "needs_changes"
                self.save(state)
                raise

    def review_windows(
        self, plan: dict, path: Path, metadata: dict, work: Path
    ) -> list[dict]:
        total = duration(metadata)
        centers = {0.0, total, total / 3, total / 2, total * 2 / 3}
        offset = 0.0
        for cut in plan["cuts"]:
            offset += (cut["end"] - cut["start"]) / cut["speed"]
            centers.add(min(offset, total))
        for overlay in plan["overlays"] + plan["titles"]:
            centers.update((overlay["start"], overlay["start"] + overlay["duration"]))
        if plan["narration"]:
            centers.update(
                (
                    plan["narration"]["start"],
                    plan["narration"]["start"] + plan["narration"]["duration"],
                )
            )
        # Long uninterrupted takes need coverage beyond three midpoint samples.
        centers.update(float(t) for t in range(30, int(total), 30))
        windows = []
        for i, center in enumerate(sorted(centers)):
            start, end = max(0, center - 1.5), min(total, center + 1.5)
            if end - start < 0.1:
                start, end = max(0, total - 1.5), total
            item = window(path, metadata, start, end, work / "review" / str(i))
            item["id"] = f"window_{i}"
            windows.append(item)
        return windows

    def check_attempt(self, attempt: dict) -> None:
        if digest(self.owned(attempt["path"])) != attempt["sha256"]:
            raise ValueError("Rendered video changed; review is stale")
        for item in attempt["windows"]:
            for path, expected in item["sha256"].items():
                if digest(self.owned(path)) != expected:
                    raise ValueError("Review evidence changed")

    def review(self, report: dict) -> dict:
        if not isinstance(report, dict) or set(report) != {
            "render_id",
            "sha256",
            "windows",
            "summary",
        }:
            raise ValueError("Review requires render_id, sha256, windows, summary")
        with self.locked() as state:
            if not state["attempts"]:
                raise ValueError("Render a preview before reviewing")
            attempt = state["attempts"][-1]
            if (
                attempt["status"] != "rendered"
                or report["render_id"] != attempt["id"]
                or report["sha256"] != attempt["sha256"]
            ):
                raise ValueError("Review does not identify the latest render")
            self.check_attempt(attempt)
            required = {w["id"] for w in attempt["windows"]}
            reviews = report["windows"]
            if not isinstance(reviews, list) or len(reviews) != len(required):
                raise ValueError("Review must cover every required window exactly once")
            seen = set()
            for item in reviews:
                if (
                    not isinstance(item, dict)
                    or set(item) != {"id", "visual", "audio", "passed"}
                    or item["id"] not in required
                    or item["id"] in seen
                    or type(item["passed"]) is not bool
                    or not all(
                        isinstance(item[k], str) and item[k].strip()
                        for k in ("visual", "audio")
                    )
                ):
                    raise ValueError(
                        "Each window needs its ID, visual/audio observations, and passed boolean"
                    )
                seen.add(item["id"])
            if not isinstance(report["summary"], str) or not report["summary"].strip():
                raise ValueError("Review requires a narrative/pacing summary")
            passed = all(w["passed"] for w in reviews)
            attempt["review"] = {
                **report,
                "passed": passed,
                "recorded_at": now(),
                "method": "agent_attestation",
            }
            state["state"] = (
                ("final_accepted" if attempt["final"] else "preview_accepted")
                if passed
                else "needs_changes"
            )
            self.save(state)
            return {
                "state": state["state"],
                "passed": passed,
                "next": self.next_action(state),
            }

    def finish(self) -> dict:
        with self.locked() as state:
            attempt = state["attempts"][-1] if state["attempts"] else {}
            if (
                state["state"] not in ("final_accepted", "finished")
                or not attempt.get("final")
                or not attempt.get("review", {}).get("passed")
            ):
                raise ValueError(
                    "Finish requires a passing review of the final-quality render"
                )
            self.check_attempt(attempt)
            target = plain_path(self.root / "final.mp4", exists=False)
            if target.exists():
                if digest(target) != attempt["sha256"]:
                    raise ValueError("An unrelated final.mp4 already exists")
            else:
                staging = self.root / f".final-{uuid.uuid4().hex}.mp4"
                with (
                    self.owned(attempt["path"]).open("rb") as source,
                    staging.open("xb") as dest,
                ):
                    shutil.copyfileobj(source, dest)
                    dest.flush()
                    os.fsync(dest.fileno())
                if digest(staging) != attempt["sha256"]:
                    raise ValueError("Final render changed during delivery copy")
                # Atomic, no-overwrite publication. An interrupted copy leaves
                # only an attempt-specific staging file and can be retried.
                publish_file(staging, target)
            atomic_json(self.root / "edl.json", state["plan"])
            state.update(
                state="finished", final_path=str(target), final_sha256=digest(target)
            )
            self.save(state)
            project = plain_path(self.root / "project.md")
            if "## Delivered" not in project.read_text():
                with project.open("a") as handle:
                    handle.write(
                        "\n## Delivered\n\n" + attempt["review"]["summary"] + "\n\n"
                    )
                    for cut in state["plan"]["cuts"]:
                        handle.write(
                            f"- {cut['asset_id']} {cut['start']:.3f}–{cut['end']:.3f}: {cut['reason']}\n"
                        )
            return {
                "state": "finished",
                "video": str(target),
                "sha256": state["final_sha256"],
                "decisions": str(self.root / "edl.json"),
                "notes": str(project),
            }

    def acquire(self, url: str, purpose: str) -> dict:
        if not isinstance(purpose, str) or not purpose.strip():
            raise ValueError("Internet media requires an editing purpose")
        with self.locked() as state:
            asset_id = "asset_" + uuid.uuid4().hex[:12]
            record = download(url, self.root / "sources" / asset_id)
            path = self.owned(record["path"])
            asset = {
                **record,
                "original": None,
                "name": path.name,
                "kind": KINDS[path.suffix],
                "purpose": purpose,
                "retrieved_at": now(),
                "metadata": probe(path),
            }
            self._valid_media(asset)
            state["assets"][asset_id] = asset
            self.save(state)
            return {"asset_id": asset_id, **asset}


def dispatch(action: str, session_dir: str | None = None, **kwargs) -> dict:
    arguments = {k: v for k, v in kwargs.items() if v is not None}
    if action == "doctor":
        if arguments or session_dir:
            raise ValueError("Doctor takes no session or arguments")
        return DirectSession.doctor()
    if action == "start":
        if session_dir:
            raise ValueError("Start creates its own session directory")
        return DirectSession.start(**arguments)
    if not session_dir:
        raise ValueError("Provide session_dir from the start result")
    session = DirectSession(Path(session_dir))
    methods = {
        "status": session.status,
        "resume": session.status,
        "inspect": session.inspect,
        "transcribe": session.transcribe,
        "approve": session.approve,
        "render": session.render,
        "export": lambda: session.render(final=True),
        "review": session.review,
        "finish": session.finish,
        "acquire": session.acquire,
    }
    if action not in methods:
        raise ValueError(f"Unknown direct action: {action}")
    try:
        return methods[action](**arguments)
    except TypeError as exc:
        raise ValueError(f"Invalid arguments for direct {action}") from exc
