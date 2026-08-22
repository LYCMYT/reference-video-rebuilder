# Codex Reference Video Rebuilder

`rebuild-reference-video` is a Codex Skill design for turning a reference video into a clean, reusable video template. It analyzes timing, layout, motion, cuts, replaceable content, and removable overlays, then rebuilds the video with user-supplied models, clothing, products, backgrounds, text, logos, props, and audio.

The project treats a reference video as a **structure and timing specification**, not as a source of pixels to copy. Platform UI, comments, account information, and watermarks are excluded from the rebuilt result.

> Status: architecture and Skill specification. The deterministic manifest validator is included; full analyzers, generation adapters, and renderers are planned in phases.

## Core idea

The system has two operating modes:

1. **Compile mode** — analyze a new reference video and produce a reviewable Template IR with replaceable slots.
2. **Remix mode** — reuse an approved template with a new asset mapping and render a new video.

```text
reference video + user assets
        -> analyze and classify
        -> Template IR + confidence report
        -> confirm uncertain slots
        -> prepare replacement assets
        -> deterministic render
        -> automated and human QA
        -> final videos + reusable project
```

## Repository layout

```text
docs/DESIGN.zh-CN.md                 Complete product and technical design
docs/GITHUB_SETUP.zh-CN.md           Recommended GitHub repository settings
THIRD_PARTY.md                       Dependency and license policy
skills/rebuild-reference-video/      Installable Codex Skill
```

## Supported direction

- High automation: fixed camera, one primary subject, simple backgrounds, regular cuts, 2D overlays, product carousels, and outfit-switch videos.
- Assisted workflow: moderate subject motion, slow camera movement, trackable occlusions, and dynamic masks.
- Experimental: fast motion, large pose changes, complex cloth dynamics, reflections, transparency, or multi-person interactions.

The system must classify the reference before promising a result. It does not claim pixel-perfect arbitrary-video replacement.

## Install the Skill for local development

This repository is the source project; the installable Skill is the nested
`skills/rebuild-reference-video` directory. Install that directory with the
Codex GitHub-skill installation flow, or copy/link it into the skills directory
configured by your Codex runtime. Do not copy the repository root as though it
were a single Skill, and do not describe this design release as a Plugin.

Runtime tools such as FFmpeg, Remotion, segmentation models, and image/video
generation providers are separate dependencies and are not vendored here. The
current release remains a design-stage scaffold until the analyzer, renderer,
and video QA capabilities report `true` from `doctor`.

## Design-stage quick start

```powershell
cd skills/rebuild-reference-video
python scripts/video_remix.py doctor --json
python scripts/video_remix.py validate-template assets/project-template/template.ir.example.json --json
python scripts/video_remix.py validate-assets assets/project-template/template.ir.example.json assets/project-template/assets.example.json --allow-missing-files --json
```

Run the lightweight test suite from the repository root:

```powershell
python -m unittest discover -s tests -v
```

The example uses `--allow-missing-files` because it contains placeholder paths. Production validation checks file existence and project-root containment by default. Media dimensions and codecs still require the planned media-probe stage. The current CLI deliberately reports analysis, generation, rendering, and video QA as unavailable so the design release does not imply that planned capabilities already work.

## Privacy and rights

The workflow must obtain confirmation that the user has permission to use the reference video, likenesses, products, logos, and audio. Local-only and cloud-assisted generation are separate execution profiles; cloud-assisted mode must never upload assets without explicit user authorization.

## License

Original code and documentation in this repository are licensed under Apache License 2.0. Third-party tools, models, checkpoints, fonts, codecs, and generated media retain their own licenses. See [THIRD_PARTY.md](THIRD_PARTY.md).

For the complete Chinese design, see [docs/DESIGN.zh-CN.md](docs/DESIGN.zh-CN.md).
