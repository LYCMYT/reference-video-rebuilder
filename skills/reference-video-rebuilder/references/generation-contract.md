# v0.6 offline generation bridge and v0.7 OpenAI controller contract

## Contents

1. [Purpose and boundary](#purpose-and-boundary)
2. [State flow](#state-flow)
3. [Inputs and path rules](#inputs-and-path-rules)
4. [Commands and artifacts](#commands-and-artifacts)
5. [Executor declaration and consent](#executor-declaration-and-consent)
6. [Human review requirements](#human-review-requirements)
7. [Result packs, retries, and assembly](#result-packs-retries-and-assembly)
8. [Privacy, rights, and provenance](#privacy-rights-and-provenance)
9. [Non-guarantees](#non-guarantees)
10. [v0.7 OpenAI GPT Image 2 controller](#v07-openai-gpt-image-2-controller)

## Purpose and boundary

Use this contract to turn externally created still assets into a reviewable
input for the existing v0.5 asset freeze. v0.6 coordinates a plan, a local
file-drop result, explicit review, and a clean media-only handoff. The separate
v0.7 OpenAI controller can create the same result pack only under the additional
rules at the end of this document.

`video_remix.py` must never invoke a model, arbitrary shell command, local CUDA
runtime, remote worker, HTTP API, controller SDK, browser, or weight downloader.
It does not upload any file. The standalone v0.7 `openai_image_controller.py`
is the narrow, explicit exception: it performs only the approved OpenAI API
image requests described below. A different controller may perform work outside
`video_remix.py` after plan approval; its controls and terms remain separate
responsibilities.

This bridge supports static render-ready image replacement and accepted audio
only. It does not make semantic claims from filenames, pixels, prompts, or
metadata. It does not replace the v0.5 `propose-assets -> asset review ->
freeze-assets` mapping/snapshot gate.

## State flow

```text
validated Template IR + Generation Request + reference pack
  -> prepare-generation
  -> Generation Plan + pending plan review + input contact sheet
  -> explicit plan approval
  -> external controller or local file drop
  -> new result pack
  -> propose-generation-results
  -> Result Proposal + pending result review + result contact sheet
  -> explicit result approval
  -> assemble-generation-pack
  -> clean exact-slot media pack
  -> v0.5 propose-assets -> asset review -> freeze-assets -> render
```

The plan is the immutable declaration of requested work. A result proposal is
evidence for one separately supplied result pack. Do not alter the plan after
it has been approved. Do not overwrite an approved generated file: put a retry
in a new result pack and make a new result proposal/review bound to that pack.

## Inputs and path rules

Use the JSON Schemas shipped in `assets/schemas/` as the executable source of
field names, value bounds, and required confirmations. This reference explains
the workflow and must not be used to hand-wave a failing schema validation.

`prepare-generation` accepts:

- a validated Template IR;
- a Generation Request that explicitly maps user-provided references to
  requested output slots;
- one direct-child `reference-pack` containing the requested local references;
- an explicit rights flag before any generation-request or pack analysis.

`propose-generation-results` accepts the approved plan and its approved review,
then one new direct-child `result-pack`. `assemble-generation-pack` accepts the
four project-root-relative packets (plan, plan review, result proposal, result
review) and emits one new direct-child output pack.

Treat template and packet arguments as normalized project-root-relative paths
when the command contract requires them. Treat `reference-pack`, `result-pack`,
and `output-dir` as direct child names, never arbitrary paths. Reject absolute,
UNC, rooted, nested, dot-segment, link/reparse-point, and escaping paths.
Never place source/video paths, credentials, private prompts, or tool paths in
a packet intended for review or public CLI JSON.

## Commands and artifacts

Use the validators before and after each human decision:

```text
video-remix validate-generation-request <request.json> --json
video-remix prepare-generation <template> <request> --project-root <root> --reference-pack <direct-child> --output-dir <direct-child> --generation-rights-confirmed --ffprobe <ffprobe> --timeout <seconds> --json
video-remix validate-generation-plan <generation-plan.json> --json
video-remix validate-generation-plan-review <generation-plan-review.json> --json

video-remix propose-generation-results <plan> <plan-review> --project-root <root> --result-pack <direct-child> --output-dir <direct-child> --generation-results-rights-confirmed --ffprobe <ffprobe> --timeout <seconds> --json
video-remix validate-generation-results-proposal <generation-results-proposal.json> --json
video-remix validate-generation-results-review <generation-results-review.json> --json

video-remix assemble-generation-pack <plan> <plan-review> <results-proposal> <results-review> --project-root <root> --output-dir <direct-child> --ffprobe <ffprobe> --timeout <seconds> --json
```

`prepare-generation` writes exactly these review artifacts under its output
directory:

- `generation-plan.json`;
- `generation-plan-review.template.json`;
- `generation-input-contact-sheet.png`.

`propose-generation-results` writes:

- `generation-results-proposal.json`;
- `generation-results-review.template.json`;
- `generation-results-contact-sheet.png`.

`assemble-generation-pack` writes only accepted media files. It deliberately
writes no JSON, prompts, reports, logs, credentials, or provenance sidecar into
the assembled pack because the v0.5 strict pack scanner accepts media only.

## Executor declaration and consent

Declare one of these values in the Generation Request's `execution_profile`
and retain it in the reviewed plan:

| Execution mode | Meaning | CLI behavior |
| --- | --- | --- |
| `local-file-drop` | A user or locally operated tool creates result files and drops them into a new result pack. | No model or tool is launched by the CLI. |
| `controller-managed` | A separate controller receives the reviewed plan and creates the result files. | The CLI records the declaration only; it neither launches nor authenticates the controller. |

Declare one value in `privacy_profile` as well:

| Privacy profile | Requirement |
| --- | --- |
| `local-only` | The controller must keep approved inputs and processing local. The CLI makes no network call. |
| `controller-cloud` | Valid only with `controller-managed`. The request and approved Plan Review must both set `cloud_upload_confirmed: true` before any controller upload, and the request/plan must retain the bounded `controller_label`. The CLI still makes no network call. Send only the minimum approved references; never treat a rights flag as cloud consent. |

`local-command` is intentionally not an alpha execution mode. Do not add a
command-string escape hatch, automatic GPU discovery, model installation,
checkpoint download, browser automation, or controller key handling. A local
CUDA workflow is allowed only as an external operator/controller action that
leaves files in a result pack.

Record the schema-required bounded execution declaration before plan approval:

- `execution_profile` and `privacy_profile`;
- `adapter_id` and `adapter_version`; neither may be a path, URL, or credential;
- `controller_label` for `controller-managed`;
- `cloud_upload_confirmed: false` for `local-only`, or `true` in both the
  request and approved Plan Review for `controller-cloud`;
- only the required rights/retention/reproducibility/limitation facts allowed
  by the schema, never a raw prompt or credential.

## Human review requirements

The plan review must explicitly confirm, for every requested slot:

- the intended Template slot and all permitted reference inputs;
- executor mode, privacy profile, bounded `adapter_id`/`adapter_version`, and
  `controller_label` when applicable;
- rights for likeness, garment, product, brand/logo, background, audio, and
  the intended processing;
- `cloud_upload_confirmed: true` in both the request and Plan Review when
  `controller-cloud` is selected;
- whether output may preserve, replace, or omit each allowed element.

The result review must explicitly decide every generated mapping. Confirm at
least:

- model identity, face/hair continuity, accepted body proportion, pose, and
  framing;
- garment silhouette, color, neckline, sleeves, length, print, and logo/text;
- product identity, readable markings, and non-substitution;
- background, composition, and required retain/remove boundaries;
- hands, limbs, edges, reflections, transparency, artifacts, and render
  readiness;
- rights for the delivered result bytes.

Contact sheets, hashes, media probes, and a passing validator are technical
evidence only. They cannot establish any of the semantic/visual confirmations
above. A reviewer must reject or retry a slot when fidelity is uncertain.

## Result packs, retries, and assembly

Use exact Template output-slot stems in a result pack. For every non-
passthrough/non-omitted task, supply exactly one static JPEG, PNG, or WebP
image; a result pack is not an audio drop. Audio can only be an explicitly
reviewed passthrough reference from the reference pack. Reject unknown files,
nested content, sidecars, links/reparse points, video/animation, and unsafe
media. The generation plan has no filename-guessing or OCR fallback.

For a rejected single look, create a new result pack containing the complete
expected result set (including unchanged accepted candidates as needed by the
contract), then create a new proposal and review. Do not mutate the approved
plan, result-proposal, result-review, or assembled pack in place. This keeps
each retry hash-bound and auditable.

Assembly validates the approved packet binding and emits a pack suitable for
the v0.5 scanner:

- apply EXIF orientation to accepted static images, then re-encode them as
  PNG without source metadata;
- pass an approved audio passthrough from the reference pack unchanged;
- retain exact slot-compatible media filenames/stems;
- emit no non-media files.

Assembly is normalization, not quality approval and not a security signature.
Run v0.5 `propose-assets`, complete its independent asset review, and run
`freeze-assets` before rendering.

## Privacy, rights, and provenance

Keep original user references, raw controller prompts, controller credentials,
downloaded weights, and result packs out of Git. Record only the bounded
executor declaration, privacy profile, `adapter_id`, `adapter_version`,
`controller_label` where applicable, explicit consent facts, packet/content
hashes, and reviewer decisions needed by the schema. Public CLI summaries must
not echo these private declarations. Never log secrets or private absolute
paths.

The request/plan/proposal/review files are locally asserted, hash-bound audit
records. They are not a cryptographic signature or independent proof that a
specific human, controller, or model performed an action. Use a trusted signer or
access-controlled immutable store if that proof is required.

## Non-guarantees

Do not claim that v0.6 automatically creates a faithful try-on image, preserves
identity, preserves a logo/text, identifies a garment, detects a watermark, or
removes platform elements. It cannot recover pixels hidden by original UI or
overlays. It does not make a cloud controller safe, licensed, private, or
commercially usable by recording its name. The final video remains a clean-room
reconstruction from reviewed assets and a reviewed template—not a pixel-level
copy of the reference.

## v0.7 OpenAI GPT Image 2 controller

### Separate, narrow networking surface

`scripts/openai_image_controller.py` is a standalone, explicit networking
controller. It is not a `video_remix.py` subcommand, and the v0.6
`video_remix.py` workflow remains completely offline. Do not describe this as a
Codex built-in image feature or assume that any Codex in-app identity,
subscription, credential, or billing arrangement is shared with this controller.

The controller accepts only an already approved v0.6 Generation Plan and Plan
Review whose declaration is exactly:

| Field | Required value |
| --- | --- |
| `privacy_profile` | `controller-cloud` |
| `execution_profile` | `controller-managed` |
| `adapter_id` | `openai-gpt-image-2` |
| `adapter_version` | `2026-04-21` |
| cloud consent | Request and approved Plan Review both have `cloud_upload_confirmed: true` |

The plan must remain hash-bound to its request/template/reference inventory and
the review must accept every task it authorizes. A `local-file-drop`,
`local-only`, pending, rejected, drifted, differently routed, or differently
versioned plan is not eligible. `controller_label` remains a bounded plan
declaration; it is not a credential or an authorization shortcut.

### Preflight before any network or write

Run preflight after the human has approved the plan and before configuring or
making a billable request:

```text
python scripts/openai_image_controller.py preflight <generation-plan.json> <generation-plan-review.json> --project-root <project-root> --generation-rights-confirmed [--ffprobe <ffprobe>] [--timeout-seconds 300] [--json]
```

Preflight validates only local packet bindings, approved tasks, protected paths,
the fixed declaration, and the number of images/references that would be used.
It makes no network request, does not read or validate an API key, and writes no
file or directory. Pack classification can invoke the explicitly selected local
`ffprobe`; that executable remains a trusted local dependency, not a controller
provider request. Its stable JSON summary contains `schema_version`,
`operation`, `approved`, `adapter` (`id`, `version`), and
`counts` (`generation_tasks`, `approved_references`). A successful preflight is
not a generation approval and does not create a result pack.

### Explicit billable run

Only after reviewing that preflight, run the separate controller with all three
affirmative confirmations:

```text
python scripts/openai_image_controller.py run <generation-plan.json> <generation-plan-review.json> --project-root <project-root> --output-dir <new-direct-child> --generation-rights-confirmed --cloud-upload-confirmed --billable-requests-confirmed --max-billable-requests <1..32> [--ffprobe <ffprobe>] [--timeout-seconds 300] [--json]
```

`--generation-rights-confirmed` confirms the right to process the approved
assets; `--cloud-upload-confirmed` confirms the reviewed upload consent; and
`--billable-requests-confirmed` confirms the bounded, potentially billed work.
All three are required every run. `--max-billable-requests` must be an integer
from 1 through 32 and must cover the preflight's approved generation-task
count. The controller makes no automatic retry, retry budget, fallback model,
or extra request. A human-directed retry needs a new explicit run decision and
a new result review cycle; never let the controller issue it autonomously.
If a later task fails, earlier provider requests in that same run may already
be billable even though atomic publication leaves no result pack. Diagnose the
failure and obtain all three confirmations again before a rerun.

The process reads its credential only from `OPENAI_API_KEY` at run time. It has
no API-key command-line flag, config field, request field, or artifact field.
Never put that value in a command history intended for sharing, prompt,
Generation Request, Plan, Review, log, contact sheet, stdout JSON, result pack,
or Git. A missing key fails before an API request; it must not be substituted by
any other environment variable or by a Codex credential.

### Fixed provider request and upload scope

For every permitted task, the controller uses exactly this OpenAI API contract:

| Request option | Fixed value |
| --- | --- |
| model | `gpt-image-2-2026-04-21` |
| quality | `high` |
| size | `1024x1536` |
| output format | `png` |
| background | `opaque` |
| moderation | `auto` |
| `input_fidelity` | omitted |

Do not add a caller override for these fields, a seed, a URL input, a mask,
partial images, or a second model route. The controller uploads only the
reference **images** listed by accepted tasks in the approved plan. It never
uploads the reference video, audio, unapproved task references, result
candidates, an arbitrary project file, or a file identified only by a prompt.
The original task instructions are scope-bound to the plan; do not append
unreviewed instructions at run time.

### Result publication and review

The output directory must be a new guarded direct child of `project-root`. On a
successful run it contains only one normalized, metadata-free PNG named
`<target_slot_id>.png` for each approved generation task. It contains no JSON,
prompt, credential, log, source copy, audio, sidecar, nested directory, or
partial/streamed image. The public run summary identifies only
`schema_version`, `operation`, `output_dir`, `counts`
(`generation_tasks`, `billable_requests`, `output_assets`), and each output
asset's `slot_id`, relative `path`, `sha256`, and `media_type`.

If an API call, returned-image check, metadata stripping, PNG normalization, or
publication check fails, fail the whole run and publish no result pack. Do not
turn a partial response into a candidate. After success, use the unchanged
`propose-generation-results -> result review -> assemble-generation-pack ->
propose-assets -> asset review -> freeze-assets` sequence. Generated output is
never self-approved.

### Capability, limitation, and cost disclosure

OpenAI documents that GPT Image can use one or more reference images and that
`gpt-image-2` processes image inputs at high fidelity automatically, which is
why this controller must omit `input_fidelity`. Those are provider capabilities,
not quality proof. OpenAI also documents limitations in recurring-character or
brand consistency, precise text, and layout-sensitive composition. Manually
review person identity, pose, garment/product/logo detail, text, hands,
background, and exact composition for every result. See the official
[Image generation guide](https://developers.openai.com/api/docs/guides/image-generation).

At the documented baseline, one high-quality 1024x1536 GPT Image 2 output is
about **$0.165**, before input text and image-token costs; the maximum 32-output
run therefore has a $5.28 output-only baseline plus input costs. Pricing,
availability, and quotas can change, so check the official
[Image generation guide and calculator](https://developers.openai.com/api/docs/guides/image-generation)
and [pricing page](https://platform.openai.com/pricing) before approving a run.
