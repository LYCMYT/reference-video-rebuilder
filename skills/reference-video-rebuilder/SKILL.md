---
name: reference-video-rebuilder
description: Build, explicitly review, freeze, and render only authorized local fixed-subject-carousel S1 templates and strict local asset packs. Use for bounded reference-video proposal, asset-pack proposal/review/freeze, Template IR validation, frozen asset rendering, and local QA; never promise arbitrary-video discovery, OCR, cloud processing, asset generation, automatic approval, or hidden-pixel recovery.
---

# reference-video-rebuilder

Use the local 0.5.0-alpha workflow only for authorized,
fixed-subject-carousel S1 work:

~~~text
propose -> review -> freeze-plan -> compile
                              -> propose-assets -> asset review -> freeze-assets -> render
~~~

When an approved Template IR already exists, begin with propose-assets. Treat
the reference as a structure and timing specification, not pixels to copy.

## Choose the path

- Use Propose, Review, and Freeze-plan only for a new authorized local S1
  reference.
- Use Propose-assets for a locally supplied pack against an existing Template
  IR. It can inventory files and make exact filename candidates, never decide
  what an asset depicts.
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

1. Confirm rights for the reference, likenesses, products, brands, audio, and
   every asset-pack file before processing. Propose-assets requires the
   explicit --asset-pack-rights-confirmed flag before it touches the project.
2. Keep media, evidence, packets, and processing local. Do not upload,
   generate, source, or use cloud assets. The contact sheet plus JSON review
   is not a GUI.
3. Keep every input, proposal, and review path normalized and relative to the
   project root. Asset-pack and output-dir are names of direct project-root
   children; reject absolute, nested, dot-segment, and existing output paths.
4. Accept only direct regular pack files: static JPEG, PNG, or WebP, plus
   WAV, MP3, M4A, or MKA audio that local ffprobe can inspect through pipe:0.
   Fail the entire pack for unknown files, videos, animation, sidecars,
   directories, links, or reparse points.
5. Match a file only when its exact stem equals a Template IR slot_id. Do not
   use OCR, visual inspection, fuzzy names, or semantic guesses to create a
   candidate.

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
- Read [qa-gates.md](references/qa-gates.md) before accepting a render.
- Read [support-levels.md](references/support-levels.md) only to assess S1
  suitability.
- Read [model-routing.md](references/model-routing.md) before delegating
  implementation or final acceptance.

Use controller_current for semantic decisions and release acceptance. Use
gpt-5.6-terra with max reasoning only for implementation after the controller
has frozen the asset and renderer contract. Neither can bypass the human review
or P0 quality gates.
