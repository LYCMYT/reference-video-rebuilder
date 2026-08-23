# QA gates

## 0.2.0-alpha.1 coverage

The bundled local CLI automates Template IR/asset validation and the technical
parts of Gate 5. `render` runs the technical verifier for every encoded output
and returns exit code `1` when an encoded delivery fails a check. Gates 3, 4,
and 6 are not automatically established by this verifier: require an agent or
human review before claiming identity consistency, garment fidelity, timing
intent, or complete removal of platform elements.

## Gate 1 — template structure

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
- cloud policy matches the selected adapter;
- no source or output path escapes the project allowlist.

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

Use OCR, known UI-region checks, contact sheets, and a human full-playback review. Require no residual platform logo, account text, comments, engagement rail, status/navigation bars, or visible reconstruction smear. In alpha, this is a required review gate rather than an automated pass claim.

## Result model

Each check returns:

- `status`: `pass`, `warn`, or `fail`;
- `metric` and threshold when applicable;
- frame or time range;
- affected slot or track;
- evidence path;
- suggested retry or correction.

Block packaging on any `fail`. Require recorded human acceptance for material `warn` results.
