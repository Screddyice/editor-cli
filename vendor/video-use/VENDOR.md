# Vendored: video-use

This directory is a **copied-in** (not submodule, not fork) snapshot of the
upstream open-source project, brought into editor-cli so its source can be used
and adapted as owned code.

| | |
|---|---|
| **Upstream** | https://github.com/browser-use/video-use |
| **Copied at commit** | `cf12ac35143caa48db76efa35b1cb439582333bb` |
| **Copied on** | 2026-06-25 |
| **License** | MIT (© Browser Use) — see `LICENSE` in this directory |

## Why copied, not submoduled

Per the request to "copy, don't fork": the source is physically vendored here as
committed files so it lives in editor-cli's tree and can be edited directly,
unlike `vendor/OpenMontage` which is a git submodule pointing at upstream.

## What was stripped during copy

The upstream working tree was cleaned before copying — removed: `.git/`,
`.venv/`, `.env`, `*.egg-info/`, `__pycache__/`, `*.pyc`. Only source, docs,
static assets, `LICENSE`, `.env.example`, and `pyproject.toml` were kept.

## Updating from upstream

Re-clone upstream, strip the same artifacts, and re-copy over this directory,
then bump the commit SHA above. This is a manual copy — it does not track
upstream automatically.

## License note

video-use is MIT, which is compatible with editor-cli (also MIT). The upstream
`LICENSE` and copyright are retained in this directory as required by MIT.
