# reference-video-rebuilder

reference-video-rebuilder is a Codex Skill and local CLI for rebuilding one
authorized, bounded reference-video family as a reusable template. It treats a
reference as a structure and timing specification, never as pixels to copy.
Platform UI, comments, account information, and watermarks are excluded from
the clean reconstruction; pixels fully hidden by them are not recoverable.

> Status: 0.6.0-alpha. The local, bounded new-reference path remains
> propose -> review -> freeze-plan -> compile. v0.6 adds a reviewed bridge for
> externally created still assets: prepare-generation -> plan review -> external
> controller or local file drop -> result review -> assemble-generation-pack ->
> v0.5 asset review/freeze -> render. The CLI never runs a model, shell command,
> network request, weight download, or automatic approval. It remains limited to
> authorized fixed-subject-carousel S1 work, not arbitrary-video discovery,
> semantic classification, OCR, or concealed-pixel recovery.

## What 0.6 adds

v0.6 makes the asset-generation handoff reviewable without pretending that a
generation model is bundled. Given an already validated Template IR, a
user-authored Generation Request and a direct-child local reference pack,
`prepare-generation` creates a hash-bound, review-required generation plan,
review template, and local input contact sheet. The plan can record either a
`local-file-drop` executor or a `controller-managed` executor. The declaration
records bounded `adapter_id` and `adapter_version` and, for controller-managed
work, `controller_label`; none can be a path, URL, or credential. The latter
may be declared `local-only` or `controller-cloud`; `controller-cloud` requires
`cloud_upload_confirmed: true` in both the request and reviewed plan. The CLI
itself does not upload anything in either case.

After an external controller (for example, a Codex image-generation step) or a
user-operated local CUDA workflow places generated files in a new result pack,
`propose-generation-results` makes a second, per-slot review packet and result
contact sheet. The reviewer—not a metric—confirms identity continuity, body and
pose suitability, garment/product/background fidelity, logos/text, hands and
other artifacts, render readiness, and rights. A rejected slot is retried in a
new result pack and proposal; an approved plan and approved image are never
silently overwritten.

`assemble-generation-pack` accepts both approved review packets and emits a
new direct-child pack containing only exact-slot media. It applies EXIF
orientation to static images and re-encodes them as metadata-free PNG; accepted
audio is passed through unchanged. It writes no sidecar, prompt, report, or
credential into that pack, so the existing v0.5 `propose-assets -> review ->
freeze-assets` path remains the final mapping, snapshot, and render gate.
This is a controller/asset bridge, not virtual try-on, CUDA inference, or
provider integration implemented inside the CLI.

## What 0.5 adds

For an existing local Template IR, v0.5 provides a strict asset-pack workflow.
propose-assets scans one direct-child pack containing only static JPEG, PNG, or
WebP images and locally probeable WAV, MP3, M4A, or MKA audio. Unknown files,
videos, animation, sidecars, and unsafe entries fail the entire pack.

Candidate selection is deliberately mechanical: a file can be proposed only
when its exact filename stem equals a Template IR slot_id and its inspected
media type is accepted by that slot. It does not use OCR, visual recognition,
or fuzzy naming. propose-assets always writes a review-required
asset-pack-proposal.json, asset-review-decision.template.json, and
asset-contact-sheet.png; the contact sheet plus JSON review is not a GUI.

freeze-assets requires an explicitly approved review, binds Proposal, Template,
and inventory hashes, safely rescans the source pack, and atomically publishes
frozen-assets/assets.json as a local-only Asset Manifest 0.2.0. The manifest
contains SHA-256-bound opaque flat copies; the paired asset-freeze-report.json
records the freeze evidence. Renderer 0.2.0 reads frozen image snapshots and
feeds frozen audio through pipe:0. Asset Manifest 0.1.0 remains legacy
compatibility only.

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
| Product and CLI | 0.6.0-alpha |
| Proposal JSON | 0.4.0 |
| Asset Pack Proposal and Review | 0.5.0 |
| Generation Request, Plan, and Result packets | 0.6.0 |
| Frozen Compiler Plan | 0.3.0 |
| Template IR | 0.2.0 |
| Frozen Asset Manifest | 0.2.0 |

The frozen Compiler Plan remains schema 0.3.0 so existing v0.3 Compiler Plan
consumers remain compatible. Deterministic compilation, rendering, Template
IR, and technical QA retain their existing contracts.

## Supported boundary

The alpha accepts only authorized fixed-subject-carousel S1 work. The bundled
CLI is local and does not provide:

- OCR or arbitrary-video semantic classification;
- identity, garment, product, UI, watermark, or text meaning inference;
- a bundled image/video model, virtual try-on, CUDA inference, weight download,
  arbitrary shell execution, or network/upload operation;
- automatic approval or recovery of concealed pixels;
- automatic family discovery beyond the bounded S1 workflow.

An external controller may create still assets after a reviewed v0.6 plan. A
`controller-cloud` declaration requires `cloud_upload_confirmed: true` in both
the Generation Request and Plan Review; it is a record of the controller's
behavior, not a permission for this CLI to upload assets. Do not send the
reference video or unapproved private assets to an external service.

Windows is the audited release platform for v0.6.0-alpha. It provides the
strong reparse-point and guarded snapshot/copy boundary for asset-pack scan,
rescan, and frozen-assets publication. Other operating systems remain
observable and fail closed where supported, but this release does not claim
an equivalent Windows NT no-delete guarantee. Renderer 0.2 binds each asset's
consumed bytes to its declared SHA-256; output-directory containment assumes
no hostile concurrent filesystem mutation during render and encode.

Asset Manifest 0.2.0 and its freeze report are locally asserted, hash-bound
records, not cryptographic proof of an approver or workflow provenance. The
governed workflow keeps the Proposal, Review, and freeze report together, but
a process that can rewrite the project can also author those JSON artifacts.
Use trusted signatures or access-controlled immutable storage if independent
approval must be enforceable.

Raw source and evidence remain local. Reference-video Proposal artifacts must
not contain the source-video or tool absolute paths, source-video filename,
container tags, title/artist/comments, account identity, raw probes, raw media,
or private evidence payloads. Their only allowed technical source fingerprint
is SHA-256, width, height, exact frame count, fps, and audio presence. The
separate local Asset Pack Proposal necessarily inventories normalized pack
filenames so exact slot stems can be reviewed; it still excludes absolute
paths and raw media. Bounded evidence references are for local review only.

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

## v0.6 Windows generation-bridge quick start

Use this path when you have a validated Template IR, a model reference and
garment/product/background reference files, but still need an external
image-generation or local CUDA tool to make the render-ready look images. The
external executor is deliberately outside this CLI. Start from a fresh local
project: reference/result packs are existing direct-child inputs, while each
named output directory must be a new direct child of the project.

~~~powershell
Set-Location .\skills\reference-video-rebuilder

$project = 'D:\video-projects\outfit-reel'
$ffmpeg = 'C:\tools\ffmpeg.exe'
$ffprobe = 'C:\tools\ffprobe.exe'
$templatePacket = 'template-compile/template.ir.json'
$template = Join-Path $project $templatePacket
$requestPacket = 'generation-request.json'
$request = Join-Path $project $requestPacket
~~~

Create `generation-request.json` from the installed
`generation-request.schema.json`; keep its requested model, outfit, product,
and optional background sources in the one direct child named
`generation-reference-pack`. Declare the executor (`local-file-drop` or
`controller-managed`), privacy profile, bounded `adapter_id`/`adapter_version`,
and `controller_label` when required. If the controller will use a cloud
service, set `cloud_upload_confirmed: true` in the request and in the approved
plan review before generating. `--generation-rights-confirmed` only confirms
processing rights; it is not cloud consent.

~~~powershell
python scripts/video_remix.py validate-generation-request "$request" --json
python scripts/video_remix.py prepare-generation "$templatePacket" "$requestPacket" --project-root $project --reference-pack generation-reference-pack --output-dir generation-plan --generation-rights-confirmed --ffprobe $ffprobe --timeout 60 --json

$planPacket = 'generation-plan/generation-plan.json'
$planReviewPacket = 'generation-plan/generation-plan-review.template.json'
$plan = Join-Path $project $planPacket
$planReview = Join-Path $project $planReviewPacket
python scripts/video_remix.py validate-generation-plan "$plan" --json
python scripts/video_remix.py validate-generation-plan-review "$planReview" --json
~~~

Inspect `generation-input-contact-sheet.png`, then edit the plan review to
explicitly approve every requested source-to-slot mapping, the chosen executor,
rights, and—when relevant—`cloud_upload_confirmed: true`. Only then ask the external
controller to generate stills or run the approved local CUDA workflow. The
controller writes its new result files to the direct child
`generation-result-pack`; do not modify `generation-plan`. The result pack must
contain exactly one static image with each non-passthrough target-slot stem.
Audio is a reviewed passthrough reference from `generation-reference-pack`, not
a generated result-pack file.

~~~powershell
python scripts/video_remix.py validate-generation-plan-review "$planReview" --json
python scripts/video_remix.py propose-generation-results "$planPacket" "$planReviewPacket" --project-root $project --result-pack generation-result-pack --output-dir generation-results-proposal --generation-results-rights-confirmed --ffprobe $ffprobe --timeout 60 --json

$resultsProposalPacket = 'generation-results-proposal/generation-results-proposal.json'
$resultsReviewPacket = 'generation-results-proposal/generation-results-review.template.json'
$resultsProposal = Join-Path $project $resultsProposalPacket
$resultsReview = Join-Path $project $resultsReviewPacket
python scripts/video_remix.py validate-generation-results-proposal "$resultsProposal" --json
python scripts/video_remix.py validate-generation-results-review "$resultsReview" --json
~~~

Inspect `generation-results-contact-sheet.png` and explicitly decide every
slot. Technical checks do not establish model identity, garment/product/logo
fidelity, body/pose, hands, or background correctness. To retry one rejected
slot, generate a complete new result pack and run a new result proposal/review;
do not overwrite the approved plan or an approved result.

~~~powershell
python scripts/video_remix.py validate-generation-results-review "$resultsReview" --json
python scripts/video_remix.py assemble-generation-pack "$planPacket" "$planReviewPacket" "$resultsProposalPacket" "$resultsReviewPacket" --project-root $project --output-dir generation-asset-pack --ffprobe $ffprobe --timeout 60 --json
~~~

The assembled directory contains only media with exact Template slot stems.
Static images are orientation-normalized, re-encoded as metadata-free PNG, and
audio is passed through. It is intentionally not a frozen Asset Manifest. Run
the existing v0.5 asset path next; it independently maps, reviews, snapshots,
and binds the bytes used by rendering.

~~~powershell
python scripts/video_remix.py propose-assets "$templatePacket" --project-root $project --asset-pack generation-asset-pack --output-dir asset-proposal --asset-pack-rights-confirmed --ffprobe $ffprobe --timeout 60 --json
# Review asset-proposal/asset-contact-sheet.png and its JSON review, then:
python scripts/video_remix.py freeze-assets 'asset-proposal/asset-pack-proposal.json' 'asset-proposal/asset-review-decision.template.json' --project-root $project --output-dir frozen-assets --ffprobe $ffprobe --timeout 60 --json
python scripts/video_remix.py render "$template" (Join-Path $project 'frozen-assets\assets.json') --project-root $project --ffmpeg $ffmpeg --summary run-summary.json --json
~~~

## v0.5 Windows asset-pack quick start

This v0.5 path starts with an already validated Template IR from the existing
v0.4 propose, review, freeze-plan, and compile flow. Run it from a fresh local
project so asset-proposal and frozen-assets do not already exist.

~~~powershell
Set-Location .\skills\reference-video-rebuilder

$project = 'D:\video-projects\outfit-reel'
$ffmpeg = 'C:\tools\ffmpeg.exe'
$ffprobe = 'C:\tools\ffprobe.exe'
$templatePacket = 'template-compile/template.ir.json'
$template = Join-Path $project $templatePacket
~~~

Place approved user-supplied bytes in one direct child named asset-pack. The
filenames below are examples only: each stem must exactly equal a slot_id in
the Template IR. The workflow does not infer that model.identity means a model
or that an outfit file is visually correct.

~~~text
asset-pack/
├── model.identity.png
├── outfit.01.png
├── …
├── outfit.12.png
├── product.01.png
├── …
├── product.12.png
├── background.png
└── audio.mka
~~~

Confirm rights before the command. The flag is mandatory before any asset-pack
analysis; it does not authorize a cloud upload or generated asset.

~~~powershell
python scripts/video_remix.py propose-assets "$templatePacket" --project-root $project --asset-pack asset-pack --output-dir asset-proposal --asset-pack-rights-confirmed --ffprobe $ffprobe --timeout 60 --json

$proposal = Join-Path $project 'asset-proposal\asset-pack-proposal.json'
$review = Join-Path $project 'asset-proposal\asset-review-decision.template.json'
$proposalPacket = 'asset-proposal/asset-pack-proposal.json'
$reviewPacket = 'asset-proposal/asset-review-decision.template.json'
python scripts/video_remix.py validate-asset-proposal "$proposal" --json
~~~

Review locally, not in a GUI:

1. Inspect asset-contact-sheet.png and the proposal inventory/candidates.
2. For every use mapping in the review JSON, explicitly confirm its content,
   media compatibility, render readiness, and rights.
3. Resolve every slot. An optional omission needs its explicit confirmation;
   required, missing, ambiguous, incompatible, or unresolved slots cannot
   freeze.
4. Mark the contact-sheet and local-only confirmations, then approve only the
   exact Proposal hash you reviewed.

~~~powershell
python scripts/video_remix.py validate-asset-review "$review" --json
python scripts/video_remix.py freeze-assets "$proposalPacket" "$reviewPacket" --project-root $project --output-dir frozen-assets --ffprobe $ffprobe --timeout 60 --json

$assets = Join-Path $project 'frozen-assets\assets.json'
python scripts/video_remix.py validate-assets "$template" "$assets" --project-root $project --json
python scripts/video_remix.py render "$template" "$assets" --project-root $project --ffmpeg $ffmpeg --summary run-summary.json --json
~~~

Template, Proposal, and Review paths above are normalized project-root-relative
paths. asset-pack and both output directories are direct child names, not
paths: absolute, nested, dot-segment, and existing output targets fail. The
workflow accepts no videos, animation, sidecars, or arbitrary media.

## 0.4.0-alpha reference-plan quick start

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
python scripts/video_remix.py validate-template "$template" --json

# Follow the v0.5 asset-pack quick start above. Do not hand-author a mutable
# assets.json in place of the human-reviewed frozen asset snapshot.
$assets = Join-Path $project 'frozen-assets\assets.json'
python scripts/video_remix.py validate-assets "$template" "$assets" --project-root $project --json
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

Confirm permission for the reference video, likenesses, products, logos, audio,
and every reference/result/asset-pack file before proposal. The CLI is
local-only: it does not upload media, evidence, proposal artifacts, prompts, or
derived data. A separately operated controller can use a cloud service only
after `cloud_upload_confirmed: true` is recorded in both the request and
reviewed plan; that consent does not change the CLI's offline behavior.

See the [Compiler Plan contract](skills/reference-video-rebuilder/references/compiler-contract.md),
[generation contract](skills/reference-video-rebuilder/references/generation-contract.md),
[asset contract](skills/reference-video-rebuilder/references/asset-contract.md),
[adapter policy](skills/reference-video-rebuilder/references/adapter-policy.md),
[QA gates](skills/reference-video-rebuilder/references/qa-gates.md), and the
complete Chinese [design](docs/DESIGN.zh-CN.md).

## License

Original code and documentation in this repository are licensed under Apache
License 2.0. Third-party tools, models, checkpoints, fonts, codecs, and media
retain their own licenses. See [THIRD_PARTY.md](THIRD_PARTY.md).
