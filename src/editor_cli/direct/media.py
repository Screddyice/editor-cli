"""Bounded local media inspection and public HTTPS downloads."""

from __future__ import annotations

import hashlib
import ctypes
import http.client
import ipaddress
import json
import math
import os
import socket
import ssl
import stat
import subprocess
import sys
import array
import wave
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from PIL import Image, ImageDraw


KINDS = {
    **dict.fromkeys((".mp4", ".mov", ".mkv", ".webm", ".m4v"), "video"),
    **dict.fromkeys((".png", ".jpg", ".jpeg", ".webp"), "image"),
    **dict.fromkeys((".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"), "audio"),
    **dict.fromkeys((".txt", ".md", ".srt"), "context"),
}
LOCAL_INPUT = ["-protocol_whitelist", "file,pipe", "-format_whitelist",
               "mov,matroska,mp3,wav,flac,aac,ogg,image2,png_pipe,jpeg_pipe,webp_pipe"]


def publish_file(staging: Path, target: Path) -> None:
    """Atomic no-replace rename, including macOS removable filesystems."""
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(os.fsencode(staging), os.fsencode(target), 0x00000004):  # RENAME_EXCL
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number), str(target))
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(staging), -100, os.fsencode(target), 1):  # RENAME_NOREPLACE
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number), str(target))
    elif os.name == "nt":
        os.rename(staging, target)  # Windows rename fails if the destination exists.
    else:
        os.link(staging, target)
        staging.unlink()


def plain_path(value: str | Path, *, exists: bool = True) -> Path:
    path = Path(os.path.abspath(Path(value).expanduser()))
    for part in (*reversed(path.parents), path):
        if part.is_symlink():
            raise ValueError(f"Symlink paths are not supported: {part}")
    if exists and not path.exists():
        raise ValueError(f"File does not exist: {path}")
    return path


def digest(path: Path) -> str:
    path = plain_path(path)
    result = hashlib.sha256()
    with path.open("rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("Expected a regular file")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def run(args: list[str], *, timeout: int = 3600) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{args[0]} exceeded its time limit") from exc
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr[-1500:]}")
    return result


def probe(path: Path) -> dict:
    return json.loads(run(["ffprobe", "-v", "error", *LOCAL_INPUT,
        "-show_format", "-show_streams", "-of", "json", str(path)], timeout=60).stdout)


def duration(metadata: dict) -> float:
    value = float(metadata.get("format", {}).get("duration", 0))
    if not math.isfinite(value) or value < 0:
        raise ValueError("Invalid media duration")
    return value


def has_audio(metadata: dict) -> bool:
    return any(s.get("codec_type") == "audio" for s in metadata.get("streams", []))


def audio_evidence(path: Path, output: Path) -> dict:
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise ValueError("Audio evidence requires mono PCM16")
        rate = wav.getframerate()
        samples = array.array("h", wav.readframes(wav.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        raise ValueError("Audio evidence is empty")
    peak = max(abs(s) for s in samples) / 32768
    rms = math.sqrt(sum((s / 32768) ** 2 for s in samples) / len(samples))
    image = Image.new("RGB", (960, 180), "#151515")
    draw = ImageDraw.Draw(image)
    draw.line((0, 90, 959, 90), fill="#555555")
    for x in range(960):
        segment = samples[x * len(samples) // 960:(x + 1) * len(samples) // 960]
        if segment:
            low, high = min(segment) / 32768, max(segment) / 32768
            draw.line((x, 90 - high * 70, x, 90 - low * 70), fill="#76c5e0")
    draw.text((10, 8), f"PCM waveform | {len(samples)/rate:.2f}s | peak {peak:.3f} | RMS {rms:.3f}", fill="white")
    image.save(output)
    return {"method": "decoded_pcm", "duration": len(samples) / rate,
            "peak": peak, "rms": rms,
            "clipped_samples": sum(abs(s) >= 32760 for s in samples),
            "max_adjacent_delta": max((abs(b - a) / 32768 for a, b in zip(samples, samples[1:])), default=0),
            "waveform": str(output),
            "limitation": "Signal measurements support audio checks; they do not establish perceptual quality."}


def window(path: Path, metadata: dict, start: float, end: float, dest: Path) -> dict:
    """Return a labelled filmstrip and audio excerpt from this exact media file."""
    total = duration(metadata)
    if not all(math.isfinite(t) for t in (start, end)) or start < 0 or end <= start:
        raise ValueError("Inspection requires finite ordered timestamps")
    if total and end > total + 0.01:
        raise ValueError("Inspection exceeds media duration")
    dest.mkdir(mode=0o700, parents=True, exist_ok=False)
    result: dict = {"start": start, "end": end, "images": [], "audio": None}
    video = any(s.get("codec_type") == "video" for s in metadata.get("streams", []))
    if video:
        sheet = Image.new("RGB", (960, 400), "#151515")
        draw = ImageDraw.Draw(sheet)
        # Do not sample the exact EOF: decoders may return no frame there.
        hi = min(end, max(start, total - 0.08)) if total else end
        for i in range(6):
            t = start + (hi - start) * i / 5
            frame = dest / f"frame-{i}.jpg"
            run(["ffmpeg", "-nostdin", "-v", "error", *LOCAL_INPUT,
                 "-ss", str(t), "-i", str(path), "-frames:v", "1",
                 "-vf", "scale=320:180:force_original_aspect_ratio=decrease", str(frame)], timeout=90)
            with Image.open(frame) as img:
                x, y = (i % 3) * 320, (i // 3) * 200
                sheet.paste(img, (x + (320 - img.width) // 2, y))
                draw.text((x + 8, y + 182), f"{t:.3f}s", fill="white")
        sheet_path = dest / "filmstrip.jpg"
        sheet.save(sheet_path)
        result["images"] = [str(sheet_path)]
    if has_audio(metadata):
        audio = dest / "audio.wav"
        # For a wide source overview, provide first 20 seconds of audio. The
        # agent can request focused windows for the remaining material.
        run(["ffmpeg", "-nostdin", "-v", "error", *LOCAL_INPUT,
             "-ss", str(start), "-i", str(path), "-t", str(min(end - start, 20)),
             "-vn", "-ac", "1", "-ar", "16000", str(audio)], timeout=120)
        result["audio"] = str(audio)
        result["audio_end"] = start + min(end - start, 20)
        result["audio_analysis"] = audio_evidence(audio, dest / "waveform.png")
        result["images"].append(str(dest / "waveform.png"))
    result["sha256"] = {str(p): digest(p) for p in dest.iterdir() if p.is_file()}
    return result


def public_target(url: str) -> tuple[str, str, str]:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("Media URL must be public HTTPS on port 443 without credentials")
    host = parsed.hostname.encode("idna").decode("ascii")
    records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(r[4][0] for r in records))
    if not addresses or not all(ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("Media URL must resolve only to public IP addresses")
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    return host, addresses[0], target


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str):
        super().__init__(host, timeout=30, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def download(url: str, destination: Path, *, max_bytes: int = 500_000_000) -> dict:
    """No cookies, proxies, website extractors, or unchecked redirects."""
    initial = url
    for _ in range(6):
        host, address, target = public_target(url)
        connection = PinnedHTTPS(host, address)
        try:
            connection.request("GET", target, headers={"User-Agent": "EditorCLI/0.1"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Media redirect has no location")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"Media server returned HTTP {response.status}")
            suffix = Path(urlsplit(url).path).suffix.lower()
            if KINDS.get(suffix) not in ("video", "image", "audio"):
                raise ValueError("Use a direct media URL ending in a supported media extension")
            length = response.getheader("Content-Length")
            if length and int(length) > max_bytes:
                raise ValueError("Media download exceeds 500 MB")
            path = destination.with_suffix(suffix)
            count = 0
            with path.open("xb") as handle:
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if count > max_bytes:
                        raise ValueError("Media download exceeds 500 MB")
                    handle.write(chunk)
            if not count:
                raise ValueError("Media server returned an empty file")
            return {"path": str(path), "source_url": initial, "final_url": url,
                    "sha256": digest(path), "bytes": count}
        finally:
            connection.close()
    raise ValueError("Too many media redirects")
