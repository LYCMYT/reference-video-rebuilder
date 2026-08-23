# Codex Reference Video Rebuilder

`reference-video-rebuilder` is a Codex Skill design for turning a reference video into a clean, reusable video template. It analyzes timing, layout, motion, cuts, replaceable content, and removable overlays, then rebuilds the video with user-supplied models, clothing, products, backgrounds, text, logos, props, and audio.

The project treats a reference video as a **structure and timing specification**, not as a source of pixels to copy. Platform UI, comments, account information, and watermarks are excluded from the rebuilt result.

> Status: **0.3.0-alpha**. The local compiler accepts only an authorized,
> `local-only` `fixed-subject-carousel` S1 reference with reviewer-confirmed
> geometry and `slot_count`. It validates a frozen Compiler Plan, creates a
> Template IR that remains schema version **0.2.0**, and can flag timing for
> review. It does not provide OCR, arbitrary semantic understanding, cloud
> execution, or asset generation.

## Core idea

The system has two operating modes:

1. **Bounded compile mode** — compile a confirmed fixed-subject-carousel S1 plan into a reviewable Template IR.
2. **Remix mode** — reuse an approved template with a new asset mapping and render a new video.

```text
authorized local reference + confirmed Compiler Plan
        -> validate plan and media preflight
        -> bounded fixed-subject-carousel compilation
        -> Template IR + compact review report
        -> resolve review_required when present
        -> map user-supplied render-ready assets
        -> deterministic render
        -> automated and human QA
        -> final videos + reusable project
```

## Repository layout

```text
docs/DESIGN.zh-CN.md                 Complete product and technical design
docs/GITHUB_SETUP.zh-CN.md           Recommended GitHub repository settings
THIRD_PARTY.md                       Dependency and license policy
skills/reference-video-rebuilder/      Installable Codex Skill
```

## Supported direction

The current compiler is intentionally narrower than a general video analyzer:
one authorized local `fixed-subject-carousel` S1 family. A human or Codex
reviewer must confirm source geometry and `slot_count` before compilation. It
does not classify arbitrary footage, infer semantic slots, read text with OCR,
use cloud services, or generate replacement assets. It does not claim
pixel-perfect arbitrary-video replacement.

## Install the Skill and runtime dependencies

This repository is the source project; the installable Skill is the nested
`skills/reference-video-rebuilder` directory. Install that directory with the
Codex GitHub-skill installation flow, or copy/link it into the skills directory
configured by your Codex runtime. Do not copy the repository root as though it
were a single Skill, and do not describe this design release as a Plugin.

Install runtime dependencies from the repository root:

```powershell
python -m pip install -r .\skills\reference-video-rebuilder\requirements-runtime.txt

# Contributors: runtime dependencies plus development-only tooling.
python -m pip install -r .\requirements-dev.txt
```

After installing or copying the Skill on its own, run this from the installed
Skill directory (the directory that contains `SKILL.md`):

```powershell
python -m pip install -r .\requirements-runtime.txt
```

FFmpeg/ffprobe remain external local executables. Run `doctor` with explicit
executable paths when using a portable installation. Only the reported
capabilities are available. In particular, this alpha has no OCR, no arbitrary
semantic-slot inference, no cloud route, and no image/video asset generation;
replacement looks must be supplied as reviewed `render-ready` assets.

## 0.3.0-alpha quick start

```powershell
cd skills/reference-video-rebuilder
$ffmpeg = 'C:\tools\ffmpeg.exe' # Or omit when ffmpeg is on PATH.
$ffprobe = 'C:\tools\ffprobe.exe' # Required for bounded compilation.
python scripts/video_remix.py doctor --ffmpeg $ffmpeg --ffprobe $ffprobe --json
python scripts/video_remix.py validate-compiler-plan assets/project-template/compiler.plan.example.json --json
python scripts/video_remix.py validate-template assets/project-template/template.ir.example.json --json
python scripts/video_remix.py validate-assets assets/project-template/template.ir.example.json assets/project-template/assets.example.json --allow-missing-files --json

# A new authorized fixed-subject-carousel S1 reference, after a reviewer has
# confirmed its geometry and slot_count in the frozen Compiler Plan.
$project = 'D:\video-projects\outfit-reel'
python scripts/video_remix.py compile "$project\reference.mp4" assets/project-template/compiler.plan.example.json --project-root $project --output-dir template-compile --ffmpeg $ffmpeg --ffprobe $ffprobe --json

# An approved S1 template with user-provided or pre-generated render-ready looks.
python scripts/video_remix.py render "$project\template.ir.json" "$project\assets.json" --project-root $project --ffmpeg $ffmpeg --summary run-summary.json --json
```

Run the lightweight test suite from the repository root:

```powershell
python -m unittest discover -s tests -v
```

The example uses `--allow-missing-files` because it contains placeholder paths.
`compile` first validates the Compiler Plan and performs media-dependent
semantic preflight before publishing its output directory. Its exit code is
`0` when `review_required` is false, `1` when artifacts exist but review is
required, and `2` for validation or operational errors. CLI JSON remains
compact: it reports paths and review facts, never a full Template IR or a
per-frame score dump. See the [Compiler Plan contract](skills/reference-video-rebuilder/references/compiler-contract.md).

Production `render` always validates the Template IR and Asset Manifest with
file checks and rejects `support.review_required: true` before it writes
frames, then performs a complete FFmpeg decode
plus dimensions, cadence, exact frame count, audio-presence, and duration
checks for every requested output. A technical QA failure returns a non-zero
result and is included in the optional run summary.

Before rendering, an agent must turn the survey into a reviewed Template IR and provide a render-ready model/look image for each garment layer (or the user must supply it). The CLI does not decide what is a model, garment, product, background, platform UI, comment, or watermark, and it cannot recover pixels hidden behind an overlay. Human/agent visual review remains required for identity, garment fidelity, residual platform elements, and commercial rights.

## Privacy and rights

The workflow must obtain confirmation that the user has permission to use the
reference video, likenesses, products, logos, and audio. This alpha is
`local-only`; it has no cloud-assisted execution profile and never uploads
media or derived evidence.

## License

Original code and documentation in this repository are licensed under Apache License 2.0. Third-party tools, models, checkpoints, fonts, codecs, and generated media retain their own licenses. See [THIRD_PARTY.md](THIRD_PARTY.md).

For the complete Chinese design, see [docs/DESIGN.zh-CN.md](docs/DESIGN.zh-CN.md).
