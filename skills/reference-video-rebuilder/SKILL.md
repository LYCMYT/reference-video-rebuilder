---
name: reference-video-rebuilder
description: Build, explicitly review, freeze, and render authorized fixed-subject-carousel S1 templates; prepare and review external-generation asset handoffs; and freeze strict local asset packs. Use for bounded reference-video proposal, generation-plan/result review, Template IR validation, asset-pack review/freeze, frozen asset rendering, and local QA. Never claim that the CLI runs a model, shell, CUDA job, network/upload, weight download, automatic approval, arbitrary-video discovery, OCR, semantic inference, or hidden-pixel recovery.
---

# reference-video-rebuilder

Use the local 0.6.0-alpha workflow only for authorized,
fixed-subject-carousel S1 work:

~~~text
propose -> review -> freeze-plan -> compile
                              -> prepare-generation -> plan review
                              -> external controller or local file drop
                              -> propose-generation-results -> result review
                              -> assemble-generation-pack
                              -> propose-assets -> asset review -> freeze-assets -> render
~~~

When an approved Template IR already exists, begin with propose-assets for
already render-ready media; otherwise begin with prepare-generation, finish
both generation reviews, assemble, then enter propose-assets. Treat the
reference as a structure and timing specification, not pixels to copy.

## Choose the path

- Use Propose, Review, and Freeze-plan only for a new authorized local S1
  reference.
- Use Propose-assets for a locally supplied pack against an existing Template
  IR. It can inventory files and make exact filename candidates, never decide
  what an asset depicts.
- Use Prepare-generation only when a user has supplied an explicit Generation
  Request and a local reference pack and an existing Template IR needs
  render-ready stills. It creates a review-required plan; it does not run a
  model or create a look.
- Use Propose-generation-results only after a reviewer has approved that plan
  and an external controller or local CUDA operator has placed a new result
  pack on disk. It creates a second, per-slot review; it does not infer whether
  the generated person, garment, product, background, or logo is correct.
- Use Assemble-generation-pack only after both generation reviews are
  explicitly approved. It produces media-only, exact-slot files for the v0.5
  asset proposal; it does not replace the v0.5 asset review/freeze.
- Use Freeze-assets only after a human explicitly approves every mapping.
- In the governed path, use Render with a reviewed Template IR and the Asset
  Manifest 0.2.0 produced by Freeze-assets. The renderer verifies local
  declarations, hashes, and bound asset bytes; it does not authenticate who
  created a manifest or prove that human review occurred. Keep the Proposal,
  Review, and freeze report as the local audit packet. Legacy Asset Manifest
  0.1.0 remains compatibility input, not the strict asset-pack result.
- Use Inspect for feasibility or diagnosis only; do not write artifacts or
  imply approval.

## Enforce the boundary

1. Confirm rights for the reference, likenesses, products, brands, audio,
   reference-pack files, result-pack files, and asset-pack files before
   processing. Generation commands require explicit rights flags before their
   guarded analysis.
2. Keep the CLI local. It must not invoke a model, arbitrary shell, CUDA
   runtime, network/upload, provider SDK, browser, or weight download. A
   controller-managed cloud executor requires `cloud_upload_confirmed: true`
   in both the Generation Request and reviewed Plan Review; that declaration
   never authorizes this CLI to upload. The contact sheet plus JSON review is
   not a GUI.
3. Keep every packet path normalized and relative to the project root. Asset,
   generation-reference, and generation-result packs are guarded direct-child
   inputs; workflow output directories are new direct project-root children.
   Reject absolute, nested, dot-segment, link/reparse, and existing output
   paths.
4. Accept only direct regular pack files: static JPEG, PNG, or WebP, plus
   WAV, MP3, M4A, or MKA audio that local ffprobe can inspect through pipe:0.
   Fail the entire pack for unknown files, videos, animation, sidecars,
   directories, links, or reparse points.
   A v0.6 result pack is stricter: it contains exactly one static image for
   each non-passthrough target slot; approved audio is a reference-pack
   passthrough.
5. Match a file only when its exact stem equals a Template IR slot_id. Do not
   use OCR, visual inspection, fuzzy names, or semantic guesses to create a
   candidate.

## Generation bridge procedure

Read [generation-contract.md](references/generation-contract.md) before
creating a Generation Request or asking any controller to create an image.
Choose only `local-file-drop` or `controller-managed`; do not introduce a
`local-command` adapter in this Alpha. Record either `local-only` or
`controller-cloud`; select the latter only with `controller-managed` and after setting
`cloud_upload_confirmed: true` in both required packets. Record only bounded
`adapter_id`/`adapter_version` and `controller_label` where required—never a
path, URL, or credential.

Run the commands from the installed Skill directory:

~~~text
python scripts/video_remix.py validate-generation-request <request.json> --json
python scripts/video_remix.py prepare-generation <template> <request> --project-root <project-root> --reference-pack <direct-child-pack> --output-dir <direct-child-output> --generation-rights-confirmed --ffprobe <ffprobe> --timeout <seconds> --json
python scripts/video_remix.py validate-generation-plan <generation-plan.json> --json
python scripts/video_remix.py validate-generation-plan-review <generation-plan-review.json> --json

# Run the approved external controller/local CUDA workflow outside this CLI.
python scripts/video_remix.py propose-generation-results <plan> <plan-review> --project-root <project-root> --result-pack <direct-child-pack> --output-dir <direct-child-output> --generation-results-rights-confirmed --ffprobe <ffprobe> --timeout <seconds> --json
python scripts/video_remix.py validate-generation-results-proposal <generation-results-proposal.json> --json
python scripts/video_remix.py validate-generation-results-review <generation-results-review.json> --json
python scripts/video_remix.py assemble-generation-pack <plan> <plan-review> <results-proposal> <results-review> --project-root <project-root> --output-dir <direct-child-output> --ffprobe <ffprobe> --timeout <seconds> --json
~~~

Inspect the input and result contact sheets locally. Explicitly approve each
planned source mapping and each generated result. Confirm identity, body/pose,
garment/product/background fidelity, logos/text, hands/artifacts, render
readiness, rights, executor profile, and any cloud consent. Retry a rejected
slot through a new result pack/proposal/review; never overwrite an approved
plan or approved output. Assemble normalizes image orientation and metadata,
but semantic/visual acceptance remains human work.

## Asset-pack procedure

Run the commands from the installed Skill directory:

~~~text
python scripts/video_remix.py propose-assets <project-relative-template> --project-root <project-root> --asset-pack <direct-child-pack> --output-dir <direct-child-output> --asset-pack-rights-confirmed --ffprobe <ffprobe> --timeout <seconds> --json
python scripts/video_remix.py validate-asset-proposal <proposal.json> --json
python scripts/video_remix.py validate-asset-review <review.json> --json
python scripts/video_remix.py freeze-assets <project-relative-proposal> <project-relative-review> --project-root <project-root> --output-dir <direct-child-output> --ffprobe <ffprobe> --timeout <seconds> --json
~~~

Propose-assets writes asset-pack-proposal.json, an
asset-review-decision.template.json, and asset-contact-sheet.png. It always
requires review. Inspect the contact sheet locally, then explicitly decide
every Template slot. Every use mapping must confirm content,
media compatibility, render readiness, and rights. Do not approve missing,
ambiguous, incompatible, or unresolved mappings.

Freeze-assets binds the approved Proposal, Review, Template, and inventory
hashes; safely rescans the pack; and atomically publishes the local-only Asset
Manifest 0.2.0 at frozen-assets/assets.json with opaque flat asset copies and
asset-freeze-report.json. Do not edit or render a mutable pack as though it
were frozen.

Treat the manifest and report as locally asserted, hash-bound records, not a
trusted signature. A process with write access to the project can author or
replace local JSON. If independent approval must be enforceable, add a trusted
signer or an access-controlled immutable store outside this Alpha.

## Load detailed contracts

- Read [compiler-contract.md](references/compiler-contract.md) for a new
  reference-plan workflow.
- Read [asset-contract.md](references/asset-contract.md) before preparing,
  reviewing, freezing, or validating replacement assets.
- Read [generation-contract.md](references/generation-contract.md) before
  preparing a plan, receiving external results, or assembling a generation
  pack.
- Read [adapter-policy.md](references/adapter-policy.md) before choosing a
  controller, privacy profile, or local CUDA file-drop workflow.
- Read [qa-gates.md](references/qa-gates.md) before accepting a render.
- Read [support-levels.md](references/support-levels.md) only to assess S1
  suitability.
- Read [model-routing.md](references/model-routing.md) before delegating
  implementation or final acceptance.

Use controller_current for semantic decisions, plan/result visual acceptance,
and release acceptance. Use gpt-5.6-terra with max reasoning only for bounded
implementation after the controller has frozen the generation/asset/renderer
contract. Neither can bypass human review or P0 quality gates.
