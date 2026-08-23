# Language-model routing and quality policy

Use role-based routing. Treat a model choice as an implementation detail that must not weaken product acceptance criteria.

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
- selecting privacy, rights, and provider policies;
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
   invariants, tests, golden evidence, and acceptance criteria.
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
   timing, and the complete video.
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
- the change affects path safety, privacy, licenses, cache isolation, or provider uploads;
- a schema change can alter rendering semantics;
- frame timing, masking, occlusion, identity, garment, or product fidelity is involved;
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
