# Reference video support levels

Use the most conservative level that matches any material part of the reference
**and** its reviewed `rebuild_requirements`. A support level describes the
complexity of the requested reconstruction; it never relaxes a declared motion,
audio, or lip-sync requirement. Read
[motion-audio-contract.md](motion-audio-contract.md) with this file.

## Current executable boundary

The bundled renderer is limited to static images, 2D layout/transforms,
transitions, and selected audio. It has no temporal subject-motion controller,
voice cloning, audio/SFX rebuilding, or lip-sync engine. Therefore renderer
acceptance remains limited to `motion_required: false`, `motion_mode: static`
or `layout-only`, `lip_sync_required: false`, and an actually supported audio
treatment.

v0.10 adds a separate provider-neutral local file-drop review/freeze chain for
a reviewed S3 temporal result. It permits only `local-only` + `local-file-drop`
with `cloud_upload_confirmed: false`: the user independently operates any local
tool, then drops the result locally. It does not make the renderer a temporal
executor, invoke a provider, upload a file, read a key, or attest a provider's
capability. A frozen delivery always records
`provider_provenance: unattested-local-file-drop` and
`bitstream_faithful: false`.

An external motion provider (including a possible future Runway route) is not
installed or connected by this repository. Do not list it as a bundled S2/S3
executor merely because v0.10 can review a local dropped result.

## S1 — deterministic structure

Characteristics:

- one primary subject;
- fixed camera or negligible camera motion;
- simple or replaceable background;
- regular hard cuts, card motion, carousels, captions, or 2D overlays; and
- `motion_required: false` with `motion_mode: static` or `layout-only`.

Expected result: reproduce frame timing, layout, approved static appearance,
2D transforms, transitions, and selected audio deterministically. It does not
reproduce a person’s continuous action. A pan, zoom, transform, cross-fade, or
carousel move of a still asset remains layout-only.

## S2 — tracked composite

Characteristics:

- one primary moving subject;
- slow camera motion;
- moderate, trackable occlusion;
- perspective or scale changes; and
- dynamic masks or color matching are required.

Expected result: a future tracked-composite route may preserve structure and
some movement after mask/keyframe correction. The current bundled renderer does
not implement S2 tracking, so a request at this level fails closed rather than
falling back to an unclaimed static result.

## S3 — performance or generative motion

Characteristics:

- `motion_required: true` with `motion_mode: pose-transfer` or
  `video-to-video`;
- fast movement, large pose changes, cloth deformation, turns, hair motion, or
  strong camera movement;
- requested lip sync, `audio_mode: rebuild-sfx`, or
  `audio_mode: clone-authorized-voice`; or
- material temporal-consistency risk.

Expected result: the user may independently operate one local tool, then put
one local result in a new pack for v0.10 to bind to the reviewed Template IR
0.3, frozen Manifest 0.2, and action reference. Require the strict MP4 profile,
metadata cleanliness, technical negative checks, and full-playback human
review. The result remains an unattested local file drop—not a bundled
controller capability or automatic motion/audio proof.

## S4 — unsupported exact mode

Characteristics:

- tightly interacting people;
- mirrors, complex reflections, transparency, smoke, or liquids across
  replacement boundaries;
- severe occlusion of essential content;
- rapid mixed editing or effects not expressible by the reviewed IR;
- corrupted or extremely low-quality input; or
- unclear authorization.

Expected result: provide analysis and a simplification plan. Do not proceed
with an exact-rebuild promise.

## Legacy Template IR 0.2.0

Template IR 0.2.0 contains no `rebuild_requirements`. A legacy output may only
be called `structure_only_unclaimed`, regardless of support-level language or
how similar an individual frame looks. It cannot be accepted as a performance,
voice-imitation, or lip-sync rebuild.

## Classification evidence

Store the evidence for the assigned level and requirements:

- the exact `motion_required`, `motion_mode`, `audio_mode`,
  `lip_sync_required`, and `voice_likeness_rights_confirmed` values;
- subject count and track continuity;
- camera motion magnitude;
- pose velocity;
- occlusion duration and area;
- number and type of cuts;
- text/UI coverage;
- unsupported visual effects;
- input integrity warnings;
- reviewed temporal Plan/Results evidence for a non-static result, including
  the selected frozen input-slot hashes, action-reference/result inventories,
  and the explicit limitation that provider provenance is unattested; and
- confidence and required human corrections.
