# Reference video support levels

Use the most conservative level that matches any material part of the reference.

## S1 — deterministic template

Characteristics:

- one primary subject;
- fixed camera or negligible camera motion;
- simple or replaceable background;
- fixed or minor subject motion;
- regular hard cuts, card motion, carousels, captions, or 2D overlays;
- replaceable regions remain visible and separable.

Expected result: reproduce frame timing, layout, transforms, transitions, and audio events deterministically. Use static approved assets where possible.

## S2 — tracked composite

Characteristics:

- one primary moving subject;
- slow camera motion;
- moderate, trackable occlusion;
- perspective or scale changes;
- dynamic masks and color matching are required.

Expected result: preserve structure and most motion after keyframe or mask correction. Do not promise perfect garment consistency.

## S3 — generative modification

Characteristics:

- fast movement or large pose changes;
- cloth deformation, turning, hair motion, or strong camera movement;
- substantial regeneration of subject or scene;
- visible temporal consistency risk.

Expected result: match the creative form, pacing, and broad motion. Label the workflow experimental, render short segments, and preserve manual review.

## S4 — unsupported exact mode

Characteristics:

- tightly interacting people;
- mirrors, complex reflections, transparency, smoke, or liquids across replacement boundaries;
- severe occlusion of essential content;
- rapid mixed editing or effects not expressible by the current IR;
- corrupted or extremely low-quality input;
- unclear authorization.

Expected result: provide analysis and a simplification plan. Do not proceed with an exact-rebuild promise.

## Classification evidence

Store the evidence for the assigned level:

- subject count and track continuity;
- camera motion magnitude;
- pose velocity;
- occlusion duration and area;
- number and type of cuts;
- text/UI coverage;
- unsupported visual effects;
- input integrity warnings;
- confidence and required human corrections.
