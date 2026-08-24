# v0.6 controller policy, v0.7 OpenAI API policy, and v0.7.1 Codex ImageGen policy

## Scope

The v0.6 `video_remix.py` CLI records an execution declaration and verifies
local files around it. It does not call an adapter. Two distinct reviewed cloud
surfaces may create result files: the v0.7 standalone API controller and the
v0.7.1 manually orchestrated Codex built-in ImageGen handoff. Do not conflate
their credentials, quotas, billing, or execution contracts, and do not describe
either surface as a bundled
virtual try-on, video-generation, CUDA, or automatic controller router.

Use the Generation Request/Plan `execution_profile` for one of two values only:

| Mode | Appropriate use | CLI action |
| --- | --- | --- |
| `local-file-drop` | A user or a separately operated local tool creates reviewed stills and puts them in a result pack. | Validate/propose/assemble files only. |
| `controller-managed` | A separately governed controller acts on a reviewed plan and returns local result files. | Record the declaration and verify the returned pack only. |

`local-command` is not an alpha mode. Do not add shell command strings,
subprocess adapters, weight downloaders, automatic CUDA discovery, embedded
models, browser automation, controller credentials, or API calls to the CLI.

## Privacy profiles

### `local-only`

Keep all approved references and processing on the machine or worker selected
by the user. The controller must fail or ask for direction instead of silently
falling back to a cloud service. The CLI performs no network activity.

### `controller-cloud`

Use only if the Generation Request and approved Plan Review both set
`cloud_upload_confirmed: true`. Record bounded `adapter_id`, `adapter_version`,
and (for `controller-managed`) `controller_label`; none can be a path, URL, or
credential. Limit the upload to the minimum approved model/outfit/product/
background references. Do not send the raw reference video, unapproved result
  candidates, credentials, or unrelated project material. `video_remix.py`
  still does not upload or authenticate anything; this profile is an auditable
  controller declaration, not technical enforcement.

## Required declaration

Before plan approval, record the schema-required fields for:

- `execution_profile` and `privacy_profile`;
- bounded `adapter_id` and `adapter_version`, plus `controller_label` for
  `controller-managed` (never a path, URL, or credential);
- code, model, weight, and runtime license/commercial-use status as applicable;
- expected input classes, output classes, seed/reproducibility information,
  retention/deletion policy, and material limitations;
- hardware/runtime expectation for an external local CUDA workflow;
- retry policy and whether the controller can guarantee a fresh result pack.

Set `cloud_upload_confirmed: false` for `local-only`. Set it to `true` in both
the Generation Request and Plan Review for `controller-cloud`; a generic rights
flag or controller label is not a substitute.

Do not store controller secrets, bearer tokens, private prompt text, user
absolute paths, or raw source video metadata in review packets or public CLI
JSON.

## Selection and quality routing

Choose the least generative option that can satisfy the approved template:

1. direct supplied render-ready still;
2. deterministic crop/scale/color/mask/composite;
3. externally generated static image or local virtual try-on result;
4. unsupported or manual fallback.

v0.6 ends at static result assembly. Short video modification/generation,
arbitrary video adapters, and automatic controller routing are outside the alpha.
Use a distinct result proposal/review for each retry rather than regenerating a
whole project or replacing an approved image in place.

The controller and human reviewer, not the adapter declaration, decide whether
identity, body proportion, pose, clothing silhouette, color, pattern, logo,
product markings, background, text, hands, and edges are acceptable. If a
controller cannot preserve a required detail, reject that slot or choose a
different external workflow. Do not turn a failed semantic check into a
technical pass.

## License and rights rules

- Treat repository code, controller code, model weights, training data,
  third-party runtime binaries, user assets, and output assets as distinct
  license/rights surfaces.
- Confirm the right to process likenesses, products, brands/logos, music, and
  source references before generation planning and result intake.
- Do not make a non-commercial model or unlicensed checkpoint the default for
  commercial work.
- Pin and record the chosen external adapter version when reproducibility
  matters, but do not claim that a recorded version makes output deterministic.
- Keep compatible attribution when redistributing third-party code; do not copy
  code, weights, prompts, or assets merely because they are public.

## Escalation

Stop and escalate to `controller_current` when cloud consent is absent or
ambiguous, controller terms/retention are unclear, a source is not authorized,
an adapter changes scope, or visual evidence conflicts with a passing media
check. The external controller must not silently switch `adapter_id`,
`adapter_version`, `controller_label`, route, privacy profile, or result
semantics after plan approval.

## v0.7.1 Codex built-in ImageGen handoff

Use only the exact approved declaration
`controller-managed` + `controller-cloud` +
`codex-builtin-imagegen` / `2026-08-24`, with a bounded controller label and
`cloud_upload_confirmed: true` in the Request and approved Plan Review. This
route uses the active Codex product's built-in image-generation capability and
requires no `OPENAI_API_KEY`; it is not eligible for
`openai_image_controller.py` preflight or run.

Invoke one generation per approved target slot and include only that task's
approved reference images. Never upload video, audio, packets, other pack files,
or rejected candidates. Do not infer API billing, organization, project, request
IDs, or retention behavior from the Codex session. Selected images still enter
the normal v0.6 result review and v0.5 asset freeze, and any retry uses a new
result pack/review cycle.

## v0.7 OpenAI GPT Image 2 controller

### Only eligible adapter declaration

The standalone controller accepts one provider contract only. Before preflight,
the approved Generation Plan and its approved Plan Review must bind all of the
following:

| Field | Required value |
| --- | --- |
| `execution_profile` | `controller-managed` |
| `privacy_profile` | `controller-cloud` |
| `adapter_id` | `openai-gpt-image-2` |
| `adapter_version` | `2026-04-21` |
| request and review cloud consent | `cloud_upload_confirmed: true` |

Do not route a `local-file-drop`, `local-only`, pending, rejected, unbound, or
adapter-drifted plan to this controller. `video_remix.py` remains fully offline
for every v0.6 path, including preparation, validation, result proposal, and
assembly.

### Provider request and credential isolation

Every request fixes `gpt-image-2-2026-04-21`, `high`, `1024x1536`, `png`,
`opaque`, and `auto` moderation. Omit `input_fidelity`; do not let a user or
task change the model, quality, size, format, background, moderation, retry
behavior, or provider route. There is no automatic retry.

Read the API key only from `OPENAI_API_KEY` during `run`. Do not offer an API
key flag, request field, plan field, config file, prompt placeholder, or second
environment variable. Never record a secret in logs, stdout JSON, reviews,
contact sheets, result packs, Git, or support tickets. Codex in-app image tools,
if present, are a separate product surface: do not assert that they use this
key, account, identity, quota, or billing.

### Consent, cost, and upload minimization

`preflight` is read-only and offline. `run` requires all of:

- the v0.6 generation-rights confirmation;
- a fresh `--cloud-upload-confirmed` assertion that matches the reviewed plan;
- a fresh `--billable-requests-confirmed` assertion and a bounded
  `--max-billable-requests` value from 1 through 32.

Only reference images named by accepted, reviewed tasks may leave the machine.
Never upload a reference video, audio, arbitrary reference-pack file,
unapproved candidate, task from a rejected/pending review, credential, or raw
project packet. An approved rights flag does not broaden the upload set.

The cap controls billed image requests, not actual API spending. As documented
at release time, the high-quality 1024x1536 output baseline is $0.165 per
image, plus input costs; pricing can change. Require a reviewer to check the
current official pricing linked in [generation-contract.md](generation-contract.md)
before authorizing a run.

### Result boundary and escalation

Normalize a successful complete run into a new direct-child result pack that
contains only metadata-free `<target_slot_id>.png` files. Any failure means no
pack publication; do not accept partial output or retry automatically. Pass a
published pack through the existing v0.6 result review and v0.5 asset freeze.

Escalate when the uploaded reference set, task acceptance, plan binding,
provider terms, cost cap, fixed request settings, API failure, person/brand
consistency, or composition is ambiguous. Provider high-fidelity image input
handling is not an identity, logo, or exact-layout guarantee.
