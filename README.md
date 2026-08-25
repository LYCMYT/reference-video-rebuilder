# reference-video-rebuilder

reference-video-rebuilder is a Codex Skill and local CLI for rebuilding one
authorized, bounded reference-video family as a reusable template. The ordinary
clean-reconstruction path treats a reference as a structure and timing
specification, not pixels to copy. Platform UI, comments, account information,
and watermarks are excluded from that clean reconstruction; pixels fully hidden
by them are not recoverable.

A separately gated faithful source-preservation path is the narrow exception:
it preserves an authorized source rather than rebuilding it, so it cannot
remove, replace, infer, or reconstruct any visible content.

> Status: 0.10.0-alpha (faithful source preservation and temporal file-drop
> review are separate from the static template renderer; the local template
> executable remains static). The local, bounded new-reference path remains
> propose -> review -> freeze-plan -> compile. v0.6 adds the reviewed local
> bridge for externally created still assets. v0.7 adds one separate, explicit
> OpenAI GPT Image 2 API controller after an approved cloud plan. v0.7.1 also
> defines a no-API-key Codex built-in ImageGen handoff under a distinct approved
> cloud-controller declaration. v0.7.2 adds two fixed, audited landscape
> delivery profiles to the deterministic renderer only. v0.10 adds a separate
> provider-neutral local file-drop review/freeze chain for one user-operated
> local temporal MP4; it accepts only `local-only` + `local-file-drop` with no
> cloud upload, and neither invokes nor attests a provider. `video_remix.py`
> remains fully offline for this path: it never runs a model, shell command,
> network request, browser, CUDA job, weight download, or automatic approval.
> Automated new-reference work remains limited to authorized
> fixed-subject-carousel S1, not arbitrary-video discovery, semantic
> classification, OCR, or concealed-pixel recovery. The bundled renderer
> composites static images, 2D effects, and selected audio; it does not
> reproduce a person's continuous action, imitate a voice, rebuild SFX, or
> provide lip sync.

## What 0.10 adds

v0.10 adds an independent, provider-neutral **temporal replacement file-drop**
chain. It accepts only a reviewed Template IR 0.3 that requires
`pose-transfer` or `video-to-video`, a frozen Asset Manifest 0.2 with a
minimal selected input set, and one action-reference MP4. The CLI prepares and
reviews local packets; the Request must be exactly `privacy_profile: local-only`,
`execution_profile: local-file-drop`, and `cloud_upload_confirmed: false`.
The user independently operates any local tool, then creates a new local result
pack containing exactly metadata-clean `temporal-replacement.mp4`.

The local CLI technically rejects unsafe or mismatched media, creates contact
sheets and negative temporal checks, and requires full-playback human Plan and
Results Reviews. It cannot prove semantic action, face/hands/limbs, garment,
sound, voice, lip sync, rights, or provider behavior from those checks. Freeze
byte-copies the approved result and reports
`completion: temporal_replacement_reviewed`, `bitstream_faithful: false`, and
`provider_provenance: unattested-local-file-drop`. This is neither a faithful
archive nor provider attestation. Read the
[temporal replacement contract](skills/reference-video-rebuilder/references/temporal-replacement-contract.md).

## What 0.9.1 adds

v0.9.1 keeps three outputs deliberately separate:

- **Faithful archive** preserves approved source video packet payload and
  timing, preserves approved source audio or mutes it, and strips inherited or
  user-authored metadata. Its summary now binds the raw and canonical plan
  hashes, executor hash, invocation-policy hash, and bounded runtime versions.
- **Faithful review evidence** creates a deterministic, hash-bound contact
  sheet and JSON report from the manually reviewed text inventory. It uses no
  OCR or semantic inference and cannot prove that the reviewer omitted no
  visible text.
- **Jianying-compatible derivative** transcodes a local authorized MP4 to one
  fixed profile: H.264 High, 8-bit `yuv420p`, supported CFR, AAC-LC 48 kHz
  stereo when audio exists, `+faststart`, and cleared inherited metadata and
  rotation. This output is a re-encoded flat video with
  `bitstream_faithful: false`; it is not a Jianying project, editable layer
  package, official certification, or guarantee for every Jianying version.

The faithful archive remains the audit source of truth. The NLE derivative is
for practical import compatibility and must never replace or inherit the
faithful claim. See the
[NLE delivery contract](skills/reference-video-rebuilder/references/nle-delivery-contract.md).

## What 0.9 adds

v0.9 adds a separate, fail-closed **faithful source-preservation** operation
for an authorized, manually reviewed source. It does not create a template or
perform a clean-room reconstruction: it preserves the source video bitstream,
preserves the source audio bitstream or explicitly mutes it, and strips
inherited/user-authored container metadata. Unavoidable MP4 muxer structural
tags may remain. It verifies the declared source fingerprint and packet payload
equivalence after remuxing.

The reviewed plan uses `faithful-rebuild-plan.schema.json` `0.9.0` and requires
`rights_confirmed: true`, `operation: faithful-reference-rebuild`,
`visible_text_policy: preserve-exact`, a manually reviewed visible-text
inventory, `video_mode: preserve-bitstream`, an allowed audio mode, and
`metadata.strip_all: true`. The inventory is supplied by a reviewer; it is not
OCR and it does not permit text editing or semantic inference.

This path preserves the source picture, visible text, timing, and action. It
cannot remove platform UI, captions, watermarks, logos, comments, people,
products, backgrounds, or text; those requests change visible content and must
go through separately reviewed reconstruction work. Its success state is
`faithful_source_preservation`, never a claim of full reconstruction. Read the
[faithful source-preservation contract](skills/reference-video-rebuilder/references/faithful-rebuild-contract.md)
before using it.

## What 0.8 adds

v0.8 freezes a fail-closed acceptance contract for Template IR 0.3.0
`rebuild_requirements` object. It distinguishes requested subject motion from
static composition, and audio preservation/replacement from voice imitation or
lip sync. Its exact fields and enums are in the
[motion/audio contract](skills/reference-video-rebuilder/references/motion-audio-contract.md).

This is contract hardening, not a hidden motion-controller integration. The
current renderer still handles static images, 2D layout/transforms, effects,
and selected audio only. It must fail closed for pose transfer, video-to-video
motion, rebuilt SFX, authorized voice cloning, and lip sync. v0.10 separately
allows a user-operated, local-only temporal file drop to be reviewed and
byte-copy frozen; it does not integrate a controller or make the renderer
temporal. A Template IR
0.2.0 output has no such requirements and may be called only
`structure_only_unclaimed`—never a motion, voice-imitation, or lip-sync
reconstruction.

## What 0.7.2 adds

v0.7.2 documents a deliberately narrow renderer capability: in addition to
the existing portrait outputs, an already authored and reviewed Template IR can
encode to exactly `1280x720` or `1920x1080`. These are fixed H.264/yuv420p
delivery profiles, not an arbitrary landscape/aspect-ratio policy. All other
landscape dimensions fail before encoding writes an output.

This path was exercised with a clean-room, manually reviewed portal-reveal
benchmark. It reconstructs timing and compositing from approved **static**
replacement assets; it does not retain reference-video pixels, reproduce the
reference subject's actions, or make the source an automatic landscape
template. The new-reference `propose -> review ->
freeze-plan -> compile` path remains portrait-only, fixed-subject-carousel S1
automation with its existing 9:16 composition heuristic. Automatic landscape
analysis, classification, and compiler output are explicitly future work.

## What 0.7.1 adds

v0.7.1 closes the no-key controller-policy gap for Codex's built-in ImageGen.
This is a manually orchestrated `controller-managed` / `controller-cloud`
handoff, not `local-file-drop` and not the API controller. Use
`adapter_id: codex-builtin-imagegen`, `adapter_version: 2026-08-24`, a bounded
`controller_label`, and `cloud_upload_confirmed: true` in both the Generation
Request and approved Plan Review. No `OPENAI_API_KEY` is read or requested.

After plan approval, invoke built-in ImageGen once for each approved generated
slot, using only that task's approved reference images. Put selected results in
a new exact-slot result pack, then run the unchanged result review, assembly,
asset freeze, render, and QA gates. Do not send the reference video, audio,
packets, unrelated project files, or unapproved candidates to ImageGen. A
Codex-generated image is never self-approved, and a retry uses a new result
pack and review cycle.

Start from the bundled complete 26-task request at
`assets/project-template/generation.request.codex-builtin.example.json`. It
covers 12 generated outfit slots plus the required local audio, identity, and
12 product passthrough slots used by the bundled S1 template; replace only the
project-local filenames and reviewed instructions before `prepare-generation`.

OpenAI documents that built-in image generation uses `gpt-image-2`, accepts
reference images, and counts toward general Codex usage limits. It is distinct
from programmatic API generation, for which an API key and API pricing apply.
See the official [Codex image generation documentation](https://learn.chatgpt.com/docs/image-generation).

## What 0.7 adds

v0.7 adds `scripts/openai_image_controller.py`, a standalone, opt-in network
surface for a single reviewed `controller-cloud` + `controller-managed` plan.
It accepts only `adapter_id: openai-gpt-image-2` and
`adapter_version: 2026-04-21`, and its preflight is local, read-only, and makes
no network request. Its run requires three fresh explicit confirmations: rights,
cloud upload, and the billed request count (capped at 32).

The controller uses only `OPENAI_API_KEY` at run time—never an argument,
request/plan field, log, contact sheet, or output artifact. It uploads only
task-approved reference images and fixes every request to
`gpt-image-2-2026-04-21`, `high`, `1024x1536`, `png`, `opaque`, and `auto`
moderation; `input_fidelity` is deliberately omitted and it has no automatic
retry. On complete success it atomically publishes a new result pack containing
only metadata-free exact-slot PNG files. A failure publishes no result pack.

This is an OpenAI API controller, not a claim that a Codex in-app image feature
uses the same key, account, identity, entitlement, or billing. GPT Image can
use multiple reference images and processes `gpt-image-2` reference inputs at
high fidelity, but person consistency and precise composition still require
human review. The documented high-quality 1024x1536 output baseline is $0.165
per image plus input costs (up to $5.28 for 32 outputs before inputs); pricing
can change. See the official [Image generation guide](https://developers.openai.com/api/docs/guides/image-generation)
and [pricing page](https://platform.openai.com/pricing).

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

After an external controller or a user-operated local CUDA workflow places
generated files in a new result pack,
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
| Skill and governed workflow | 0.10.0-alpha (provider-neutral temporal file-drop review/freeze) |
| `video_remix.py` local CLI | 0.10.0-alpha |
| `openai_image_controller.py` | 0.7.0-alpha |
| Proposal JSON | 0.4.0 |
| Asset Pack Proposal and Review | 0.5.0 |
| Generation Request, Plan, and Result packets | 0.6.0 |
| Frozen Compiler Plan | 0.3.0 |
| Template IR | 0.2.0 legacy static renderer; 0.3.0 static-subset contract, with non-static requirements fail closed until a controller matches |
| Frozen Asset Manifest | 0.2.0 |
| Faithful Rebuild Plan | 0.9.0 |
| Faithful Evidence Report | 0.9.1 |
| Jianying-compatible derivative report | 0.9.1 (`jianying-compatible-v1`) |
| Temporal Request, Plan, Plan Review, Results Proposal/Review, Delivery Report | 0.10.0 |

The frozen Compiler Plan remains schema 0.3.0 so existing v0.3 Compiler Plan
consumers remain compatible. Deterministic compilation, rendering, and
technical QA retain their existing contracts. Template IR 0.3.0 is a new
motion/audio acceptance contract; it is not a claim that the current renderer
or any external controller has gained those capabilities.

The v0.9 Faithful Rebuild Plan is not a Template IR, Asset Manifest, proposal,
or generation contract. It is deliberately isolated so that source preservation
does not weaken the clean-reconstruction boundary or claim replacement,
semantic understanding, or full reconstruction.

The v0.10 temporal Request/Plan/Reviews/Delivery Report are also independent
packets. They bind one reviewed, user-operated local result to Template IR 0.3
and frozen Manifest 0.2, but do not change either contract, make the renderer
temporal, authorize cloud/provider work, or prove any provider generated the
result.

## v0.9 faithful source-preservation quick start

Prepare the `0.9.0` plan manually from the schema and contract; do not derive
its visible-text inventory with OCR. Validate it before opening the source for
the preservation run:

- schema: `<skill-root>/assets/schemas/faithful-rebuild-plan.schema.json`;
- example: `<skill-root>/assets/project-template/faithful.rebuild.plan.example.json`;
- source path spelling: project-relative `/`, for example `source/reference.mp4`;
- lowercase Windows SHA-256: `(Get-FileHash (Join-Path $project 'source\reference.mp4') -Algorithm SHA256).Hash.ToLowerInvariant()`.

The Alpha source profile is MP4 with one zero-rotation H.264 video, exact CFR,
no subtitle/data/attachment streams, at most 60 seconds/7,200 frames, and one
of `720x1280`, `1080x1920`, `1280x720`, or `1920x1080`.

```powershell
$skillRoot = 'C:\absolute\path\to\reference-video-rebuilder'
$project = 'D:\absolute\path\to\media-project'
$cli = Join-Path $skillRoot 'scripts\video_remix.py'
$plan = Join-Path $project 'faithful-rebuild-plan.json'
$ffmpeg = 'C:\absolute\path\to\ffmpeg.exe'
$ffprobe = 'C:\absolute\path\to\ffprobe.exe'

python $cli validate-faithful-plan $plan --json
python $cli faithful-rebuild $plan `
  --project-root $project `
  --output-dir faithful-rebuild `
  --ffmpeg $ffmpeg `
  --ffprobe $ffprobe `
  --timeout-seconds 60 `
  --json

python $cli faithful-evidence $plan `
  --project-root $project `
  --output-dir faithful-evidence `
  --ffmpeg $ffmpeg `
  --ffprobe $ffprobe `
  --max-panels 24 `
  --json
```

`faithful-rebuild` takes rights confirmation from the approved plan, not a
command-line flag. `--output-dir` must be a new safe project-root child. On
success it publishes `replica.mp4` and `rebuild-summary.json` under that
directory with completion `faithful_source_preservation`; it does not produce
a template or a content-rebuilt delivery.

To make a separate flat MP4 for Jianying import, transcode an authorized
project-local MP4 and then verify the derivative. Rights confirmation is an
explicit CLI gate because this is not the plan-authorized faithful operation:

The NLE Alpha input must use one of those four dimensions, exact CFR at
24/25/30/50/60 fps, at most 60 seconds, exactly one video stream, at most one
audio stream, zero rotation, and no subtitle/data/attachment streams.

```powershell
python $cli jianying-export (Join-Path $project 'faithful-rebuild\replica.mp4') `
  --project-root $project `
  --rights-confirmed `
  --output-dir jianying-delivery `
  --ffmpeg $ffmpeg `
  --ffprobe $ffprobe `
  --json

python $cli jianying-verify (Join-Path $project 'jianying-delivery\jianying-compatible-v1.mp4') `
  --project-root $project `
  --rights-confirmed `
  --ffmpeg $ffmpeg `
  --ffprobe $ffprobe `
  --json
```

The derivative is deliberately re-encoded and therefore never satisfies the
faithful bitstream contract. Keep its `nle-delivery-report.json` beside it and
retain the faithful archive and summary as the audit source.

## v0.10 temporal replacement quick start

Use this route only for a reviewed Template IR `0.3.0` whose
`rebuild_requirements` has `motion_required: true` and `motion_mode` of
`pose-transfer` or `video-to-video`. Keep it separate from the static renderer
and from faithful preservation. Start with the schema-valid
[request example](skills/reference-video-rebuilder/examples/temporal-replacement-request.example.json),
then replace its bounded fields for the project. The Request's `input_slot_ids`
may name only selected, rights-confirmed frozen Manifest slots. Its execution
triple is fixed: `local-only`, `local-file-drop`, and
`cloud_upload_confirmed: false`; do not use a cloud/controller declaration.

```powershell
$skillRoot = 'C:\absolute\path\to\reference-video-rebuilder'
$project = 'D:\absolute\path\to\media-project'
$cli = Join-Path $skillRoot 'scripts\video_remix.py'
$ffmpeg = 'C:\absolute\path\to\ffmpeg.exe'
$ffprobe = 'C:\absolute\path\to\ffprobe.exe'
$template = 'template.ir.json'
$manifest = 'frozen-assets/assets.json'
$request = 'temporal-request.json'

python $cli validate-temporal-request $request --json
python $cli prepare-temporal-replacement $template $manifest $request `
  --project-root $project `
  --reference-pack temporal-reference-pack `
  --temporal-rights-confirmed `
  --output-dir temporal-plan `
  --ffmpeg $ffmpeg `
  --ffprobe $ffprobe `
  --timeout-seconds 60 `
  --json

# Review and explicitly approve temporal-plan/temporal-replacement-plan-review.template.json.
$plan = 'temporal-plan/temporal-replacement-plan.json'
$planReview = 'temporal-plan/temporal-replacement-plan-review.template.json'
python $cli validate-temporal-plan $plan --json
python $cli validate-temporal-plan-review $planReview --json

# The user independently operates a local tool, then creates a new direct-child
# temporal-result-pack containing exactly metadata-clean temporal-replacement.mp4.
python $cli propose-temporal-results $plan $planReview `
  --project-root $project `
  --result-pack temporal-result-pack `
  --temporal-results-rights-confirmed `
  --output-dir temporal-results-proposal `
  --ffmpeg $ffmpeg `
  --ffprobe $ffprobe `
  --timeout-seconds 60 `
  --json

# Complete full-playback human review, then approve the Results Review.
$proposal = 'temporal-results-proposal/temporal-results-proposal.json'
$resultsReview = 'temporal-results-proposal/temporal-results-review.template.json'
python $cli validate-temporal-results-proposal $proposal --json
python $cli validate-temporal-results-review $resultsReview --json
python $cli freeze-temporal-delivery $plan $planReview $proposal $resultsReview `
  --project-root $project `
  --output-dir temporal-delivery `
  --ffmpeg $ffmpeg `
  --ffprobe $ffprobe `
  --timeout-seconds 60 `
  --json
python $cli verify-temporal-delivery 'temporal-delivery/temporal-delivery-report.json' `
  --project-root $project `
  --ffmpeg $ffmpeg `
  --ffprobe $ffprobe `
  --timeout-seconds 60 `
  --json
```

The reference and result must meet the strict MP4/CFR/H.264 High/8-bit
`yuv420p`/zero-rotation/at-most-60-seconds profile. If audio exists it must be
one AAC-LC 48 kHz stereo stream. Contact sheets, frame differences, stream
facts, hashes, and audio payload matching are only technical negative checks;
complete human action, face/hands/limbs, garment/product, timing, audio,
rights, and conditional voice/lip review remain mandatory. A rejected result
requires a new result pack and review cycle. A clone-voice authorization must
be unexpired at prepare, propose, and freeze; historical verify rechecks its
binding without renewing it. See the
[temporal replacement contract](skills/reference-video-rebuilder/references/temporal-replacement-contract.md)
for profile, artifact, and provenance details.

## Supported boundary

The automated new-reference path accepts only authorized fixed-subject-carousel
S1 work. A separately authored, visually reviewed Template IR may use one of
the four fixed renderer delivery profiles (`720x1280`, `1080x1920`, `1280x720`,
or `1920x1080`), but that does not expand proposal/compiler automation beyond
portrait S1. The bundled `video_remix.py` CLI is local and does not provide:

- OCR or arbitrary-video semantic classification;
- identity, garment, product, UI, watermark, or text meaning inference;
- a bundled image/video model, virtual try-on, CUDA inference, weight download,
  arbitrary shell execution, or network/upload operation;
- a built-in subject-motion, pose-transfer, video-to-video, SFX, voice, or
  lip-sync generator/controller. v0.10 only validates, reviews, and freezes an
  independently user-operated local temporal MP4; it never invokes or proves a
  provider;
- automatic approval or recovery of concealed pixels;
- automatic family discovery beyond the bounded S1 workflow.

These clean-reconstruction limits do not prevent the separate v0.9 faithful
source-preservation operation from carrying an authorized source forward
unchanged. That operation has no OCR, semantic analysis, replacement,
concealed-pixel recovery, or platform-element removal capability; it is not an
action-replication or voice-imitation engine.

In particular, it does not automatically analyze, classify, or compile an
arbitrary landscape reference. A landscape clean-room reconstruction requires a
manual/reviewed Template IR, the ordinary asset freeze, and the same full visual
QA gates as portrait work.

An external controller may create still assets after a reviewed v0.6 plan. The
approved networking surfaces are the explicit v0.7 OpenAI API controller and
the v0.7.1 manually orchestrated Codex built-in ImageGen handoff, each under its
own pinned `controller-cloud`/`controller-managed` declaration. Cloud
consent in the Generation Request and Plan Review is necessary but never broad
upload authority: do not send the reference video or unapproved private assets
to an external service.

No external motion controller is currently installed or connected. In
particular, a possible future Runway integration is not a bundled executor or
upload path. v0.10 accepts no provider/cloud/controller route: only a user
independently operating a local tool may place one result in its local file-drop
chain after Plan Review. Its declaration and frozen bytes remain
`unattested-local-file-drop`, not provider evidence.

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

# Optional v0.7 OpenAI controller only; it adds the OpenAI SDK.
python -m pip install -r .\skills\reference-video-rebuilder\requirements-openai-controller.txt

# Contributors: runtime dependencies plus development tooling.
python -m pip install -r .\requirements-dev.txt
~~~

After installing the Skill by itself, run the same runtime install command from
the installed directory that contains SKILL.md. Install the optional OpenAI
controller requirements there only when using its explicit v0.7 controller.
FFmpeg and ffprobe are external local executables.

## v0.7 OpenAI GPT Image 2 controller quick start

Use this optional path only after completing the local v0.6
`prepare-generation` step and manually approving its exact Plan Review. The
new controller is separate from `video_remix.py`; the latter stays offline.
Start the request from the bundled example, change only the task-local filenames
and instructions, then validate and prepare it through the v0.6 bridge:

~~~powershell
Set-Location .\skills\reference-video-rebuilder

$project = 'D:\video-projects\outfit-reel'
$ffprobe = 'C:\tools\ffprobe.exe'
$templatePacket = 'template-compile/template.ir.json'
$requestPacket = 'generation-request.openai.json'
$request = Join-Path $project $requestPacket

Copy-Item .\assets\project-template\generation.request.openai.example.json $request
# Edit only for this project. Keep these required values unchanged:
# controller-cloud / controller-managed / openai-gpt-image-2 / 2026-04-21.
python scripts/video_remix.py validate-generation-request "$request" --json
python scripts/video_remix.py prepare-generation "$templatePacket" "$requestPacket" --project-root $project --reference-pack generation-reference-pack --output-dir generation-plan --generation-rights-confirmed --ffprobe $ffprobe --timeout 60 --json
~~~

Inspect the input contact sheet and edit the pending Plan Review. It must be
approved, bind the exact plan, accept the requested tasks, and preserve
`cloud_upload_confirmed: true`. The OpenAI controller does not replace this
review or make an unapproved plan eligible.

~~~powershell
$planPacket = 'generation-plan/generation-plan.json'
$planReviewPacket = 'generation-plan/generation-plan-review.template.json'
$plan = Join-Path $project $planPacket
$planReview = Join-Path $project $planReviewPacket

python scripts/video_remix.py validate-generation-plan "$plan" --json
python scripts/video_remix.py validate-generation-plan-review "$planReview" --json
python scripts/openai_image_controller.py preflight "$planPacket" "$planReviewPacket" --project-root $project --generation-rights-confirmed --ffprobe $ffprobe --timeout-seconds 300 --json
~~~

Preflight makes no provider request, imports no OpenAI SDK, reads no API-key
environment, and writes neither project nor OS-temporary media. It can invoke
the user-selected local `ffprobe` while classifying a guarded pack, so treat
that executable as a trusted dependency. Read its approved task and reference
counts, choose a maximum request count that covers the tasks and is no greater
than 32, and obtain a fresh spend approval. Before running, make
`OPENAI_API_KEY` available to this process with an organization-approved
secret mechanism; do not put a key in the command, project JSON, or a log.

~~~powershell
# Requires OPENAI_API_KEY in the process environment. This command does not
# accept a key argument and never stores one in the project.
python scripts/openai_image_controller.py run "$planPacket" "$planReviewPacket" --project-root $project --output-dir generation-result-pack --generation-rights-confirmed --cloud-upload-confirmed --billable-requests-confirmed --max-billable-requests 12 --ffprobe $ffprobe --timeout-seconds 300 --json
~~~

The controller issues no automatic retry. It uploads only the accepted task
reference images and uses the fixed `gpt-image-2-2026-04-21` / high /
1024x1536 / PNG / opaque / auto contract (without `input_fidelity`). On complete
success, `generation-result-pack` contains only metadata-free
`<target_slot_id>.png` files. Any failure leaves no pack to review. Do not treat
a provider's high-fidelity reference handling as approval of identity, garment,
text/logo, or composition quality.

A failed multi-task run can still incur charges for requests already sent
before the failure, even though no result pack is published. Inspect the cause
and obtain all three confirmations again before any human-directed rerun.

Continue with the ordinary v0.6 result proposal/review and v0.5 asset freeze:

~~~powershell
python scripts/video_remix.py propose-generation-results "$planPacket" "$planReviewPacket" --project-root $project --result-pack generation-result-pack --output-dir generation-results-proposal --generation-results-rights-confirmed --ffprobe $ffprobe --timeout 60 --json
# Review generation-results-proposal locally, then validate its edited review.
python scripts/video_remix.py assemble-generation-pack "$planPacket" "$planReviewPacket" 'generation-results-proposal/generation-results-proposal.json' 'generation-results-proposal/generation-results-review.template.json' --project-root $project --output-dir generation-asset-pack --ffprobe $ffprobe --timeout 60 --json
~~~

The documented high-quality 1024x1536 output estimate is $0.165 per image plus
input costs; it is not a price guarantee. Check the official
[Image generation guide](https://developers.openai.com/api/docs/guides/image-generation)
and [pricing page](https://platform.openai.com/pricing) immediately before
approving a billable run.

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
platform elements, or commercial rights. For the fixed landscape profiles,
also confirm the intended 16:9 framing and complete playback; their acceptance
does not imply support for a new-reference landscape compiler.

For a Template IR 0.3.0 claim, validate `rebuild_requirements` before render
and again before final acceptance. Retaining the reference track is
`preserve-reference`, not voice imitation; a moving still image is
`layout-only`, not replicated subject action. The current portal-reveal request
requires `motion_required: true`, `motion_mode: video-to-video`,
`audio_mode: preserve-reference`, `lip_sync_required: false`, and
`voice_likeness_rights_confirmed: false`; the existing static portal output is
therefore `structure_only_unclaimed` until a reviewed local-only v0.10
video-to-video file-drop result passes full playback QA.

For a v0.10 temporal result, review the full action-reference and result, not
only contact sheets or frame metrics. Confirm motion/action, face/hands/limbs,
garment/product continuity, timing, audio treatment, rights, and watermark
absence; additionally confirm scoped voice authorization/voice likeness and lip
sync when requested. The frozen delivery is a byte-copy review record with
`bitstream_faithful: false` and
`provider_provenance: unattested-local-file-drop`, never a faithful or provider
claim. It may subsequently take the separate Jianying export/verify route.

Confirm permission for the reference video, likenesses, products, logos, audio,
and every reference/result/asset-pack file before proposal. `video_remix.py` is
local-only: it does not upload media, evidence, proposal artifacts, prompts, or
derived data. The separate v0.7 controller may use the OpenAI API only after
the exact reviewed cloud plan and fresh rights, upload, and billed-request
confirmations; that narrowly scoped consent does not change the offline
behavior of `video_remix.py`, nor authorize any other upload.

See the [Compiler Plan contract](skills/reference-video-rebuilder/references/compiler-contract.md),
[generation contract](skills/reference-video-rebuilder/references/generation-contract.md),
[asset contract](skills/reference-video-rebuilder/references/asset-contract.md),
[adapter policy](skills/reference-video-rebuilder/references/adapter-policy.md),
[QA gates](skills/reference-video-rebuilder/references/qa-gates.md),
[motion/audio contract](skills/reference-video-rebuilder/references/motion-audio-contract.md),
[temporal replacement contract](skills/reference-video-rebuilder/references/temporal-replacement-contract.md),
and the
complete Chinese [design](docs/DESIGN.zh-CN.md).

## License

Original code and documentation in this repository are licensed under Apache
License 2.0. Third-party tools, models, checkpoints, fonts, codecs, and media
retain their own licenses. See [THIRD_PARTY.md](THIRD_PARTY.md).
