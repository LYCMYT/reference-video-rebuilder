# Faithful source-preservation contract (v0.9)

## Purpose and boundary

Use this narrow path only when the requested result must preserve an authorized
reference's visible video, visible text, timing, and subject action exactly.
It is a source-preservation operation, not a clean-room rebuild, template
replacement, or generative reconstruction.

The output strips inherited and user-authored container metadata, but it must
preserve the source video bitstream. It may either preserve the source audio
bitstream or explicitly mute it. Unavoidable MP4 muxer structural tags (for
example, codec-brand or handler information) may remain; they are not source
metadata. The operation must never replace, remove, translate, synthesize,
OCR, infer, or semantically classify any visible content.

Do not use this path to remove platform UI, captions, watermarks, comments,
logos, people, products, backgrounds, or text. Those requests change the
source and belong to a separately reviewed reconstruction workflow.

## Reviewed plan

The machine-validated plan is `faithful-rebuild-plan.schema.json` version
`0.9.0`. Before execution, a reviewer must provide all of the following:

- `rights_confirmed: true` and `operation: faithful-reference-rebuild`;
- one project-relative source record with its SHA-256 and verified technical
  properties;
- `visible_text_policy: preserve-exact`;
- a manually created and human-reviewed `text_inventory` covering every
  visible text item, with its exact lines, frame range, and pixel region;
- `video_mode: preserve-bitstream`;
- `audio_mode: preserve-bitstream` or `mute`; and
- `metadata.strip_all: true`.

The text inventory is a human review artifact, not OCR output. Its purpose is
to make preservation auditable; it does not authorize text editing or infer
text that cannot be read by the reviewer.

## Local CLI

Validate before the preservation run:

```text
video-remix validate-faithful-plan <faithful-rebuild-plan.json> --json
video-remix faithful-rebuild <faithful-rebuild-plan.json> --project-root <project-dir> [--output-dir faithful-rebuild] [--ffmpeg <path>] [--ffprobe <path>] [--timeout-seconds <seconds>] --json
```

The plan itself carries `rights_confirmed: true`; there is no rights flag for
`faithful-rebuild`. Its output directory must be a new safe direct child of
`project-root`. A successful run publishes `replica.mp4` and
`rebuild-summary.json` with `completion: faithful_source_preservation`.

## Acceptance

Accept only when a full playback confirms that the result preserves the
reviewed source picture, visible text, timing, and action without replacement
or reconstruction, uses the declared audio treatment, and contains no
inherited or user-authored container metadata beyond unavoidable muxer
structural tags. A successful technical encode does not prove rights or that
the manual inventory is complete.

Reject if any requested operation would alter visible content, if a text item
cannot be manually inventoried, if the source fingerprint changes, or if the
output needs a different video or audio treatment. Do not downgrade those
requests to a structural render or claim full reconstruction.
