# Third-party dependency policy

This repository does not vendor third-party model weights, FFmpeg binaries, Remotion source, fonts, music, or sample reference videos. Each runtime adapter must record the exact dependency version and license in the run manifest.

> Review status: the `0.3.0-alpha` local runtime inventory was reviewed on
> 2026-08-23. Python dependency ranges are declared in the installable Skill;
> resolved versions are reported by `doctor`. FFmpeg/ffprobe remain external
> user-selected executables and are never redistributed by this repository.
> Optional analyzers, generators, and GPU adapters remain disabled until their
> code, weights, source, license, and checksum have been reviewed.

## Enabled direct dependencies

| Component | Declared range | License | Use and distribution policy |
|---|---:|---|---|
| [jsonschema](https://github.com/python-jsonschema/jsonschema) | `>=4.23,<5` | MIT | Required at runtime for Draft 2020-12 structural validation; installed from the user's Python package index |
| [Pillow](https://python-pillow.github.io/) | `>=10,<12` | MIT-CMU | Required at runtime for deterministic raster composition and contact sheets; installed from the user's Python package index |
| [PyYAML](https://github.com/yaml/pyyaml) | `>=6,<7` | MIT | Development/metadata validation only; not required by the installed Skill runtime |
| [FFmpeg / ffprobe](https://ffmpeg.org/) | external executable | LGPL/GPL depending on build flags | Required for media operations; detected from an explicit path, environment, or `PATH`; never vendored or silently redistributed |

The repository records supported ranges rather than claiming that every future
release inside those ranges is byte-identical. Reproducible runs must retain the
resolved Python package versions, FFmpeg/ffprobe version strings, Template IR,
asset hashes, and output hashes. Release artifacts must never bundle a third-
party binary merely because it was used during development.

## Recommended external building blocks

| Component | Intended role | License note | Distribution policy |
|---|---|---|---|
| [Remotion](https://github.com/remotion-dev/remotion) and [official Remotion Skills](https://github.com/remotion-dev/skills) | Deterministic React-based timeline and rendering | Remotion uses its own license; company use may require a commercial license | Keep as an external dependency and pin a tested version |
| [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | Scene and cut detection | BSD-3-Clause | Optional direct dependency |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | Text, account, comment, and overlay detection | Apache-2.0 | Optional direct dependency |
| [SAM 2](https://github.com/facebookresearch/sam2) | Promptable image/video segmentation and mask propagation | Apache-2.0 for published code/checkpoints, subject to repository notices | Optional GPU adapter |
| [GroundingDINO](https://github.com/IDEA-Research/GroundingDINO) | Text-guided open-set object detection | Apache-2.0 for repository code | Optional GPU adapter; review checkpoint provenance |
| [ffmpeg-mcp-video-editor](https://github.com/AbyAbyss/ffmpeg-mcp-video-editor) | Typed FFmpeg/MCP execution layer | MIT; bundled FFmpeg license still applies | Optional integration, pinned to a reviewed commit |
| [ClipCaptionAI](https://github.com/jongan69/ClipCaptionAI) | Run manifests, QA, and deterministic short-video architecture reference | MIT | Borrow architecture selectively; do not make the full app a runtime dependency |
| [ccvideo](https://github.com/AmazingAng/ccvideo) | Deterministic props and validation patterns | MIT | Borrow patterns or compatible code with attribution |

## Research-only or non-commercial defaults

The following popular projects are not safe default dependencies for a commercial distribution without additional permission:

- CatVTON — CC BY-NC-SA 4.0.
- IDM-VTON — code and checkpoints published under CC BY-NC-SA 4.0.
- ProPainter — NTU S-Lab non-commercial license.

They may be used for isolated research evaluation only when their terms permit it. Commercial releases must replace them with licensed services, licensed checkpoints, or independently implemented adapters.

## Repositories without an explicit license

Public access is not permission to copy or redistribute. Projects such as
[`crafter-station/remotion-clone-video`](https://github.com/crafter-station/remotion-clone-video)
may be studied for ideas, but their code, prompt text, Skill instructions, and
templates must not be copied unless the author supplies a suitable license.

## Generated and user-provided media

The repository license does not grant rights to:

- user-provided reference videos or assets;
- faces, voices, trademarks, logos, products, or music;
- generated images and videos produced by external providers;
- model weights, fonts, codecs, or binaries downloaded at runtime.

Every production run must store a rights confirmation and the provenance of each asset without embedding private source files in Git history.
