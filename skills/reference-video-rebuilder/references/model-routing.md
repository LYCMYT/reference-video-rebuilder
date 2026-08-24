# Language-model routing and quality policy

Use role-based routing. Treat a model choice as an implementation detail that must not weaken product acceptance criteria.

## v0.8 motion/audio decision boundary

`controller_current` owns the decision whether the user requires static
structure, layout-only movement, pose transfer, video-to-video motion, audio
preservation/replacement, rebuilt SFX, authorized voice cloning, or lip sync.
It must freeze the exact Template IR 0.3.0 `rebuild_requirements` values before
any implementation or external-controller work begins:

- `motion_required`;
- `motion_mode`: `static`, `layout-only`, `pose-transfer`, or
  `video-to-video`;
- `audio_mode`: `mute`, `preserve-reference`, `replace-upload`,
  `rebuild-sfx`, or `clone-authorized-voice`;
- `lip_sync_required`; and
- `voice_likeness_rights_confirmed`.

Current static rendering can never satisfy `motion_required: true`,
`pose-transfer`, `video-to-video`, `lip_sync_required: true`, `rebuild-sfx`,
or `clone-authorized-voice`. A controller must not relabel static transforms,
cross-fades, or preserved audio as a successful downgrade. Template IR 0.2.0
outputs are `structure_only_unclaimed` only.

No external motion controller is integrated at this time. A potential future
Runway route is a product/controller decision requiring a separate approved
adapter, upload/rights boundary, and evidence plan; it is not an installed or
available executor. Do not delegate provider selection, voice-rights judgement,
or motion/voice acceptance to a worker or a provider model.

## v0.6 generation and asset decision boundary

controller_current owns the decisions that the strict local scanner and v0.6
generation bridge cannot make: whether a Template slot means the intended
content; whether an identity, body/pose, garment, product, background, or logo
is visually faithful; whether an external controller/privacy profile is
acceptable; whether rights and cloud consent are sufficient; whether a human
review is approved; and whether the completed render is acceptable. Exact
filename matching, a contact sheet, a bounded controller declaration, media metadata, and a
passing JSON validator do not answer those questions.

Freeze the v0.6 contract before implementation: normalized packet paths,
direct-child reference/result/output names, external-only execution modes,
explicit `controller-cloud` consent, no CLI shell/network/model/download
operation, hash-bound plan/result reviews, metadata-free image assembly,
media-only handoff, v0.5 snapshot rendering, and P0 tests. After that freeze,
use gpt-5.6-terra with reasoning.effort max for a bounded implementation or
deterministic test task. Terra must escalate rather than turn an unresolved
mapping, controller policy, visual judgement, or rights judgement into code.

## v0.7 OpenAI controller decision boundary

The standalone v0.7 OpenAI GPT Image 2 controller is an execution surface, not
an approver. `controller_current` and the human reviewer must decide before
preflight whether the exact approved plan is `controller-cloud` and
`controller-managed`, binds `openai-gpt-image-2` / `2026-04-21`, authorizes
only its accepted reference images, and has an acceptable 1–32 billed-request
cap. A passing read-only preflight only establishes that the bounded run is
eligible; it does not approve rights, cloud upload, provider terms, or spend.

Before run, require the separate rights, cloud-upload, and billed-request
confirmations. Do not hand a lower-cost worker, the provider model, or the
controller executable the authority to infer a task reference, widen an upload
set, choose a different request setting, retry a failure, or accept a charge.
The controller's fixed `gpt-image-2-2026-04-21` / high / 1024x1536 / PNG /
opaque / auto configuration and its automatic high-fidelity image-input
handling do not establish identity, brand, text, pose, or exact-composition
fidelity.

After a successful atomic PNG-only publication, `controller_current` plus a
human must independently review every result before it reaches v0.6 result
review and v0.5 asset freeze. A provider error, moderation block, partial
result, or visual failure stops the run; no automatic retry is permitted. Any
later retry is a new explicit human decision with a new result review packet.
Never record or expose `OPENAI_API_KEY`; it is not evidence of which Codex
account, if any, was used.

For the v0.7.1 no-key Codex built-in ImageGen route, `controller_current` must
first approve a distinct `controller-cloud` + `controller-managed` plan pinned
to `codex-builtin-imagegen` / `2026-08-24`. It may then make one built-in image
generation call per accepted task with only that task's approved reference
images. This route must not be labeled local-only or file-drop, must not claim
API credential/billing identity, and must not delegate visual acceptance to a
worker or the image model. Every selected output still enters the bound result
review and asset-freeze pipeline.

## Roles

Use logical profiles in orchestration code so product logic is not coupled to a
model name:

- `controller_current`: the resolved primary model for the active session;
- `builder_quality`: `gpt-5.6-terra` with `reasoning.effort: max`;
- `builder_standard`: `gpt-5.6-terra` with `reasoning.effort: high`;
- `mechanical_worker`: an evaluation-approved Terra medium configuration or a
  lower-cost model for a narrowly mechanical task.

### Controller and final reviewer

Use the current primary session model for:

- interpreting ambiguous user intent;
- analyzing reference-video semantics and creative structure;
- assigning S1–S4 support levels;
- deciding keep, remove, and replace boundaries;
- approving Template IR architecture and schema changes;
- selecting privacy, rights, and controller policies;
- determining whether a static, layout-only, pose-transfer, video-to-video,
  audio, voice, or lip-sync result is actually requested and supported;
- confirming voice-likeness authorization before
  `clone-authorized-voice` is considered;
- approving a Generation Request/Plan, controller declaration, and any cloud
  consent before an external controller receives assets;
- judging every generated result's identity consistency, garment/product/logo
  fidelity, background correctness, hands/artifacts, and retry decision;
- judging identity, garment, product, and final-video quality;
- accepting warnings and declaring a release ready.

Record the resolved model and reasoning configuration in the run manifest. Do not hardcode an assumed controller model name.

### Implementation worker

Use `gpt-5.6-terra` with `reasoning.effort: max` as the default delegated worker for a frozen, bounded implementation task, including:

- implementing an approved interface or schema;
- writing renderer components from an approved timeline;
- adding deterministic validators and tests;
- performing bounded refactors and bug fixes;
- producing structured documentation from approved decisions.

`max` is a reasoning-effort setting, not a separate model. Keep the requested model ID and effort distinct in logs.

### Lower-cost worker

Use Terra at `high`, `medium`, or lower only after representative evaluations show no material regression for that task class. Reserve still lower-cost models for mechanical work such as file inventory, stable JSON conversion, fixture expansion, log summarization, or formatting.

Never let a lower-cost worker independently decide support level, semantic slots, removal boundaries, identity quality, garment accuracy, security policy, provider rights, or final acceptance.

## Delegation contract

Before delegation, the controller must freeze:

- objective and non-goals;
- exact files and allowed scope;
- input and output schema;
- invariants and forbidden changes;
- deterministic tests;
- visual or human acceptance criteria;
- escalation triggers.

Give the worker only the task-local context it needs. Do not delegate an unresolved product decision disguised as a coding task.

Split long work into bounded packages. A worker must stop and escalate instead
of expanding scope when a change crosses modules, changes schema semantics, or
requires a new visual/product decision.

## Quality gates

Use the following gates in order:

1. **G0 — contract freeze:** the controller freezes scope, interfaces,
   invariants, tests, golden evidence, and acceptance criteria, including the
   exact v0.8 `rebuild_requirements` values and a capability match when motion
   or audio rebuilding is claimed.
2. **G1 — worker self-check:** the worker returns changed files, tests,
   warnings, assumptions, and any requested-versus-resolved model difference.
3. **G2 — deterministic verification:** run lint, type, schema, unit, and
   integration checks outside the worker's reasoning.
4. **G3 — scope and invariant review:** verify the diff stayed within allowed
   files and did not change frozen decisions.
5. **G4 — controller code review:** the current primary model reviews every
   substantive worker code change, not only security or renderer code.
6. **G5 — golden end-to-end regression:** run both the current reference case
   and a second replacement-asset set against an independently approved
   baseline.
7. **G6 — visual acceptance:** the current primary model reviews contact
   sheets, residual overlays, identity consistency, garment/product accuracy,
   timing, and the complete video; it also verifies the requested subject
   motion, audio treatment, and lip sync rather than accepting static-frame
   similarity or audio-stream presence.
8. **G7 — release sign-off:** record hashes, test evidence, warnings, reviewer,
   and final verdict before packaging or publishing.

A worker cannot prove correctness solely with tests it added in the same
change. Require an existing regression, an independent validator, or an
approved golden artifact. No model may approve its own output.

Reject or escalate when any gate fails, confidence is low, requirements
conflict, or the worker changes frozen decisions. Retry a bounded worker once
with failure evidence; after a second failure, escalate to the controller or a
stronger configuration.

## Downgrade rules

Do not downgrade solely because a task is long or expensive. Downgrade only when all are true:

- the task class has a passing evaluation baseline;
- inputs and outputs are structured;
- deterministic checks cover the material risks;
- no ambiguous visual or product decision remains;
- controller review remains in the workflow.

Promote a lower configuration only after A/B evaluation against the current accepted baseline. Compare task success, code correctness, test results, visual acceptance, total retries, latency, and cost.

## Escalation triggers

Escalate to the current primary model when:

- the reference-video meaning or layer ownership is ambiguous;
- an external controller, cloud consent, upload scope, retention, or licensing
  declaration is ambiguous;
- the change affects path safety, privacy, licenses, cache isolation, or
  controller uploads;
- a schema change can alter rendering semantics;
- a generation plan changes executor mode, privacy profile, consent, paths, or
  media-normalization semantics;
- frame timing, masking, occlusion, identity, garment, or product fidelity is involved;
- a request requires pose transfer, video-to-video motion, rebuilt SFX,
  authorized voice cloning, or lip sync;
- generated results disagree with deterministic metrics or human review;
- the same worker failure recurs twice;
- a requested action is outside the frozen scope.

## Runtime limitation

Use per-agent or per-request model overrides only when the active runtime exposes them. If it does not, keep the current model and apply the same delegation contract and gates. Never tell the user that a model switch occurred unless the runtime confirms it.

Language models accept sampled frames and contact sheets, not an assumption that the raw video is directly understood. Keep video decoding, frame extraction, audio analysis, and full-frame QA in deterministic tools.

## Run-manifest fields

Record for every model task:

- `task_id`, `parent_run_id`, `task_class`, and `risk_level`;
- `requested_model`, `resolved_model`, `provider`, `reasoning_effort`, and
  `reasoning_mode`;
- `routing_policy_version`, `prompt_version`, `schema_version`, and
  `eval_baseline_version`;
- `allowed_files`, `input_hashes`, `output_hashes`, and
  `diff_or_commit_hash`;
- `test_commands`, `test_results`, and `qa_metrics`;
- `input_tokens`, `cached_input_tokens`, `output_tokens`, `latency_ms`, and
  `estimated_cost` when the runtime reports them;
- `retry_count`, `escalation_reason`, `reviewer_model`, `review_reference`, and
  `final_verdict`.

If a golden end-to-end regression or final review fails, retain the last
approved template and renderer as the active version. Do not publish a worker
change merely because its unit tests pass.
