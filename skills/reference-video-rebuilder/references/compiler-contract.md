# Compiler Plan contract

The frozen Compiler Plan is the compact, local-only input to the v0.3
`fixed-subject-carousel` compiler. Its machine-readable source of truth is
[`../assets/schemas/compiler-plan.schema.json`](../assets/schemas/compiler-plan.schema.json).
The plan deliberately contains no source path, media, hash, or private
evidence payload.

## Coordinate spaces

`geometry.source_rect` is measured in source-media pixels. Its `width` and
`height` establish the clean compiler canvas. `geometry.carousel_rect`,
`geometry.subject_rect`, and `carousel.origin` use that canvas's pixel space,
with `(0, 0)` at its top-left. Output profiles scale this base canvas; analysis
downscaling never changes plan coordinates.

## Proposal, review, freeze

1. **Proposal** produces a candidate plan plus a local review packet. Proposal
   metadata may include `confidence` (a 0–1 estimate of evidence support) and
   `review_required` (whether a human decision is required before freeze).
   Neither field belongs in the frozen plan, and confidence is never evidence
   of rights.
2. **Review** confirms the proposal's measured geometry, timing, and rights.
   A `review_required: true` proposal cannot be frozen until it is approved.
3. **Freeze** strips proposal-only metadata, validates this schema and the
   semantic rules below, then writes the canonical Compiler Plan. The compile
   report keeps the review result, and any emitted Template IR carries it as
   `support.review_required`. Rendering accepts only templates where that flag
   is absent or explicitly `false`.

## Timing

`uniform` divides the local media duration into `slot_count` segments.
`hybrid` starts from that division and snaps switches to local evidence within
`analysis.snap_window_frames`. `manual` takes explicit switch starts in
`timing.switch_frames`.

The exact cross-field rules are:

- `switch_frames` is required only for `manual`; it is forbidden for `uniform`
  and `hybrid`.
- For `manual`, semantic validation requires exactly `slot_count` unique,
  strictly increasing switch starts, each inside the source duration; each
  resulting segment must satisfy `min_segment_frames`.
- For `uniform` and `hybrid`, the compiler must reject durations that cannot
  produce `slot_count` segments of at least `min_segment_frames`.
- `audio.mode: "preserve"` requires
  `authorization.audio_rights_confirmed: true` and `audio.required: true`;
  `audio.mode: "mute"` requires `audio.required: false`. The schema enforces
  these combinations.

The schema intentionally cannot validate media-relative facts. Semantic
validation must use the locally probed media to ensure `source_rect` lies
within the source, canvas rectangles and the carousel placement are renderable
without an implicit crop or scale, and every derived timing boundary is within
the duration. A structurally valid manual list with the wrong length is thus a
semantic failure, not a schema failure.

## Deterministic local boundary and artifacts

Raw media, hashes, paths, probes, and evidence frames stay local. Given the
same local inputs and approved review decisions, the compiler canonicalizes
the plan and produces the same frozen result; it must not upload media or use
an unreviewed remote result as frozen truth.

The workflow emits a compact proposal/review packet (with at most
`analysis.max_evidence_frames` local evidence references), a review decision,
and a schema-valid frozen plan. This keeps agent handoffs small: send scalar
geometry, timing, and evidence references rather than frames, probe dumps, or
media. `analysis.width` and `max_evidence_frames` are explicit cost bounds,
not quality guarantees.

## Fail closed

Do not freeze or render when the schema rejects a plan, unknown properties are
present, reference rights are not confirmed, preserved audio lacks confirmed
rights, semantic validation fails, required review is unresolved, or the
local-only boundary cannot be maintained. The compiler must report the failed
gate instead of guessing missing geometry, timing, authorization, or media
behavior.
