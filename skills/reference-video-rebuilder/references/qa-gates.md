# QA gates

## 0.10.1-alpha no-API-key Higgsfield web handoff

v0.10.1 is a separate, user-operated browser bridge above an already approved
v0.10 Temporal Plan. The local CLI never opens or controls a browser, uploads,
submits, polls, downloads, retries, uses cookies, or calls a provider API. Its
packets remain local attestations with
`provider_provenance: unattested-user-operated-web` and
`browser_submission_attested: false`.

P0 preparation requires all of the following:

- an approved, unchanged v0.10 Temporal Plan and Plan Review whose requirements
  are supported by this bridge: `pose-transfer|video-to-video`,
  `mute|preserve-reference`, no lip sync, and no voice clone;
- the fixed `higgsfield-web` / `motion-control` /
  `kling-3.0-motion-control` / `720p` declaration;
- two unexpired, exact-byte, single-purpose upload authorizations for only the
  selected character image and action-reference MP4; and
- the explicit `--web-handoff-rights-confirmed` gate before any private Request
  or media is read.

Preparation must publish exactly one guarded Handoff Plan and an `upload/`
directory containing only metadata-free `character.png` and silent,
metadata-clean `motion-reference.mp4`. The character and motion bytes, output
ID, provider, purpose, authorizations, and complete private Request hash must
all remain bound. A separate guessable prompt digest is forbidden. Under
`preserve-reference`, the approved audio stays local and is added only during
normalization.

Immediately before upload, prompt entry, or a billable Generate click, P0
requires a fresh live-page check of origin/surface, model, resolution, exact two
files, reviewed prompt, displayed cost, and current balance. The displayed cost
must be no greater than both the Request cap and available balance, and the user
must explicitly confirm that exact upload and one billable action at that exact
cost. A prior confirmation at a lower price is invalid. Unknown, failed, or
timed-out submissions are never retried automatically.

`record-higgsfield-web-action` writes only a pre-submit local receipt.
`projected_remaining_credits_after` is arithmetic, not an observed post-charge
balance, invoice, provider receipt, or proof that a submission occurred. The
receipt must not contain a prompt, URL, job ID, cookie, account identifier,
credential, screenshot, or provider response.

P0 single-use state is terminal and private: the exact Handoff Request hash may
claim only one action receipt across copied/reprepared Plans, and the exact
receipt hash may claim only one normalization across sequential or concurrent
commands. A crash or any later failure leaves the corresponding ignored
`.rrv-higgsfield-web-*-use-*` marker in place. Deleting that state to retry is
outside the contract; use a fresh Request, Plan, live confirmation, receipt,
and result pack.

Normalization accepts exactly one newly downloaded ordinary video, snapshots
and fully decodes it, strips inherited metadata, normalizes it to the approved
v0.10 profile, and publishes a one-file result pack named
`temporal-replacement.mp4`. For `preserve-reference`, its audio must come from
the approved local action reference rather than the uploaded or downloaded
browser media. Normalization is not final acceptance: the result must still
pass `propose-temporal-results`, full-playback Results Review,
`freeze-temporal-delivery`, and `verify-temporal-delivery`. No local hash or
receipt proves provider provenance, semantic action fidelity, rights, voice
likeness, or lip sync.

## 0.10.0-alpha temporal file-drop review and freeze

v0.10 is a separate provider-neutral temporal replacement chain. It neither
changes Template IR 0.3, Asset Manifest 0.2, the static renderer, faithful
source preservation, nor the still-image generation bridge. It never calls a
provider, uses a browser/CUDA job, reads an API key, uploads media, or generates
video. This Alpha permits only `privacy_profile: local-only`,
`execution_profile: local-file-drop`, and `cloud_upload_confirmed: false`: a
user independently operates any local tool, then drops bytes locally. It only
reviews those bytes and, after approval, byte-copies one result into a frozen
delivery.

P0 eligibility requires all of the following before planning:

- a reviewed Template IR `0.3.0` with `support.review_required: false`,
  `motion_required: true`, and `motion_mode: pose-transfer` or
  `video-to-video`;
- its selected output, a frozen local-only Asset Manifest `0.2.0`, and a
  Temporal Request whose `input_slot_ids` resolve only to selected
  rights-confirmed frozen Manifest bytes; the Plan records the opaque
  `input_assets` list plus its canonical hash, and the Request declares only
  `local-only` + `local-file-drop` + `cloud_upload_confirmed: false`;
- one direct-child action-reference pack containing exactly one safe MP4 and no
  sidecars; and
- the explicit temporal rights flag before the CLI reads the private Request or
  reference pack.

P0 technical profile applies to both the action reference and result: MP4,
exact CFR, zero rotation, H.264 High/8-bit `yuv420p`, no unsupported side
streams, at most 60 seconds, full local decode, and the bound frame count/FPS/
dimensions. When audio exists, permit exactly one AAC-LC 48 kHz stereo stream;
otherwise require no audio stream. The result pack is a distinct guarded
direct-child directory containing exactly one file named
`temporal-replacement.mp4`. That candidate must contain no inherited or
user-authored metadata.

P0 Plan Review must bind the exact Plan and explicitly confirm the limited
input set, action-reference contact sheet, execution/privacy declaration,
full-playback intent, motion/action, face/hands/limbs, garment/product,
timing, audio, rights, and watermark review. The reviewer rejects every
controller/cloud/API declaration: v0.10 permits only the three local values
above, and no declaration authorizes this CLI to upload anything. An adapter/
tool/version declaration remains local and does not prove any claimed work.

P0 Results Review must bind the exact proposal and confirm, after full playback,
continuous action; face, hands, limbs, hair, clothing and product continuity;
timing; audio treatment; rights; and watermark absence. For a requested voice
clone, require the scope-bound local authorization assertion's canonical hash in
the Plan/Plan Review plus explicit voice-likeness review. For requested lip
sync, require explicit visible mouth-to-audio review. Neither condition is
independently proven by the local authorization or any technical metric. The
voice assertion must be unexpired for prepare, propose, and freeze; later
historical verification rechecks the frozen binding without renewing it.

The result contact sheet, technical-sanity record, hashes, frame-difference/
freeze metrics, stream presence, audio payload matching, and complete decode
are negative technical checks only. They can reject bad media, drift, black
frames, extreme freezes, or a changed file. They cannot prove semantic action,
sound recreation, voice likeness, lip sync, rights, garment fidelity, or
provider provenance; `semantic_action_not_proven: true` is mandatory.

`freeze-temporal-delivery` publishes no partial final target. On success it
byte-copies the reviewed result and writes a report with
`completion: temporal_replacement_reviewed`, `bitstream_faithful: false`, and
`provider_provenance: unattested-local-file-drop`. This is not faithful source
preservation or provider attestation. Rejections/retries use a new result pack
and proposal/review; ignored `.rrv-temporal-*` staging is never a delivery.
`verify-temporal-delivery` independently rechecks the exact packet bindings,
bytes, technical evidence, profile, and decode. A frozen MP4 may later use the
separate `jianying-export`/`jianying-verify` contract.

## 0.9.1-alpha faithful evidence, provenance, and NLE derivative

The faithful plan remains schema `0.9.0`; v0.9.1 adds evidence/report schema
`0.9.1`, stronger run provenance, and a separate NLE derivative. Do not merge
their claims.

P0 evidence requires the source and canonical plan hashes to match, every
declared text item to remain `human_reviewed: true`, deterministic sample
frames to be recorded, contact-sheet bytes to match their SHA-256, and the
report to state `ocr_used: false` and `claim: human_review_support_only`.
Evidence cannot certify that the human reviewer omitted no visible text; full
playback and contact-sheet review remain mandatory.

P0 faithful provenance requires matching raw and canonical plan hashes,
executor hash, invocation-policy hash, workflow version, and bounded local
runtime provenance. Executor or source drift before publication fails closed.

P0 NLE delivery requires explicit rights confirmation, the exact
`jianying-compatible-v1` profile, a new atomic output, full video/audio decode,
and a passing independent `jianying-verify`. Its report must state
`completion: nle_compatible_derivative` and `bitstream_faithful: false`.
Reject any claim that it is an original bitstream, Jianying project, editable
timeline/layer package, official certification, or guaranteed import across
all versions. Read [nle-delivery-contract.md](nle-delivery-contract.md).

## 0.9.0-alpha faithful source-preservation

Faithful source preservation is a separate, exact-preservation route for an
authorized source; it is not a Template IR render, clean-room reconstruction,
or replacement workflow. Read
[faithful-rebuild-contract.md](faithful-rebuild-contract.md) before accepting
it.

P0 requires a hash-bound `0.9.0` plan with `rights_confirmed: true`,
`operation: faithful-reference-rebuild`, `visible_text_policy: preserve-exact`,
a human-created and reviewed inventory of every visible text item,
`video_mode: preserve-bitstream`, `audio_mode: preserve-bitstream` or `mute`,
and `metadata.strip_all: true`. The inventory is review evidence, not OCR; the
validator cannot infer missing text or certify that a reviewer missed none.

Accept only after the source fingerprint, source-video packet payload, selected
audio treatment, inherited/user-authored metadata stripping, and full playback
all match the reviewed plan. Unavoidable MP4 muxer structural tags may remain.
The result may preserve source audio exactly or be intentionally muted; it must
not replace, clone, synthesize, translate, remove, or otherwise alter visible
content. Do not use it to remove UI, captions, watermarks, logos, comments,
people, products, backgrounds, or text. Its only success claim is
`faithful_source_preservation`, never full reconstruction.

## 0.8.0-alpha motion/audio contract hardening

v0.8 defines an acceptance contract for Template IR 0.3.0; it does not make
the existing deterministic renderer a motion, voice, or lip-sync engine. The
current local path composites static images, 2D layout/transforms, transitions,
and selected audio. It cannot claim continuous subject action from a still
image, voice imitation from retained audio, or mouth/audio synchronization.

Read [motion-audio-contract.md](motion-audio-contract.md) before accepting a
Template IR 0.3.0 or any result that claims performance, voice, SFX rebuilding,
or lip sync. The older Template IR 0.2.0 has no `rebuild_requirements`; every
legacy render must be labelled `structure_only_unclaimed` and cannot pass a
motion, voice, or lip-sync acceptance gate.

## 0.7.2-alpha coverage

The bundled local CLI retains the v0.4 reference Proposal/Review/freeze-plan
path, v0.5 strict asset-pack proposal/freeze, and adds a v0.6 external-
generation bridge: validate-generation-request, prepare-generation,
plan review, propose-generation-results, result review, and
assemble-generation-pack. The bridge only prepares/reviews local file handoff
artifacts. It does not invoke a generation model, local CUDA job, shell,
network request, provider SDK, or weight download. A successful proposal is
never approval. Proposal/review validation and freeze/assembly failures return
exit code 2. Compile retains exit code 1 when its existing timing evidence
requires review, and render retains its technical verifier behavior.

The new planning workflow does not automate semantic acceptance. Generation
plan and result reviewers must explicitly decide identity consistency,
body/pose, garment/product/background fidelity, logos/text, hands/artifacts,
rights, and any controller-cloud consent. Gates 3, 4, and 6 still require an
agent or human review before claiming those properties, timing intent, or
complete removal of platform elements.

`video_remix.py` remains the fully offline v0.6 path. v0.7 adds the
separate explicit `openai_image_controller.py` API controller, which can make
approved OpenAI API image requests after preflight and three fresh run-time
confirmations. It is not part of `video_remix`, does not turn a v0.6 cloud
declaration into generic upload authority, and does not replace either human
review.

v0.7.1 also permits a distinct, manually orchestrated Codex built-in ImageGen
handoff after an approved cloud plan. It needs no API key, is not a CLI
subcommand, and does not weaken the upload, result-review, asset-freeze, or
visual-acceptance gates.

v0.7.2 additionally permits deterministic output encoding to the exact
`1280x720` and `1920x1080` profiles, alongside `720x1280` and `1080x1920`.
This is renderer-only support for a manual/reviewed Template IR; it does not
widen the automated new-reference proposal/compiler path beyond portrait,
fixed-subject-carousel S1 work.

## P0 — v0.7.2 fixed landscape delivery

- Accept only the exact frozen output profiles `720x1280`, `1080x1920`,
  `1280x720`, and `1920x1080`; arbitrary portrait or landscape dimensions must
  fail before encoder output is written.
- Treat `1280x720` and `1920x1080` as delivery/reframe targets of a manually
  authored and visually reviewed Template IR. They do not authorize
  `propose`, support-level classification, or `compile` to analyze or generate
  a landscape reference plan.
- Keep the ordinary reviewed asset-freeze path, source-pixel exclusion,
  technical media verification, contact-sheet inspection, and human
  full-playback review. The clean-room portal-reveal benchmark is evidence for
  this bounded manual path, not a claim of arbitrary-video or S2 automation.
- Confirm the intended 16:9 composition, profile dimensions, exact decoded
  frame count, audio treatment, and absence of prohibited overlays before
  acceptance. A successful encode alone is not visual or rights approval.

## P0 — v0.8 Template IR motion/audio requirements

All applicable P0 checks must pass before a delivery can claim more than
`structure_only_unclaimed`:

- a Template IR 0.3.0 contains one complete `rebuild_requirements` object with
  `motion_required`, `motion_mode`, `audio_mode`, `lip_sync_required`, and
  `voice_likeness_rights_confirmed`;
- `motion_mode` is exactly one of `static`, `layout-only`, `pose-transfer`, or
  `video-to-video`; `audio_mode` is exactly one of `mute`,
  `preserve-reference`, `replace-upload`, `rebuild-sfx`, or
  `clone-authorized-voice`;
- `motion_required: false` permits only `static` or `layout-only`, while
  `motion_required: true` requires `pose-transfer` or `video-to-video`;
- `lip_sync_required: true` requires `motion_required: true` and an audio mode
  other than `mute`;
- `audio_mode: clone-authorized-voice` requires
  `voice_likeness_rights_confirmed: true`; a true value under another audio
  mode does not authorize voice cloning;
- an unknown, absent, contradictory, or unsupported requirement fails closed;
  no reviewer, renderer, or controller may silently reduce it to static
  composition;
- a pan, zoom, transform, cross-fade, or carousel movement of a static image
  proves only `layout-only`. It cannot satisfy `motion_required: true`,
  `pose-transfer`, or `video-to-video`;
- `audio_mode: preserve-reference` proves only approved reference-audio
  preservation. It is not voice imitation, voice cloning, SFX rebuilding, or
  lip sync;
- a non-static result has a reviewed controller declaration naming the actual
  execution mechanism/version, authorized upload scope, and full-playback
  evidence for the requested motion/audio behavior; and
- an unintegrated external provider, including a possible future Runway route,
  is not evidence of capability. No motion controller is integrated by this
  repository today; v0.10's local file-drop review/freeze route is not an
  integration or provider attestation.

The current portal-reveal request is fixed as:

```json
{
  "motion_required": true,
  "motion_mode": "video-to-video",
  "audio_mode": "preserve-reference",
  "lip_sync_required": false,
  "voice_likeness_rights_confirmed": false
}
```

The existing static portal render can pass only structure/timing/effect and
audio-preservation review as `structure_only_unclaimed`; it must fail this P0
motion gate until a reviewed video-to-video result is available.

## P0 — v0.7.1 Codex built-in ImageGen handoff

- Request and approved Plan Review declare only `controller-cloud` +
  `controller-managed`, `adapter_id: codex-builtin-imagegen`,
  `adapter_version: 2026-08-24`, a bounded controller label, and
  `cloud_upload_confirmed: true`.
- Each built-in generation call receives only the approved reference images for
  one accepted generated task. Never send video, audio, packets, unrelated pack
  entries, rejected candidates, or credentials.
- No `OPENAI_API_KEY` is requested, read, logged, or stored. Do not report API
  billing/account/project facts for this route; built-in generation consumes
  the active Codex product's usage limits.
- Output is copied to a new result pack under the exact target-slot filename.
  No automatic retry, silent overwrite, or self-approval is allowed.
- The current primary model inspects every result for identity, pose, garment,
  product, background, hands, anatomy, text/logo, and prohibited overlays before
  approving the bound result review.
- The unchanged generation result proposal/review, assembly, v0.5 asset
  proposal/review/freeze, render, technical QA, and full-playback review all
  remain mandatory.

## P0 — v0.7 OpenAI GPT Image 2 controller

All applicable P0 checks must pass before an OpenAI request and again before
treating its output as a v0.6 result pack:

- the Plan and approved Plan Review are exact hash-bound artifacts from the
  v0.6 workflow and declare only `controller-cloud` + `controller-managed`,
  `adapter_id: openai-gpt-image-2`, `adapter_version: 2026-04-21`, and the
  required request/review `cloud_upload_confirmed: true` facts;
- preflight runs with the generation-rights confirmation before any network
  action or output creation. It is read-only: no HTTP/API call, key validation,
  result pack, staging directory, contact sheet, log artifact, or other write;
- run repeats the rights confirmation and additionally requires explicit cloud
  upload and billable-request confirmations. `max-billable-requests` is an
  integer from 1 through 32 and covers every approved generation task; no
  implicit default, over-cap request, fallback provider, or automatic retry is
  allowed;
- only reference **images** explicitly attached to accepted, reviewed tasks may
  be uploaded. Never upload a video, audio, plan/review JSON, prompt sidecar,
  unapproved task/reference, result candidate, arbitrary pack file, or secret;
- API authentication reads only `OPENAI_API_KEY` at run time. The value is never
  accepted as a CLI/config/request field and must not appear in command output,
  logs, packets, artifacts, result files, Git, or test fixtures. Do not infer a
  relationship to a Codex in-app image credential or billing account;
- every provider request is fixed to `gpt-image-2-2026-04-21`, `high`,
  `1024x1536`, `png`, `opaque`, and `auto` moderation. Omit
  `input_fidelity`; do not emit partial images or caller-selected request
  settings;
- success atomically publishes a new direct-child result pack containing only
  metadata-free PNG files named by accepted target slot. Any API, response,
  decode, normalization, metadata, path, or publication failure leaves no
  result pack or partial target;
- the result still enters `propose-generation-results`, explicit result review,
  `assemble-generation-pack`, and v0.5 asset review/freeze. Provider support
  for multi-reference/high-fidelity image input never auto-accepts person
  consistency, garment/product/logo/text fidelity, hands, background, or exact
  composition;
- record only bounded operational facts such as task and output hashes and
  billed request count. Treat the current $0.165 high/1024x1536 output baseline
  as an estimate plus input costs, not a locked price; check official pricing
  before each approved spend.

## P0 — v0.6 generation bridge

All applicable P0 checks must pass before treating a generated result as a
candidate for the v0.5 asset-pack workflow:

- prepare-generation requires `--generation-rights-confirmed` before guarded
  Generation Request/reference-pack analysis; propose-generation-results
  requires `--generation-results-rights-confirmed` before guarded result-pack
  analysis;
- template/packet references follow their normalized project-root-relative
  contract; reference-pack and result-pack are guarded direct-child inputs and
  each output directory is a new direct child of project-root. None may be
  absolute, nested, dot-segment, escaping, linked, or reparse-point paths;
- the Generation Plan records only `local-file-drop` or `controller-managed`;
  `local-command`, arbitrary shell, local model invocation, CUDA discovery,
  weight download, browser/provider SDK, and CLI networking are forbidden;
- `controller-cloud` requires `cloud_upload_confirmed: true` in both the
  request and approved Plan Review. That declaration never gives the CLI upload
  authority and does not replace review of controller rights, terms, retention,
  or upload scope;
- `video_remix.py` public CLI summaries must not echo `adapter_id`,
  `adapter_version`, `controller_label`, prompt content, credentials, URLs, or
  private paths;
- every plan review binds the exact plan/request/template/reference evidence
  required by the schema and explicitly confirms source mappings, execution
  declaration, rights, and any cloud consent;
- every result proposal/review binds the approved plan/review and the exact
  result evidence required by the schema. Technical media checks never turn
  identity, garment/product/logo/background fidelity, pose, hands, or artifacts
  into automatic acceptance;
- rejected work is retried with a new result pack and new proposal/review; do
  not mutate an approved plan, approved result, or assembled output in place;
- assembly accepts only approved/bound packets, emits no partial target, and
  produces a media-only exact-slot pack: static images receive EXIF orientation
  and metadata-free PNG re-encoding, while approved audio passthrough from the
  reference pack is passed through. Result packs contain only required static
  target-slot images, never generated audio;
- the assembled pack contains no prompt, JSON, sidecar, report, credential,
  source reference, video, animation, unknown file, link, reparse point, or
  nested directory. It is not an Asset Manifest and cannot render directly;
- v0.5 `propose-assets -> asset review -> freeze-assets` remains mandatory
  after assembly. Its independent scan, review, snapshot, and renderer byte
  binding cannot be skipped.

The Plan/Review packets are local hash-bound audit records, not signatures or
proof that an external controller honored its declaration. A process that can
write the project can author records; use trusted signing or immutable storage
outside this alpha if independent proof is required.

## P0 — strict local asset-pack freeze

All P0 checks must pass before render:

- propose-assets requires asset-pack-rights-confirmed before reading the
  project or asset pack;
- Template input, Proposal, and Review are normalized project-root-relative
  paths, while asset-pack and output-dir are direct project-root children;
- the pack contains only direct regular static JPEG, PNG, or WebP images and
  WAV, MP3, M4A, or MKA audio verified by local ffprobe through pipe:0;
- unknown files, videos, animations, sidecars, directories, links, reparse
  points, and unsafe paths fail the entire pack; no OCR, visual, or fuzzy
  candidate inference is permitted;
- every candidate comes only from exact filename stem equal to slot_id and
  accepted_media compatibility;
- asset-pack-proposal.json, asset-review-decision.template.json, and
  asset-contact-sheet.png are local review evidence. They are not a GUI and do
  not approve a mapping;
- every approved use explicitly confirms content, media compatibility, render
  readiness, and rights; required, unresolved, ambiguous, incompatible, or
  unconfirmed mappings block freeze;
- freeze-assets binds Proposal, Review, Template, and inventory hashes,
  rescans safely, rejects drift, and atomically publishes local-only Asset
  Manifest 0.2.0 with SHA-256 for every opaque flat copy plus its freeze report;
- renderer 0.2.0 decodes frozen image bytes from verified snapshots and sends
  frozen audio to FFmpeg only through pipe:0. Legacy Asset Manifest 0.1.0
  remains compatibility behavior, not a P0 substitute;
- Windows is the audited reparse/snapshot boundary for asset-pack scan,
  rescan, and frozen-assets publication. Other platforms are observable
  fail-closed where supported, without an equivalent NT no-delete guarantee.
- render binds consumed 0.2 asset bytes to declared hashes, but frame/output
  path containment assumes no hostile concurrent filesystem mutation.

validate-assets is declaration preflight: it validates the declared manifest
contract, containment, and applicable hashes, but does not sniff media bytes
or provide renderer-equivalent link/reparse guarantees. The strict scanner
and freeze rescan provide media inspection; the renderer is authoritative for
runtime snapshot binding. No cloud upload, generation, GUI, or arbitrary-video
path is part of P0.

The Proposal, Review, manifest, and freeze report are local hash-bound audit
records, not signatures. They prevent accidental drift in the governed
workflow but cannot prove approval against a writer who controls the project.

## Gate 0 — bounded Proposal, Review, and freeze

- Proposal schema version is exactly `0.4.0`, its nested candidate plan is
  valid, and `review_required: true` is mandatory.
- Source is exact CFR, has zero rotation, is no more than 60 seconds long, and
  uses the required local FFmpeg/ffprobe tools.
- Proposal emits an overview contact sheet, geometry preview, timing profile,
  strict Proposal JSON, and pending Review template with bounded evidence.
- Proposal may contain only the safe technical source fingerprint: SHA-256,
  width, height, exact frame_count, fps, and has_audio. It must not contain a
  source filename/path, tool path, container tags, title, artist, comments,
  account identity, raw probe, raw media, or private evidence payload.
- The proposed source_rect is a maximal centered crop for the supported 9:16
  aspect, using the full source only when it already matches. It is a
  composition heuristic, not semantic platform-UI detection/removal; chrome,
  non-centered content, nonuniform crop, semantics, and ambiguous timing
  require reviewer correction.
- Review binds the exact Proposal SHA-256 and explicitly confirms family,
  geometry, slot_count, timing, carousel, background, audio, and authorization.
  The reviewer may correct approved_plan.
- freeze-plan validates the hash binding, all confirmations, approved_plan,
  local path rules, and authorization before writing a canonical Compiler Plan
  schema version `0.3.0`.
- freeze-plan Proposal and Review inputs are normalized paths relative to
  project-root. Absolute local, drive-rooted, and UNC packet paths fail before
  candidate packet inspection; this freeze-only rule does not restrict the
  standalone validate-proposal or validate-review commands.
- Proposal/review/freeze outputs are project-contained and atomic. Any
  validation or freeze failure returns exit code 2 and writes no partial frozen
  plan.
- propose and freeze-plan output directories are new direct children of
  project-root. Absolute, nested, dot-segment, and existing targets fail before
  media processing or writes.

## Gate 1 — template structure

- the authorized local Compiler Plan is a canonical schema `0.3.0` frozen
  result with reviewed geometry and `slot_count`;
- any `compile` result with `review_required: true` has been resolved, and the
  frozen Template IR has `support.review_required: false` before rendering;
- media geometry and duration are valid;
- all IDs are unique;
- events fall within the frame range;
- event slot references exist;
- output dimensions are positive and even;
- support level and warnings are present;
- Template IR 0.3.0 includes valid `rebuild_requirements`; Template IR 0.2.0
  is routed only to the `structure_only_unclaimed` acceptance scope; and
- prohibited layers are excluded.

## Gate 2 — asset readiness

- every required slot is mapped exactly as intended;
- input hashes and rights are recorded;
- file types, resolution, transparency, and duration are valid;
- the v0.5 asset freeze/render path remains local-only. v0.6 may record a
  reviewed `controller-cloud` declaration for an external controller, but the
  CLI itself still has no cloud adapter, upload, or provider runtime route;
- no source or output path escapes the project allowlist.
- the governed v0.5 path uses the Asset Manifest 0.2.0 published by
  freeze-assets and retains its Proposal, Review, and report; Asset Manifest
  0.1.0 is legacy compatibility and does not replace that path.

## Gate 3 — look approval

- model identity is consistent;
- body proportions and pose are accepted;
- garment color, silhouette, neckline, sleeves, length, print, and logo meet the configured target;
- hands, limbs, face, hair, and edges have no material artifact;
- products use the correct source and are not swapped.

## Gate 4 — preview

- cuts, slot changes, transitions, motion curves, and audio cues match the Template IR;
- subject anchors and background remain stable;
- no unexpected flash, freeze, black frame, or missing asset appears;
- when `motion_required: true`, verify continuous subject action rather than
  only 2D layer movement; when `lip_sync_required: true`, verify visible
  mouth/audio synchronization; and
- distinguish `preserve-reference`, `replace-upload`, `rebuild-sfx`, and
  `clone-authorized-voice` in the recorded audio review; and
- all warnings are visible to the reviewer.

## Gate 5 — final media

- output opens and fully decodes;
- expected frame rate, exact decoded frame count, dimensions, audio-stream presence, and container/video duration agreement pass;
- all requested output profiles derive from the same master timeline.

An audio stream proves neither voice imitation nor lip sync. Frame decoding and
static-frame similarity prove neither pose transfer nor video-to-video motion.

The alpha verifier does not yet establish pixel format, codec policy,
faststart/mobile behavior, subjective audio sync, or visual quality. Add an
explicit check before relying on any of those conditions.

## Gate 6 — prohibited overlay removal

Use known UI-region checks, contact sheets, the geometry/timing proposal
artifacts, and a human full-playback review. The bundled `0.7.2-alpha` Skill
does not include OCR or automatic platform-UI semantic detection. Require no
residual platform logo, account text, comments, engagement rail,
status/navigation bars, or visible reconstruction smear. This remains a
required review gate rather than an automated pass claim.

## Result model

Each check returns:

- `status`: `pass`, `warn`, or `fail`;
- `metric` and threshold when applicable;
- frame or time range;
- affected slot or track;
- evidence path;
- suggested retry or correction.

Block packaging on any `fail`. Require recorded human acceptance for material `warn` results.
