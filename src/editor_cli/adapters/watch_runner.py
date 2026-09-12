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
    sys.argv = [str(script), *sys.argv[2:]]
    module.main()


if __name__ == "__main__":
    main()
