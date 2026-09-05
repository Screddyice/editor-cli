"""Catalog installed Final Cut and Motion templates from approved roots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ASSET_TYPES = {
    ".moef": ("effect", "fcpx_videoEffect"),
    ".moti": ("title", "fcpx_title"),
    ".motr": ("transition", "fcpx_transition"),
    ".motn": ("generator", "fcpx_generator"),
}
TEMPLATE_CONTAINERS = frozenset(
    {
        "Effects",
        "Titles",
        "Transitions",
        "Generators",
    }
)
DEFAULT_ASSET_ROOTS = (
    Path(
        "/Applications/Final Cut Pro.app/Contents/PlugIns/"
        "MediaProviders/MotionEffect.fxp/Contents/Resources/Templates.localized"
    ),
    Path("/Library/Plug-Ins/FxPlug"),
    Path("~/Library/Plug-Ins/FxPlug").expanduser(),
    Path("~/Movies/Motion Templates.localized").expanduser(),
    Path("/Library/Audio/Apple Loops/Apple/Final Cut Pro Sound Effects"),
)


@dataclass(frozen=True)
class InstalledAsset:
    kind: str
    name: str
    category: str
    action_id: str
    handler: str
    path: Path


class InstalledAssetCatalog:
    def __init__(self, roots: tuple[Path, ...] = DEFAULT_ASSET_ROOTS):
        self.roots = tuple(root.expanduser().resolve() for root in roots)

    def scan(self) -> tuple[InstalledAsset, ...]:
        assets: list[InstalledAsset] = []
        for root in self.roots:
            if not root.is_dir():
                continue
            for candidate in root.rglob("*"):
                if candidate.suffix.lower() not in ASSET_TYPES:
                    continue
                resolved = candidate.resolve()
                if not resolved.is_relative_to(root):
                    continue
                kind, handler = ASSET_TYPES[candidate.suffix.lower()]
                relative = candidate.relative_to(root)
                parts = [part.removesuffix(".localized") for part in relative.parts]
                name = Path(parts[-1]).stem
                categories = parts[:-1]
                if categories and categories[0] in TEMPLATE_CONTAINERS:
                    categories = categories[1:]
                category = "/".join(categories)
                action_id = "/".join((*categories, name)) if categories else name
                assets.append(
                    InstalledAsset(
                        kind=kind,
                        name=name,
                        category=category,
                        action_id=action_id,
                        handler=handler,
                        path=resolved,
                    )
                )
        return tuple(sorted(assets, key=lambda item: (item.kind, item.action_id, item.path)))
