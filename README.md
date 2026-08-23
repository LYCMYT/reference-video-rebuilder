# Codex Reference Video Rebuilder

reference-video-rebuilder is a Codex Skill and local CLI for rebuilding one
authorized, bounded reference-video family as a reusable template. It treats a
reference as a structure and timing specification, never as pixels to copy.
Platform UI, comments, account information, and watermarks are excluded from
the clean reconstruction; pixels fully hidden by them are not recoverable.

> Status: 0.4.0-alpha. The supported new-reference path is local and bounded:
> propose -> review -> freeze-plan -> compile -> render. It is limited to an
> authorized fixed-subject-carousel S1 reference. It is not an arbitrary-video
> family-discovery, semantic-classification, OCR, cloud, or asset-generation
> product.

## What 0.4 adds

For an authorized local fixed-subject-carousel S1 source, propose produces a
strict 0.4.0 Proposal JSON and a pending review template. It also writes local,
bounded artifacts:

- an overview contact sheet;
- a geometry preview;
- a timing profile;
- candidate source crop, top carousel boundary, subject region, slot count,
  switch timing, proportional carousel layout, and background color.

The proposed source rectangle is a maximal centered crop matching the supported
9:16 output aspect. It is the whole source frame only when the source already
has that aspect. This is a composition heuristic, not semantic platform-UI
detection or removal. A reviewer must correct it when platform chrome,
non-centered content, or a nonuniform crop makes it wrong.

A proposal is never approval: its review_required value is always true. Full
geometry, carousel proportions, timing, and every semantic interpretation
remain subject to explicit review. Propose does not infer a person, garment,
product, UI element, watermark, or hidden content.

Review is explicit and bound to the exact proposal hash. An approved review
confirms the family, geometry, slot_count, timing, carousel, background,
audio, and authorization; the reviewer may edit the approved_plan before
freezing. freeze-plan validates the binding and every confirmation, then emits
the canonical frozen Compiler Plan.

## Version compatibility

| Artifact or surface | Version |
| --- | --- |
| Product and CLI | 0.4.0-alpha |
| Proposal JSON | 0.4.0 |
| Frozen Compiler Plan | 0.3.0 |
| Template IR | 0.2.0 |

The frozen Compiler Plan remains schema 0.3.0 so existing v0.3 Compiler Plan
consumers remain compatible. Deterministic compilation, rendering, Template
IR, and technical QA retain their existing contracts.

## Supported boundary

The alpha accepts only local, authorized fixed-subject-carousel S1 work. It
does not provide:

- OCR or arbitrary-video semantic classification;
- identity, garment, product, UI, watermark, or text meaning inference;
- cloud execution, uploads, or generated replacement assets;
- automatic approval or recovery of concealed pixels;
- automatic family discovery beyond the bounded S1 workflow.

Windows is the audited release platform for v0.4.0-alpha. Other operating
systems retain fail-closed identity and reparse-point checks, but this release
does not claim the same directory-handle race guarantees outside Windows.

Raw source and evidence remain local. Proposal artifacts must not contain source
or tool absolute paths, filenames, container tags, title/artist/comments,
account identity, raw probes, raw media, or private evidence payloads. The only
allowed technical source fingerprint is SHA-256, width, height, exact frame
count, fps, and audio presence. Bounded evidence references are for local
review only.

## Install the Skill and runtime dependencies

This repository is the source project. Its installable Skill is the nested
skills/reference-video-rebuilder directory. Install that directory with the
Codex GitHub-skill installation flow, or copy/link it into the configured Skill
directory. Do not install the repository root as a single Skill or describe
this release as a Plugin.

From the repository root:

~~~powershell
python -m pip install -r .\skills\reference-video-rebuilder\requirements-runtime.txt

# Contributors: runtime dependencies plus development tooling.
python -m pip install -r .\requirements-dev.txt
~~~

After installing the Skill by itself, run the same runtime install command from
the installed directory that contains SKILL.md. FFmpeg and ffprobe are external
local executables.

## 0.4.0-alpha quick start

Run these commands from the repository checkout. The proposal command is the
only new-reference entry point; do not hand-author a Compiler Plan in place of
review.

~~~powershell
Set-Location .\skills\reference-video-rebuilder

$project = 'D:\video-projects\outfit-reel'
$source = Join-Path $project 'reference.mp4'
$ffmpeg = 'C:\tools\ffmpeg.exe'
$ffprobe = 'C:\tools\ffprobe.exe'

python scripts/video_remix.py doctor --ffmpeg $ffmpeg --ffprobe $ffprobe --json
python scripts/video_remix.py propose "$source" --project-root $project --output-dir proposal --template-id outfit-reel-001 --reference-rights-confirmed --ffmpeg $ffmpeg --ffprobe $ffprobe --json
~~~

For `propose` and `freeze-plan`, `--output-dir` must name one new direct child
of `--project-root`, such as `proposal` or `frozen-plan`. Absolute paths,
nested paths, `.`, `..`, and existing targets are rejected before media work or
artifact writes. This v0.4 restriction is part of the Windows-safe atomic
publication contract. Use a neutral directory name: project-relative artifact
paths record this caller-supplied name, so it must not contain a source filename,
person name, account ID, or other private identifier.

For `freeze-plan`, its two positional packet arguments must also be normalized
paths relative to `--project-root`, for example
`proposal/compiler-plan-proposal.json`. Absolute local paths, drive-rooted
paths, and UNC paths are rejected before the packet path is inspected. This
restriction applies only to freezing: `validate-proposal` and `validate-review`
may independently inspect a user-specified file.

For the output directory used above, propose writes the following
project-relative artifacts. Before approval, inspect the contact sheet,
geometry preview, and timing profile locally. Correct the review template or
its approved_plan where needed; do not treat a generated candidate as an
approval.

~~~powershell
$proposalFile = Join-Path $project 'proposal\compiler-plan-proposal.json'
$reviewFile = Join-Path $project 'proposal\review-decision.template.json'
$proposalPacket = 'proposal/compiler-plan-proposal.json'
$reviewPacket = 'proposal/review-decision.template.json'

python scripts/video_remix.py validate-proposal "$proposalFile" --json

# Edit $reviewFile to explicitly approve the proposal hash and all required
# confirmations, including any corrected approved_plan.
python scripts/video_remix.py validate-review "$reviewFile" --json
python scripts/video_remix.py freeze-plan "$proposalPacket" "$reviewPacket" --project-root $project --output-dir frozen-plan --json
~~~

freeze-plan reports the project-relative frozen Compiler Plan path. Validate
that frozen plan, compile it, and then render only with a reviewed Template IR
and explicit render-ready asset mapping.

~~~powershell
$plan = Join-Path $project 'frozen-plan\compiler-plan.json'

python scripts/video_remix.py validate-compiler-plan "$plan" --json
python scripts/video_remix.py compile "$source" "$plan" --project-root $project --output-dir template-compile --ffmpeg $ffmpeg --ffprobe $ffprobe --json

$template = Join-Path $project 'template-compile\template.ir.json'
$assets = Join-Path $project 'assets.json'
python scripts/video_remix.py validate-template "$template" --json
python scripts/video_remix.py validate-assets "$template" "$assets" --json
python scripts/video_remix.py render "$template" "$assets" --project-root $project --ffmpeg $ffmpeg --summary run-summary.json --json
~~~

Optional propose inputs are slot-count hint, confirmed audio rights, audio
mode, and output profile. Supply them only when the user has selected those
values and the local CLI help confirms the applicable form.

`doctor --json` is a local diagnostic and reports resolved executable paths.
Do not paste its raw output into a public issue; proposal, freeze, compile, and
render summaries use the narrower public-output contracts instead.

Propose succeeds with exit code 0 and status review_required; that status is a
mandatory stop, not an approval. Proposal/review validation failures and
freeze-plan failures exit 2. Compile retains its existing exits: 0 when no
review is required, 1 when artifacts exist but review is required, and 2 for
validation or operational errors.

Run the lightweight test suite from the repository root:

~~~powershell
python -m unittest discover -s tests -v
~~~

## Review, QA, privacy, and rights

Validate the local technical gates, then perform human visual and rights
review. A successful media decode does not establish identity consistency,
garment/product fidelity, correct carousel semantics, absence of residual
platform elements, or commercial rights.

Confirm permission for the reference video, likenesses, products, logos, and
audio before proposal. This alpha is local-only: it does not upload media,
evidence, proposal artifacts, or derived data.

See the [Compiler Plan contract](skills/reference-video-rebuilder/references/compiler-contract.md),
[QA gates](skills/reference-video-rebuilder/references/qa-gates.md), and the
complete Chinese [design](docs/DESIGN.zh-CN.md).

## License

Original code and documentation in this repository are licensed under Apache
License 2.0. Third-party tools, models, checkpoints, fonts, codecs, and media
retain their own licenses. See [THIRD_PARTY.md](THIRD_PARTY.md).
