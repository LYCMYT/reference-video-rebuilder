---
name: reference-video-rebuilder
description: Propose, explicitly review, hash-bind, and freeze an authorized local fixed-subject-carousel S1 plan, then compile or render the approved deterministic template. Use for bounded S1 reference-video proposal, review, freeze-plan, validation, and rendering; never promise arbitrary-video semantic discovery, OCR, cloud processing, asset generation, automatic approval, or hidden-pixel recovery.
---

# Reference Video Rebuilder

Use the local 0.4.0-alpha workflow only for an authorized
fixed-subject-carousel S1 reference:

~~~text
propose -> review -> freeze-plan -> compile -> render
~~~

Treat the reference as a structure and timing specification, not pixels to
copy. Exclude platform UI, comments, account information, and watermarks from
the reconstruction. Never claim recovery of content an overlay fully hides.

## Route the request

- Use Propose for a new, authorized local fixed-subject-carousel S1 source.
- Use Review when the user needs to inspect, correct, approve, reject, or
  validate a Proposal.
- Use Freeze only after an explicit approved, hash-bound Review exists.
- Use Compile only with a Frozen Compiler Plan.
- Use Remix for an approved Template IR and explicit render-ready asset mapping.
- Use Inspect for feasibility or diagnosis only; do not write artifacts or
  imply an approval.

Before Propose, Review, or Freeze, read
[compiler-contract.md](references/compiler-contract.md). Before delivery, read
[qa-gates.md](references/qa-gates.md). Read
[support-levels.md](references/support-levels.md) only when explaining whether
a request fits the bounded S1 family. Read the Template IR or asset contract
only when working on an existing template or asset mapping.

## Enforce the boundary

1. Confirm authorization for the reference, likenesses, products, brands, and
   audio. If it is absent or ambiguous, stop before proposal.
2. Keep source, evidence, and all processing local. Do not upload any media,
   frames, audio, Proposal, Review, or derived artifact.
3. Accept only exact-CFR, zero-rotation, no-more-than-60-second source media
   with available local FFmpeg and ffprobe.
4. Do not classify arbitrary video or infer identity, garments, products, UI,
   watermark, text, or concealed content.
5. Do not generate or source replacement assets. Render only user-supplied or
   separately approved render-ready assets.
6. Keep all writes under the project root. Do not store user media in the Skill
   checkout.

## Propose

Run doctor when local tools need confirmation. Then invoke the bounded proposal
command from the installed Skill directory:

~~~text
python scripts/video_remix.py propose <source> --project-root <project-root> --output-dir <output-dir> --template-id <template-id> [--slot-count-hint] --reference-rights-confirmed [--audio-rights-confirmed] [--audio-mode] [--output-profile] --ffmpeg <ffmpeg> --ffprobe <ffprobe> --json
~~~

For propose and freeze-plan, output-dir must be the name of one new direct
child of project-root. Reject absolute paths, nested paths, `.`, `..`, and
existing targets before any media operation or artifact write.

Propose returns exit code 0 only when it publishes a bounded local packet with
review_required true. That is a mandatory stop, never approval.

Inspect the local overview contact sheet, geometry preview, and timing profile.
The Proposal contains candidates for:

- source_rect as a maximal centered crop matching the supported 9:16 output
  aspect; use the full source only when it already matches;
- top carousel boundary and subject region;
- slot_count and switch timing;
- proportional carousel layout and background color.

Treat all candidates as correctable heuristics. The centered source crop is a
composition heuristic, not platform-chrome/UI semantic detection or removal.
Correct it when chrome, non-centered content, nonuniform crop, semantics, or
ambiguous timing make it wrong.

Proposal artifacts may carry only the safe technical source fingerprint:
SHA-256, width, height, exact frame_count, fps, and has_audio. Never expose a
source filename/path, tool path, container tags, title, artist, comments,
account identity, raw probe, raw media, or private evidence payload.

## Review and freeze

1. Validate the Proposal with validate-proposal.
2. Review the local artifacts. Do not infer approval from confidence,
   review_required, or a successful propose exit.
3. Make the Review explicitly approve the exact Proposal SHA-256 and set all
   confirmations true: family, geometry, slot_count, timing, carousel,
   background, audio, and authorization.
4. Allow the reviewer to correct approved_plan when the Proposal is wrong.
5. Validate the Review with validate-review.
6. Freeze only the matching approved pair:

~~~text
python scripts/video_remix.py freeze-plan <proposal> <review> --project-root <project-root> --output-dir <output-dir> --json
~~~

Validation and freeze errors exit code 2. Freeze must fail before final output
is written when binding, confirmation, rights, path, or plan validation fails.
Its success emits the canonical Frozen Compiler Plan schema 0.3.0. Do not
compile a Proposal or Review directly.

## Compile and render

Validate the frozen plan, then use the existing bounded compile command. Its
exit semantics remain unchanged: 0 for a completed compile without required
review, 1 when compile artifacts exist but review is required, and 2 for
validation or operational failure.

The compiler consumes Compiler Plan schema 0.3.0 and emits Template IR schema
0.2.0. Deterministic compiler, renderer, Template IR, and technical QA
contracts are unchanged by the Proposal workflow.

Before render, validate the Template IR and explicit asset mapping. Render only
after human/Codex review has accepted identity, garment/product fidelity,
residual platform elements, timing, and rights. A technical decode is not
visual or rights approval.

## Record and report

Record source/asset hashes, local artifacts, approved review, frozen plan,
tool versions, Template IR version, render settings, output hashes, warnings,
and human acceptance. Return compact summaries and project-relative paths; keep
detailed evidence local.

Use controller_current for semantic questions and final acceptance. Use
gpt-5.6-terra with max reasoning only for nontrivial implementation against
already frozen contracts. Neither may bypass explicit review or quality gates.
