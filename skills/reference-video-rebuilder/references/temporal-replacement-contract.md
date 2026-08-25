# Temporal replacement contract (v0.10.0-alpha)

## Contents

- [Scope and claim boundary](#scope-and-claim-boundary)
- [Eligibility and bounded inputs](#eligibility-and-bounded-inputs)
- [Local CLI and review artifacts](#local-cli-and-review-artifacts)
- [Technical checks versus human acceptance](#technical-checks-versus-human-acceptance)
- [Freeze, retries, and later NLE delivery](#freeze-retries-and-later-nle-delivery)

## Scope and claim boundary

Use v0.10 only to review and freeze one temporal replacement file created by a
user-operated local tool. It is an independent, provider-neutral local
file-drop chain; it does not change Template IR 0.3.0, the static generation
bridge, Asset Manifest 0.2.0, or the deterministic renderer.

`video_remix.py` never calls a provider, reads an API key, opens a browser,
starts a shell/CUDA job, uploads media, or generates/transcodes the result.
It only validates local packets, probes/decode-checks staged local snapshots,
creates review evidence, and byte-copies an approved result into a frozen
delivery. The v0.10 Alpha permits only `privacy_profile: local-only`,
`execution_profile: local-file-drop`, and `cloud_upload_confirmed: false`.
It does not authorize a cloud, controller, provider, or API route. `adapter_id`,
`adapter_version`, and capability declarations are local tool declarations, not
provider proof.

Every frozen report fixes `provider_provenance` to
`unattested-local-file-drop`. Do not call a plan, hash, contact sheet, result,
or report an attestation that a named provider/model created the video.

```text
Template IR 0.3 + frozen Manifest 0.2 + Temporal Request + action-reference MP4
  -> prepare-temporal-replacement
  -> Plan + human Plan Review
  -> user independently operates a local tool -> new local result pack
  -> propose-temporal-results
  -> technical negative checks + human Results Review
  -> freeze-temporal-delivery (byte copy) -> verify-temporal-delivery
```

## Eligibility and bounded inputs

Require a reviewed Template IR `0.3.0` with `support.review_required: false`,
`motion_required: true`, and exactly `motion_mode: pose-transfer` or
`video-to-video`. Select one existing Template output through the Request's
`output_id`. A static or layout-only Template cannot enter this route.

Require a frozen local-only Asset Manifest `0.2.0`. The private Request names
only `input_slot_ids`; each selected slot must exist in that Manifest, have
rights confirmed, retain `cloud_upload_allowed: false`, and match its frozen
bytes. Its privacy/execution declarations must be exactly `local-only`,
`local-file-drop`, and `cloud_upload_confirmed: false`; a cloud/provider/
controller declaration is not accepted by this Alpha. For those selected
inputs, the public Plan records only an opaque sorted `input_assets` list
(`slot_id`, SHA-256, media type) plus its
canonical SHA-256. Never broaden the input set from a prompt, filename, contact
sheet, or external instruction. The Plan also binds the resolved
motion/audio/voice/lip requirements with canonical
`requirements_sha256`; the Proposal and Delivery Report retain that binding.

Supply one guarded direct-child action-reference pack with exactly one MP4 and
no sidecars. Supply a distinct new guarded direct-child result pack with
exactly one file named `temporal-replacement.mp4`. Both files must be at most
60 seconds, exact CFR, zero rotation, MP4/H.264 High/8-bit `yuv420p`, and fully
locally decodable. When audio exists it must be exactly one AAC-LC, 48 kHz,
stereo stream; otherwise no audio stream or audio fields are allowed. The
action reference must match the Template source geometry/timing; the result
must match the selected output geometry and the Template source FPS/frame
count. The candidate result must have no inherited or user-authored metadata.
A mismatched, unsafe, linked, nested, sidecar-bearing, metadata-bearing, or
partial pack fails closed.

`clone-authorized-voice` requires the Template's voice-rights confirmation,
the Request's scope-bound local authorization assertion (subject, purpose,
adapter, output, expiry), and a capability declaration. The Plan canonicalizes
only that local assertion's SHA-256 and both reviews bind it. This is an
authorization record, not independent proof of identity, consent, provider
behavior, generated voice, or lip sync.

The authorization `expires_at` must still be valid whenever prepare, propose,
or freeze is run. Historical `verify-temporal-delivery` rechecks the frozen
packet binding without requiring that old authorization to remain unexpired;
verification is not a renewal, approval, or permission to reuse the voice.

## Local CLI and review artifacts

Resolve `<skill-root>` to the installed `reference-video-rebuilder` Skill
directory. The paths below are literal CLI contracts, not commands requiring a
particular current working directory.

```text
python <skill-root>/scripts/video_remix.py validate-temporal-request <request.json> --json
python <skill-root>/scripts/video_remix.py prepare-temporal-replacement <template.json> <assets.json> <request.json> --project-root <project-dir> --reference-pack <direct-child> --temporal-rights-confirmed [--output-dir temporal-plan] [--ffmpeg <path>] [--ffprobe <path>] [--timeout-seconds <seconds>] --json
python <skill-root>/scripts/video_remix.py validate-temporal-plan <plan.json> --json
python <skill-root>/scripts/video_remix.py validate-temporal-plan-review <plan-review.json> --json

python <skill-root>/scripts/video_remix.py propose-temporal-results <plan.json> <plan-review.json> --project-root <project-dir> --result-pack <direct-child> --temporal-results-rights-confirmed [--output-dir temporal-results-proposal] [--ffmpeg <path>] [--ffprobe <path>] [--timeout-seconds <seconds>] --json
python <skill-root>/scripts/video_remix.py validate-temporal-results-proposal <proposal.json> --json
python <skill-root>/scripts/video_remix.py validate-temporal-results-review <results-review.json> --json

python <skill-root>/scripts/video_remix.py freeze-temporal-delivery <plan.json> <plan-review.json> <proposal.json> <results-review.json> --project-root <project-dir> [--output-dir temporal-delivery] [--ffmpeg <path>] [--ffprobe <path>] [--timeout-seconds <seconds>] --json
python <skill-root>/scripts/video_remix.py verify-temporal-delivery <delivery-report.json> --project-root <project-dir> [--ffmpeg <path>] [--ffprobe <path>] [--timeout-seconds <seconds>] --json
```

`prepare-temporal-replacement` publishes exactly:

- `temporal-replacement-plan.json`;
- `temporal-replacement-plan-review.template.json`; and
- `temporal-input-contact-sheet.png`.

After an explicit approved Plan Review, the user may independently operate an
allowed local tool and place a new local result pack. `propose-temporal-results`
publishes exactly:

- `temporal-results-proposal.json`;
- `temporal-results-review.template.json`;
- `temporal-results-contact-sheet.png`; and
- `temporal-technical-sanity.json`.

After an explicit approved Results Review, `freeze-temporal-delivery` publishes
exactly `temporal-replacement.mp4` and `temporal-delivery-report.json` in a new
delivery directory. `verify-temporal-delivery` is read-only and rechecks the
bound packets, input/result bytes, strict media facts, technical evidence, and
full decode.

## Technical checks versus human acceptance

The proposal's contact sheet, frame-difference/freeze metrics, stream facts,
audio payload comparison, hashes, and full local decode are technical negative
checks. They can reject black frames, unsafe media, extreme freezes, drift, a
wrong profile, or a changed file. They cannot automatically establish subject
action, face/hand/limb fidelity, garment/product fidelity, timing intent,
voice likeness, sound reconstruction, lip synchronization, rights, absence of
watermarks, or provider provenance. `semantic_action_not_proven: true` remains
in the technical-sanity record, together with the fixed limitation:
`Technical temporal metrics are negative checks only and do not prove semantic
action or motion reproduction.`

Before approving a Plan, review the bounded input set, action reference,
execution/privacy declaration, rights, required motion/audio/lip behavior, and
the input contact sheet. Before approving Results, play the entire result and
explicitly review continuous action; face, hands, limbs, hair, clothing and
product continuity; temporal artifacts; timing; audio treatment; rights; and
watermark absence. When applicable, separately confirm the scoped voice
authorization/voice likeness and visible lip-to-audio synchronization. Reject
uncertain work; technical success never converts uncertainty into approval.

## Freeze, retries, and later NLE delivery

Freeze copies the reviewed result bytes; it does not prove those bytes are
faithful to the action reference or that any provider performed a claimed
operation. Every report therefore has
`completion: temporal_replacement_reviewed`, `bitstream_faithful: false`, and
`provider_provenance: unattested-local-file-drop`. Do not call this a faithful
archive, original bitstream, source preservation, or provider-certified output.

Never mutate an approved plan, proposal, review, result pack, or delivery in
place. A rejection or retry uses a fresh result pack and a new proposal/review
cycle. Ordinary failure publishes no final target. Conservative cleanup may
leave a suspicious private `.rrv-temporal-*` staging directory; it is ignored,
not a delivery, and must be inspected as a project-root child before manual
removal.

A frozen temporal MP4 may later enter the separate `jianying-export` then
`jianying-verify` path under its own rights gate and
`jianying-compatible-v1` contract. That derivative remains a re-encoded flat
file, not a faithful archive or editable Jianying project.
