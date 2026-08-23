# v0.6 controller and adapter policy

## Scope

The v0.6 CLI records an execution declaration and verifies local files around
it. It does not call an adapter. Do not describe this policy as a bundled
virtual try-on, image-generation, video-generation, CUDA, or provider
integration.

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
candidates, credentials, or unrelated project material. The CLI still does not
upload or authenticate anything; this profile is an auditable controller
declaration, not technical enforcement.

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
