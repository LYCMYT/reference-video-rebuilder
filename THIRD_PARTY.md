# Third-party dependency policy

This repository does not vendor third-party model weights, FFmpeg binaries, Remotion source, fonts, music, or sample reference videos. Each runtime adapter must record the exact dependency version and license in the run manifest.

> Review status: design inventory reviewed on 2026-08-22. Runtime versions and
> commit hashes are intentionally not pinned yet because those adapters are not
> implemented. A public runtime release is blocked until every enabled
> dependency has an exact version/commit, source URL, license URL, checksum when
> applicable, and a recorded review date.

## Recommended external building blocks

| Component | Intended role | License note | Distribution policy |
|---|---|---|---|
| [FFmpeg / ffprobe](https://ffmpeg.org/) | Decode, encode, mux, audio, frame extraction | License depends on build flags; some binaries are GPL | Detect a user-installed binary or download only after showing its license; do not silently redistribute |
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
