---
name: reference-video-rebuilder
description: Build, explicitly review, freeze, and render authorized fixed-subject-carousel S1 templates; prepare and review strict local generation handoffs; and, only after an approved controller-cloud/controller-managed plan, operate the separate explicit OpenAI GPT Image 2 controller. Use for bounded reference-video proposal, generation-plan/result review, approved OpenAI-controller preflight/run, Template IR validation, asset-pack review/freeze, frozen asset rendering, and QA. Never claim that video_remix runs a model, shell, CUDA job, network/upload, weight download, automatic approval, arbitrary-video discovery, OCR, semantic inference, or hidden-pixel recovery.
---

# reference-video-rebuilder

Use this Skill only for authorized, fixed-subject-carousel S1 work. Treat a
reference as a structure and timing specification, not pixels to copy.

```text
propose -> review -> freeze-plan -> compile
                              -> prepare-generation -> plan review
                              -> local file drop or approved controller
                              -> [optional v0.7 OpenAI controller]
                              -> propose-generation-results -> result review
                              -> assemble-generation-pack
                              -> propose-assets -> asset review -> freeze-assets -> render
```

## Choose the path

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
- Use Propose-generation-results and Assemble-generation-pack after a result
  pack exists. Assembly does not replace the v0.5 asset review/freeze.
- Use Render only with a reviewed Template IR and the Asset Manifest 0.2.0
  produced by Freeze-assets. Inspect is diagnosis only; it writes nothing and
  never implies approval.

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
- Read [support-levels.md](references/support-levels.md) only to assess S1
  suitability.
- Read [model-routing.md](references/model-routing.md) before delegating work
  or accepting visual output.

Use `controller_current` for semantic decisions, cloud consent, plan/result
visual acceptance, and release acceptance. Use `gpt-5.6-terra` with max
reasoning only for bounded implementation after the contract is frozen. Neither
can bypass human review or P0 quality gates.
