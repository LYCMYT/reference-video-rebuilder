---
name: reference-video-rebuilder
description: Use for authorized fixed-subject-carousel S1 template rebuilds, strict local proposal/assets/review/QA workflow, approved still-image generation handoffs, or v0.9 faithful source preservation with v0.9.1 review evidence and a separate Jianying-compatible derivative. Never confuse faithful preservation with a re-encoded NLE delivery, claim OCR completeness, generated motion/voice/lip sync, visible-content replacement, or an official Jianying project/certification.
---

# reference-video-rebuilder

Use the automated new-reference path only for authorized,
fixed-subject-carousel S1 work. The ordinary path treats a reference as a
structure and timing specification, not pixels to copy. A manually authored
and visually reviewed Template IR may use one of the four fixed output
profiles, including 16:9, but this does not imply automatic landscape analysis,
support-level classification, or compilation.

The distinct v0.9 faithful source-preservation path is only for an authorized
source whose visible picture, text, timing, and action must remain unchanged.
It is source preservation, not a template rebuild or a way to remove platform
elements, replace assets, or recover hidden pixels.

v0.9.1 can generate local human-review evidence for that plan and a separate
flat Jianying-compatible MP4 derivative. Evidence performs no OCR and cannot
prove inventory completeness. The NLE derivative is re-encoded and always
`bitstream_faithful: false`; it is not an editable Jianying project or official
compatibility certification.

```text
propose -> review -> freeze-plan -> compile
                              -> prepare-generation -> plan review
                              -> local file drop or approved controller
                              -> [optional v0.7.1 Codex built-in ImageGen]
                              -> [optional v0.7 OpenAI API controller]
                              -> propose-generation-results -> result review
-> assemble-generation-pack
-> propose-assets -> asset review -> freeze-assets -> render
```

Before accepting a delivery that mentions subject action, voice imitation, or
mouth/audio synchronization, read [motion-audio-contract.md](references/motion-audio-contract.md).
The bundled renderer is static-image/2D compositing plus selected audio only.
Static-image transforms, cross-fades, or retained audio never establish
character-motion replication, voice imitation, or lip sync.

## Choose the path

- Use faithful source preservation only with a human-reviewed v0.9 plan that
  inventories all visible text and declares exact preservation, source-video
  bitstream preservation, permitted audio treatment, and inherited/user-authored
  metadata stripping (not unavoidable muxer structural tags).
  It has no OCR, semantic inference, replacement, visible-content removal, or
  full-reconstruction claim. Read
  [faithful-rebuild-contract.md](references/faithful-rebuild-contract.md)
  before preparing or accepting this path.
  This Alpha accepts only MP4 with exactly one zero-rotation H.264 video,
  exact CFR, at most 60 seconds, no subtitle/data/attachment streams, and one
  of `720x1280`, `1080x1920`, `1280x720`, or `1920x1080`.
  Resolve `<skill-root>` to this installed Skill directory. Validate first with
  `python <skill-root>/scripts/video_remix.py validate-faithful-plan
  <plan> --json`, then run `python <skill-root>/scripts/video_remix.py faithful-rebuild
  <plan> --project-root <dir> [--output-dir
  <new-child>] [--ffmpeg <path>] [--ffprobe <path>] [--timeout-seconds <n>]
  --json`. Rights confirmation is inside the approved plan, not a CLI flag.
- Use `python <skill-root>/scripts/video_remix.py faithful-evidence <plan> --project-root <dir>
  [--output-dir <new-child>] [--max-panels 24] --json` only to support human
  review. Inspect the contact sheet yourself; never treat it as OCR or proof
  that no visible text item was omitted.
- Use `python <skill-root>/scripts/video_remix.py jianying-export <project-local-mp4> --project-root <dir>
  --rights-confirmed [--output-dir <new-child>] --json`, followed by
  `python <skill-root>/scripts/video_remix.py jianying-verify <delivery-mp4> --project-root <dir>
  --rights-confirmed --json`, only for a separate flat NLE derivative. Keep the
  faithful archive as the audit source and never attach its faithful claim to
  the derivative. This Alpha accepts only the four dimensions above, exact CFR
  at 24/25/30/50/60 fps, at most 60 seconds, exactly one video stream, at most
  one audio stream, zero rotation, and no subtitle/data/attachment streams.
- Use Propose, Review, and Freeze-plan only for a new authorized local S1
  reference.
- Use Propose-assets for already render-ready local media against an existing
  Template IR. It can make exact filename candidates, never decide what an
  asset depicts.
- Use Prepare-generation to make a review-required v0.6 plan from a local
  reference pack. `video_remix.py` remains fully offline: it only prepares,
  validates, reviews, and assembles local files.
- Use the standalone v0.7 OpenAI GPT Image 2 controller only after an approved
  `controller-cloud` + `controller-managed` plan pins
  `openai-gpt-image-2` / `2026-04-21`. It is not a `video_remix` subcommand or
  a Codex built-in image tool.
- Use Codex built-in ImageGen without an API key only after a separate approved
  `controller-cloud` + `controller-managed` plan pins
  `codex-builtin-imagegen` / `2026-08-24`. Invoke it once per approved generated
  slot using only that task's approved reference images. It is not
  `local-file-drop`, does not make `video_remix.py` networked, and never bypasses
  result review or asset freeze.
- Use Propose-generation-results and Assemble-generation-pack after a result
  pack exists. Assembly does not replace the v0.5 asset review/freeze.
- Use Render only with a reviewed Template IR and the Asset Manifest 0.2.0
  produced by Freeze-assets. Inspect is diagnosis only; it writes nothing and
  never implies approval.
- For a Template IR 0.3.0 delivery claim, require a valid
  `rebuild_requirements` object and an execution route that can meet every
  declared motion/audio requirement. Missing, contradictory, or unsupported
  requirements fail closed; never downgrade a request to static composition.
- Treat every legacy Template IR 0.2.0 output as
  `structure_only_unclaimed`. It may be reviewed for structure, timing, static
  appearance, effects, and selected audio, but never for subject-motion
  replication, voice imitation, or lip sync.
- Use a manually authored/reviewed landscape Template IR only for the exact
  `1280x720` or `1920x1080` delivery profiles. Do not route a landscape
  reference through `propose` or `compile`: that automated path remains the
  portrait 9:16 S1 family and is future work for landscape.

## Enforce the boundary

1. Confirm rights for every reference, likeness, product, brand, audio, and
   result before processing. Human review decides semantics and visual quality.
2. Keep all v0.6 `video_remix` work local. It must not invoke a model, shell,
   CUDA runtime, network/upload, provider SDK, browser, or weight download.
3. Keep packet paths normalized and project-root-relative. Packs are guarded
   direct children; output directories are new direct children. Reject
   absolute, nested, dot-segment, link/reparse, and existing output paths.
4. Match assets only by exact Template slot stem. Do not use OCR, visual
   guesses, or fuzzy names to create a candidate.
5. Treat contact sheets, hashes, and media probes as technical evidence only.
   They do not establish identity, pose, garment/product/logo fidelity, rights,
   or removal correctness.
6. Do not accept a static path for `motion_required: true`,
   `motion_mode: pose-transfer`/`video-to-video`,
   `lip_sync_required: true`, `audio_mode: rebuild-sfx`, or
   `audio_mode: clone-authorized-voice`. No motion/voice controller is
   integrated today; an external provider is only a future reviewed path.
7. Do not route a request to remove, replace, translate, synthesize, or infer
   visible content through faithful source preservation. It preserves approved
   source video/text/action exactly and either preserves the approved source
   audio bitstream or mutes it; it is never a full reconstruction claim.
8. Keep faithful archive, review evidence, and NLE derivative as separate
   output directories and claims. NLE verification does not prove actual
   import in every Jianying release or create editable tracks, captions,
   effects, or layers.

## Use Codex built-in ImageGen deliberately

Read [generation-contract.md](references/generation-contract.md),
[adapter-policy.md](references/adapter-policy.md), and
[qa-gates.md](references/qa-gates.md) first.

- Require the approved request and Plan Review to record
  `controller-cloud`, `controller-managed`, `codex-builtin-imagegen`,
  `2026-08-24`, a bounded controller label, and
  `cloud_upload_confirmed: true`.
- Start from the complete 26-task
  `assets/project-template/generation.request.codex-builtin.example.json` for
  the bundled S1 template; it includes all required passthrough and generated
  slots, and still requires a new reviewed Plan.
- Built-in ImageGen needs no `OPENAI_API_KEY`; it uses Codex product access and
  usage limits. Never describe it as the API controller or infer API billing,
  organization, project, or credential identity.
- Send only the approved identity/garment/product/background reference images
  for the current task. Never send video, audio, packets, unrelated assets, or
  rejected candidates.
- Make one tool call per distinct output asset. Store only the selected image
  under the exact target-slot filename in a new result pack. No automatic retry
  or silent overwrite is allowed.
- The current primary model must inspect every result and record the result
  review. Continue through assembly, v0.5 asset review/freeze, render, and full
  visual QA.

## Use the v0.7 OpenAI controller deliberately

Read [generation-contract.md](references/generation-contract.md) and
[adapter-policy.md](references/adapter-policy.md) before running it.

- Run its preflight before any network request or output write. It validates the
  approved plan and reports the bounded task and approved-reference counts.
- Run it only with separate explicit confirmations for rights, cloud upload,
  and the capped billed request count. It has no automatic retry.
- It uses only `OPENAI_API_KEY` at run time. Never put a key in a flag, request,
  plan, log, contact sheet, result pack, or other artifact. Do not assume a
  Codex in-app image feature shares this API key, identity, account, or billing.
- Upload only reference images approved by the plan's accepted tasks. The
  controller fixes the model/output contract and atomically publishes only
  metadata-free PNG files on success. A failure publishes no result pack.
- Review every returned image, then continue through the unchanged v0.6 result
  review and v0.5 asset-freeze gates.

## Load detailed contracts

- Read [compiler-contract.md](references/compiler-contract.md) for a new
  reference-plan workflow.
- Read [asset-contract.md](references/asset-contract.md) before preparing,
  reviewing, freezing, or validating replacement assets.
- Read [generation-contract.md](references/generation-contract.md) before
  preparing a plan, using the v0.7 controller, receiving results, or assembling
  a generation pack.
- Read [adapter-policy.md](references/adapter-policy.md) before choosing a
  controller, privacy profile, or local file-drop workflow.
- Read [qa-gates.md](references/qa-gates.md) before accepting a result or
  render.
- Read [motion-audio-contract.md](references/motion-audio-contract.md) before
  classifying motion/audio, authoring Template IR 0.3.0, choosing a controller,
  or accepting a result that claims performance, voice, or lip sync.
- Read [faithful-rebuild-contract.md](references/faithful-rebuild-contract.md)
  before preparing or accepting v0.9 faithful source preservation.
- Read [nle-delivery-contract.md](references/nle-delivery-contract.md) before
  creating, verifying, or describing a v0.9.1 Jianying-compatible derivative.
- Read [support-levels.md](references/support-levels.md) only to assess S1
  suitability.
- Read [model-routing.md](references/model-routing.md) before delegating work
  or accepting visual output.

Use `controller_current` for semantic decisions, cloud consent, motion/audio
requirements, plan/result visual acceptance, and release acceptance. Use
`gpt-5.6-terra` with max reasoning only for bounded implementation after the
contract is frozen. Neither can bypass human review or P0 quality gates.
