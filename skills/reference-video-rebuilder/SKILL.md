---
name: reference-video-rebuilder
description: "Use for authorized reference-video rebuild workflows: bounded S1 static review/render, external still-image handoffs, v0.9 faithful/Jianying derivatives, v0.10 local temporal review/freeze, or the v0.10.1 no-API-key user-operated Higgsfield web handoff. Never claim built-in motion generation, voice cloning, lip sync, provider attestation, OCR, automatic semantic approval, or an editable/official Jianying project."
---

# reference-video-rebuilder

Use the normal automated new-reference route only for authorized local
fixed-subject-carousel S1 work. It treats the reference as structure and
timing, not pixels to copy. The deterministic renderer remains static-image/2D
composition plus selected audio; it does not produce subject motion, voice,
SFX, or lip sync.

Use every CLI contract through `python <skill-root>/scripts/video_remix.py`,
where `<skill-root>` is the installed Skill directory. Do not rely on a bare
command or the current working directory.

## Select the route

- Use `propose -> review -> freeze-plan -> compile`, then the strict asset
  freeze/render path, for authorized static S1 reconstruction. A manually
  authored/reviewed Template IR may use the four fixed delivery profiles, but
  automated proposal/compiler support remains portrait S1 only.
- Use the v0.6/v0.7 still-image bridge only for reviewed external static assets.
  It does not make `video_remix.py` networked, nor does it install a video
  model, CUDA route, shell, browser, or provider SDK.
- Use [faithful-rebuild-contract.md](references/faithful-rebuild-contract.md)
  only to preserve an authorized source exactly. It never removes, replaces,
  infers, or reconstructs visible content.
- Use [nle-delivery-contract.md](references/nle-delivery-contract.md) only for
  a separate re-encoded Jianying-compatible derivative. It is neither a
  faithful archive nor an editable/official Jianying project.
- Use [temporal-replacement-contract.md](references/temporal-replacement-contract.md)
  only when a reviewed Template IR 0.3 requires `motion_required: true` with
  `pose-transfer` or `video-to-video`. v0.10 accepts only a user-operated,
  local-only file drop and freezes approved bytes; it does not invoke, prove,
  or attest a provider.
- Use [higgsfield-web-handoff-contract.md](references/higgsfield-web-handoff-contract.md)
  only for the no-API-key v0.10.1 bridge above an already approved v0.10 Plan.
  The CLI prepares exact local files and normalizes a manual download; it never
  controls the browser or attests Higgsfield submission/output.

## Enforce the v0.10 temporal boundary

1. Require a reviewed Template IR 0.3, frozen Asset Manifest 0.2, selected
   frozen input slots, and one action-reference MP4 before planning.
2. Require `privacy_profile: local-only`, `execution_profile: local-file-drop`,
   and `cloud_upload_confirmed: false`. Keep the Request/Plan input set minimal
   and Manifest-bound. The user independently operates any local tool; this CLI
   never uploads, authenticates, generates, or calls a provider.
3. Accept only a new result pack containing exactly
   `temporal-replacement.mp4` with no inherited/user-authored metadata, then
   complete the technical checks and the two explicit human reviews.
4. Review full playback for action; face, hands, limbs, clothing/product
   continuity; timing; audio; rights; watermark absence; and, when required,
   scoped voice authorization/voice likeness and lip sync. Contact sheets,
   frame differences, stream facts, and hashes are not semantic proof.
   A voice assertion must remain current for prepare, propose, and freeze;
   historical verify rechecks its binding without requiring a new expiry.
5. Treat the byte-copy delivery as
   `completion: temporal_replacement_reviewed`,
   `bitstream_faithful: false`, and
   `provider_provenance: unattested-local-file-drop`. A frozen result is not a
   faithful archive or provider certificate.
6. Retry only with a new result pack and new proposal/review. Failed publication
   leaves no final target; ignored `.rrv-temporal-*` staging is never a result.

## Enforce the v0.10.1 browser handoff boundary

1. Keep the original v0.10 Plan/Manifest local-only. Require two fresh,
   expiring, exact-byte authorizations scoped to one Higgsfield Motion Control
   upload before creating `character.png` and silent `motion-reference.mp4`.
2. Immediately before upload/prompt entry/Generate, recheck the live origin,
   surface, model, 720p setting, exact two files, current displayed cost and
   balance. Ask the user to confirm that exact upload and billable action.
3. Stop if the live cost exceeds the Request cap. An older approval at a lower
   cost does not carry forward. Never auto-retry an unknown or failed action.
4. Treat the browser receipt and normalized video as
   `unattested-user-operated-web`. A manual download must still pass the full
   v0.10 Proposal, playback review, freeze, and verify chain.
5. Treat both single-use transitions as terminal: one private Handoff Request
   can issue only one action receipt, and one receipt can normalize only one
   result. A local failure after either private claim requires a fresh Request/
   Plan/confirmation; never delete ignored `.rrv-higgsfield-web-*-use-*`
   directories to bypass that gate.

## Shared non-negotiables

- Confirm rights for each source, likeness, voice, brand, product, audio,
  reference, and delivered file. Local records and hashes are audit bindings,
  not signatures or independent proof.
- Reject unknown, contradictory, unreviewed, unsafe-path, linked/reparse, or
  profile-incompatible inputs. Do not downgrade a dynamic request to a static
  render just to make it pass.
- Never treat technical decode, a contact sheet, retained audio, a moving still,
  or a provider declaration as proof of action reproduction, voice imitation,
  lip sync, semantic fidelity, or rights.
- Keep private packs, result media, prompts, credentials, and staging out of
  Git. Do not expose paths, API keys, or raw private instructions in public
  summaries.

## Read the detailed contract that matches the work

- [compiler-contract.md](references/compiler-contract.md) - S1
  proposal/review/freeze/compile.
- [asset-contract.md](references/asset-contract.md) - frozen Manifest 0.2 and
  local asset snapshot rules.
- [generation-contract.md](references/generation-contract.md) and
  [adapter-policy.md](references/adapter-policy.md) - reviewed still-image
  handoffs and the separate explicit OpenAI/Codex image surfaces.
- [motion-audio-contract.md](references/motion-audio-contract.md) - Template
  IR 0.3 requirements and the distinction between static rendering and v0.10
  temporal-result review.
- [temporal-replacement-contract.md](references/temporal-replacement-contract.md)
  - v0.10 commands, artifacts, profile, review, freeze, and verification.
- [higgsfield-web-handoff-contract.md](references/higgsfield-web-handoff-contract.md)
  - v0.10.1 exact upload preparation, action-time cost gate, and manual-result
    normalization.
- [qa-gates.md](references/qa-gates.md) - P0 checks and what technical evidence
  cannot prove.
- [support-levels.md](references/support-levels.md) - conservative S1-S4
  classification.
- [faithful-rebuild-contract.md](references/faithful-rebuild-contract.md) and
  [nle-delivery-contract.md](references/nle-delivery-contract.md) - the
  separate source-preservation and NLE derivative claims.

Use `controller_current` for semantic, rights, visual, motion, voice, lip-sync,
and release decisions. No model, local-tool declaration, or automated check can
bypass the recorded human reviews.
