"""Run the pinned watch script while retaining fractional frame timestamps."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys


def precise_time(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("watch returned an invalid frame timestamp")
    return f"{seconds:.6f}"


def install_exact_uniform_sampling(module) -> None:
    original = getattr(module, "extract_scene_or_uniform", None)
    exact = getattr(module, "extract_at_timestamps", None)
    if not callable(original) or not callable(exact):
        raise RuntimeError("watch does not expose its pinned frame extractors")

    def extract(video_path, out_dir, **kwargs):
        frames, metadata = original(video_path, out_dir, **kwargs)
        uniform = [frame for frame in frames if frame.get("reason") == "uniform"]
        if uniform:
            timestamps = sorted({frame["timestamp_seconds"] for frame in uniform})
            replacements, _ = exact(
                video_path,
                Path(out_dir) / "uniform-exact",
                timestamps,
                resolution=kwargs.get("resolution", 512),
                max_frames=None,
            )
            by_time = {frame["timestamp_seconds"]: frame for frame in replacements}
            if set(by_time) != set(timestamps):
                raise RuntimeError("watch could not extract every uniform timestamp")
            frames = [
                {**by_time[frame["timestamp_seconds"]], "reason": "uniform-exact"}
                if frame.get("reason") == "uniform"
                else frame
                for frame in frames
            ]
        return frames, metadata

    module.extract_scene_or_uniform = extract


def main() -> None:
    script = Path(sys.argv[1]).expanduser().resolve(strict=True)
    sys.path.insert(0, str(script.parent))
    spec = importlib.util.spec_from_file_location("_editor_watch", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the configured watch script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "format_time", None)) or not callable(
        getattr(module, "main", None)
    ):
        raise RuntimeError("The configured watch script has an unsupported interface")
    # watch 0.2.0 rounds its Markdown timestamps to whole seconds. Keep the
    # underlying frame timestamp so review cannot mistake a nearby scene for
    # a short transition. The installed script itself stays unchanged.
    module.format_time = precise_time
    # The upstream fps filter labels output slots rather than source-frame PTS.
    # Re-extract its selected uniform moments through the exact cue extractor.
    install_exact_uniform_sampling(module)
    sys.argv = [str(script), *sys.argv[2:]]
    module.main()


if __name__ == "__main__":
    main()
