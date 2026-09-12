from pathlib import Path

import pytest

from editor_cli.adapters.export_bundle import materialize_export


@pytest.fixture
def export(tmp_path):
    bundle = tmp_path / "source.fcpxmld"
    bundle.mkdir()
    (bundle / "Info.fcpxml").write_bytes(b"<fcpxml/>\n")
    return bundle, tmp_path / "source.fcpxml", tmp_path


def test_materializes_exact_bytes_and_preserves_bundle(export):
    bundle, destination, root = export
    materialize_export(*export)
    assert destination.read_bytes() == (bundle / "Info.fcpxml").read_bytes()
    with pytest.raises(FileExistsError):
        materialize_export(*export)
    assert sorted(p.name for p in root.iterdir()) == ["source.fcpxml", "source.fcpxmld"]


@pytest.mark.parametrize("part", ["document", "parent", "destination"])
def test_rejects_symlinks(export, part):
    bundle, destination, root = export
    if part == "document":
        document = bundle / "Info.fcpxml"
        document.unlink()
        document.symlink_to(root / "unselected")
    elif part == "parent":
        alias = root / "alias"
        alias.symlink_to(root, target_is_directory=True)
        bundle, destination = alias / bundle.name, alias / destination.name
    else:
        destination.symlink_to(root / "unselected")
    with pytest.raises(ValueError, match="symlink|regular"):
        materialize_export(bundle, destination, root)
    assert not (root / "unselected").exists()


def test_rejects_wrong_receipt_and_outside_root(export):
    bundle, destination, root = export
    with pytest.raises(ValueError):
        materialize_export(bundle, root / "other.fcpxml", root)
    with pytest.raises(ValueError):
        materialize_export(bundle, destination, root / "elsewhere")
    with pytest.raises(ValueError):
        materialize_export(
            root / ".." / "source.fcpxmld", root / ".." / "source.fcpxml", root
        )


def test_concurrent_output_is_not_replaced(export, monkeypatch):
    bundle, destination, root = export
    import editor_cli.adapters.export_bundle as module

    link = module.os.link

    def concurrent_link(source, target):
        Path(target).write_bytes(b"other writer")
        return link(source, target)

    monkeypatch.setattr(module.os, "link", concurrent_link)
    with pytest.raises(FileExistsError):
        materialize_export(*export)
    assert destination.read_bytes() == b"other writer"
    assert sorted(p.name for p in root.iterdir()) == ["source.fcpxml", "source.fcpxmld"]
