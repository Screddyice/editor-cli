from editor_cli.adapters.fcp_assets import InstalledAssetCatalog


def test_asset_catalog_scans_only_approved_roots(tmp_path):
    approved = tmp_path / "Motion Templates.localized"
    effect = approved / "Effects.localized" / "Comedy" / "Punch In.moef"
    effect.parent.mkdir(parents=True)
    effect.write_text("fixture")
    outside = tmp_path / "Private"
    outside.mkdir()
    (outside / "Secret.moti").write_text("fixture")

    catalog = InstalledAssetCatalog((approved,)).scan()

    assert [(item.kind, item.name) for item in catalog] == [("effect", "Punch In")]
    assert catalog[0].action_id == "Comedy/Punch In"


def test_asset_catalog_does_not_follow_symlink_outside_root(tmp_path):
    approved = tmp_path / "Motion Templates.localized"
    approved.mkdir()
    outside = tmp_path / "Private"
    outside.mkdir()
    (outside / "Secret.moti").write_text("fixture")
    (approved / "Titles.localized").symlink_to(outside, target_is_directory=True)

    assert InstalledAssetCatalog((approved,)).scan() == ()
