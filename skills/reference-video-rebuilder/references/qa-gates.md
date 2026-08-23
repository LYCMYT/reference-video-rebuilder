# QA gates

## 0.5.0-alpha coverage

The bundled local CLI retains the v0.4 reference Proposal/Review/freeze-plan
path and adds v0.5 validate-asset-proposal, validate-asset-review,
propose-assets, and freeze-assets before rendering. A successful proposal is
never approval. Proposal/review validation and freeze failures return exit
code 2. Compile retains exit code 1 when its existing timing evidence requires
review, and render retains its technical verifier behavior.

The new planning workflow does not automate semantic acceptance. Gates 3, 4,
and 6 still require an agent or human review before claiming identity
consistency, garment fidelity, timing intent, or complete removal of platform
elements.

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
- prohibited layers are excluded.

## Gate 2 — asset readiness

- every required slot is mapped exactly as intended;
- input hashes and rights are recorded;
- file types, resolution, transparency, and duration are valid;
- the local-only policy is maintained; this alpha has no cloud adapter route;
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
- all warnings are visible to the reviewer.

## Gate 5 — final media

- output opens and fully decodes;
- expected frame rate, exact decoded frame count, dimensions, audio-stream presence, and container/video duration agreement pass;
- all requested output profiles derive from the same master timeline.

The alpha verifier does not yet establish pixel format, codec policy,
faststart/mobile behavior, subjective audio sync, or visual quality. Add an
explicit check before relying on any of those conditions.

## Gate 6 — prohibited overlay removal

Use known UI-region checks, contact sheets, the geometry/timing proposal
artifacts, and a human full-playback review. The bundled `0.5.0-alpha` Skill
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
