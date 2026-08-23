# Proposal, Review, and Compiler Plan contract

## Version boundary

The 0.4.0-alpha workflow has three distinct documents:

| Document | Schema version | Role |
| --- | --- | --- |
| Proposal | 0.4.0 | Strict, bounded candidate for local review |
| Review decision | 0.4.0 | Explicit decision bound to one Proposal hash |
| Frozen Compiler Plan | 0.3.0 | Canonical input to the existing compiler |

The machine-readable contracts are
[Proposal](../assets/schemas/compiler-plan-proposal.schema.json),
[Review](../assets/schemas/review-decision.schema.json), and the existing
[Compiler Plan](../assets/schemas/compiler-plan.schema.json). The Template IR
remains schema version 0.2.0.

Proposal is not an alternate Compiler Plan. It always has review_required set
to true, and no Proposal can be compiled directly. Only freeze-plan may emit
the canonical 0.3.0 Frozen Compiler Plan.

## Local-only boundary and safe technical fingerprint

Raw media and visual evidence stay local. Proposal, Review, CLI JSON, and
frozen-plan artifacts must not expose:

- source filenames or absolute paths;
- FFmpeg, ffprobe, or other tool paths;
- container tags, title, artist, comments, account identity, or other
  identifying source metadata;
- raw probe payloads, raw media, image frames, or unbounded evidence data.

The strict Proposal may contain the required technical source fingerprint:
SHA-256, width, height, exact frame_count, fps, and has_audio. These facts
bind the local proposal to the media without disclosing a source filename,
path, tag, identity, or raw probe. Do not add other source metadata.

The caller-selected direct-child output name becomes the prefix of relative
artifact references. It must therefore be a neutral project label and must not
reuse a source filename, person name, account identifier, or other private
input label.

Evidence artifacts are project-relative, bounded local references. They support
review but do not transfer evidence content through JSON. Their paths must not
escape the project root or encode an absolute path.

## Proposal contract

propose accepts only an authorized local fixed-subject-carousel S1 source. It
first enforces exact CFR, zero rotation, duration no greater than 60 seconds,
required local tools, and a project-contained output path. Its output directory
must be one new direct child of project-root; absolute paths, nested paths,
`.`, `..`, and existing targets are rejected before media work or writes. It
then writes a strict 0.4.0 Proposal and a pending Review template atomically.

The Proposal must include:

- the fixed-subject-carousel family and local-only privacy boundary;
- review_required: true;
- the safe technical source fingerprint;
- one nested candidate plan;
- bounded confidence and candidate evidence;
- bounded limitations;
- hashes and project-relative references for the overview contact sheet,
  geometry preview, and timing profile.

The candidate plan proposes only the bounded S1 facts needed by the existing
compiler:

- source_rect as a maximal centered source crop matching the supported 9:16
  output aspect; use the full source only when it already matches that aspect;
- top carousel boundary;
- subject region;
- slot_count;
- switch timing;
- proportional carousel layout;
- background color;
- explicitly selected audio and authorization facts.

The source crop is a composition heuristic, not semantic platform-UI detection
or removal. Platform chrome, non-centered content, a nonuniform crop, ambiguous
timing, and all semantic meaning require reviewer correction. Proposal does not
infer identity, garment, product, UI, watermark, or hidden-pixel content.

## Review contract

The pending Review template must remain pending until an accountable reviewer
acts. An approved Review is valid only when all of the following are true:

1. proposal_sha256 equals the canonical hash of the exact Proposal being
   frozen;
2. decision is approved and reviewer_confirmed is true;
3. all confirmations are true: family, geometry, slot_count, timing, carousel,
   background, audio, and authorization;
4. approved_plan is present and valid for the approved facts;
5. any preserved audio has confirmed audio rights and any reference use has
   confirmed authorization.

The reviewer may correct approved_plan. That edit is deliberate: it replaces a
candidate with reviewed facts. It never lets a reviewer omit a confirmation,
approve a different Proposal, or turn a hash mismatch into a warning.

validate-proposal validates the strict Proposal and its nested plan.
validate-review validates a Review document. freeze-plan must validate both
documents together because only it can establish the Proposal hash binding and
the relationship between the approved_plan and Proposal.

## Freeze contract

freeze-plan receives Proposal and Review plus a project root and output
directory. Before it writes a final artifact, it must validate:

- Proposal and Review are named by normalized paths relative to project-root;
  absolute local, drive-rooted, and UNC packet paths are rejected before any
  candidate packet inspection. This freeze-only containment contract does not
  change the independent validate-proposal or validate-review commands;

- the output directory is one new direct child of project-root, using the same
  absolute/nested/dot/existing-target rejection as propose;

- Proposal schema, nested candidate plan, safe artifact references, and bounded
  evidence;
- Review schema, approval state, reviewer confirmation, and every required
  confirmation;
- canonical Proposal hash binding;
- approved_plan structural and semantic compatibility with the frozen
  Compiler Plan contract;
- local-only authorization, audio, path, and output constraints.

On success, freeze-plan canonicalizes the reviewed approved_plan as a
schema-version 0.3.0 Compiler Plan. It strips Proposal-only candidates,
confidence, evidence, source fingerprint, and review workflow metadata. It
does not create a new 0.4 Compiler Plan schema.

On any validation or operational failure, freeze-plan exits with code 2 and
publishes no final output. Staging and publication must be atomic, so a failed
freeze cannot leave a partial Frozen Compiler Plan in the target directory.

## Coordinate spaces

geometry.source_rect is measured in source-media pixels. Its width and height
establish the clean compiler canvas. geometry.carousel_rect,
geometry.subject_rect, and carousel.origin use that canvas pixel space with
(0, 0) at the canvas top-left. Proportional carousel layout is reviewed before
freeze and resolves into the compatible frozen plan. Output profiles scale the
base canvas; analysis downscaling never changes frozen coordinates.

The source rectangle must be reviewed even if it was generated from the
centered 9:16 composition heuristic. No stage may describe that heuristic as
a semantic crop, platform-chrome detector, or UI-removal mechanism.

## Timing and audio in the frozen plan

uniform divides the local media duration into slot_count segments. hybrid
starts from that division and snaps switches to local evidence within the
bounded analysis window. manual uses explicit switch starts.

The frozen-plan rules remain:

- switch_frames is required only for manual and forbidden for uniform and
  hybrid;
- manual requires exactly slot_count unique, strictly increasing starts within
  the source duration, with every segment meeting min_segment_frames;
- uniform and hybrid reject durations that cannot yield the required minimum
  segment length;
- preserve audio requires audio_rights_confirmed and audio.required true;
- mute requires audio.required false.

Schema validation cannot establish media-relative facts. The existing compiler
must still check that source_rect lies inside the source, canvas geometry is
renderable without implicit crop or scale, all timing boundaries are inside the
source duration, and decoded media meets its existing safety gates.

## Exit and fail-closed rules

propose can exit 0 only after it has atomically published a review_required
Proposal packet. That is never approval. Proposal/review validation errors and
freeze-plan errors exit 2. Compile keeps its existing exit behavior: 0 for a
completed compile without required review, 1 when artifacts exist but review
is required, and 2 for validation or operational failure.

Do not freeze or render when authorization is absent, preserved audio lacks
rights, a hash is mismatched, a required confirmation is false or missing, a
schema rejects a document, an unknown property appears, evidence/path bounds
are violated, a media gate fails, or the local-only boundary cannot be
maintained. Report the failed gate; never guess missing geometry, timing,
semantic meaning, authorization, or media behavior.
