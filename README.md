# Codex Reference Video Rebuilder

`rebuild-reference-video` is a Codex Skill design for turning a reference video into a clean, reusable video template. It analyzes timing, layout, motion, cuts, replaceable content, and removable overlays, then rebuilds the video with user-supplied models, clothing, products, backgrounds, text, logos, props, and audio.

The project treats a reference video as a **structure and timing specification**, not as a source of pixels to copy. Platform UI, comments, account information, and watermarks are excluded from the rebuilt result.

> Status: **0.2.0-alpha**. The local S1 path can probe/survey reference media, validate a frozen Template IR and local asset manifest, deterministically render supported timelines, and technically verify every encoded output. Semantic slot decisions and generation of replacement looks remain agent-assisted.

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

## Install the Skill and runtime dependencies

This repository is the source project; the installable Skill is the nested
`skills/rebuild-reference-video` directory. Install that directory with the
Codex GitHub-skill installation flow, or copy/link it into the skills directory
configured by your Codex runtime. Do not copy the repository root as though it
were a single Skill, and do not describe this design release as a Plugin.

Install runtime dependencies from the repository root:

```powershell
python -m pip install -r .\skills\rebuild-reference-video\requirements-runtime.txt

# Contributors: runtime dependencies plus development-only tooling.
python -m pip install -r .\requirements-dev.txt
```

After installing or copying the Skill on its own, run this from the installed
Skill directory (the directory that contains `SKILL.md`):

```powershell
python -m pip install -r .\requirements-runtime.txt
```

FFmpeg/ffprobe remain external local executables; image/video generation
providers and advanced analyzers are optional and are not vendored here. Run
`doctor` with explicit executable paths when using a portable installation.
Only the reported capabilities are available. In particular, this alpha does
not infer semantic slots by itself, generate a new outfit/model image during
`render`, or promise pixel-identical replacement for arbitrary video.

## 0.2.0-alpha quick start

```powershell
cd skills/rebuild-reference-video
$ffmpeg = 'C:\tools\ffmpeg.exe' # Or omit when ffmpeg is on PATH.
python scripts/video_remix.py doctor --ffmpeg $ffmpeg --json
python scripts/video_remix.py validate-template assets/project-template/template.ir.example.json --json
python scripts/video_remix.py validate-assets assets/project-template/template.ir.example.json assets/project-template/assets.example.json --allow-missing-files --json

# A new authorized reference: make bounded local evidence for Codex/agent review.
$project = 'D:\video-projects\outfit-reel'
python scripts/video_remix.py survey "$project\reference.mp4" --project-root $project --output-dir reference-survey --ffmpeg $ffmpeg --json

# An approved S1 template with user-provided or pre-generated render-ready looks.
python scripts/video_remix.py render "$project\template.ir.json" "$project\assets.json" --project-root $project --ffmpeg $ffmpeg --summary run-summary.json --json
```

Run the lightweight test suite from the repository root:

```powershell
python -m unittest discover -s tests -v
```

The example uses `--allow-missing-files` because it contains placeholder paths. Production `render` always validates the Template IR and Asset Manifest with file checks before it writes frames, then performs a complete FFmpeg decode plus dimensions, cadence, exact frame count, audio-presence, and duration checks for every requested output. A technical QA failure returns a non-zero result and is included in the optional run summary.

Before rendering, an agent must turn the survey into a reviewed Template IR and provide a render-ready model/look image for each garment layer (or the user must supply it). The CLI does not decide what is a model, garment, product, background, platform UI, comment, or watermark, and it cannot recover pixels hidden behind an overlay. Human/agent visual review remains required for identity, garment fidelity, residual platform elements, and commercial rights.

## Privacy and rights

The workflow must obtain confirmation that the user has permission to use the reference video, likenesses, products, logos, and audio. Local-only and cloud-assisted generation are separate execution profiles; cloud-assisted mode must never upload assets without explicit user authorization.

## License

Original code and documentation in this repository are licensed under Apache License 2.0. Third-party tools, models, checkpoints, fonts, codecs, and generated media retain their own licenses. See [THIRD_PARTY.md](THIRD_PARTY.md).

For the complete Chinese design, see [docs/DESIGN.zh-CN.md](docs/DESIGN.zh-CN.md).
