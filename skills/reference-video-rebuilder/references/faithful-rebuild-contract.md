# Faithful source-preservation contract (v0.9/v0.9.1)

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
text that cannot be read by the reviewer. Use an empty array only after a human
review confirms that the complete source contains no visible text.

## Supported source profile

This Alpha accepts only an MP4 containing exactly one H.264 video stream, zero
rotation, FFprobe-confirmed exact CFR timing, no subtitle/data/attachment or
other side streams, at most 60 seconds and 7,200 frames, and one of
`720x1280`, `1080x1920`, `1280x720`, or `1920x1080`. Audio may be preserved
bit-for-bit or muted. A source outside this profile needs a separately reviewed
normalization workflow and cannot receive this path's faithful claim.

The plan schema is
`<skill-root>/assets/schemas/faithful-rebuild-plan.schema.json`; a schema-valid,
non-sensitive starting point is
`<skill-root>/assets/project-template/faithful.rebuild.plan.example.json`. Paths inside
the plan use project-relative `/` spelling such as `source/reference.mp4`.
On Windows, obtain the required lowercase digest with:

```powershell
(Get-FileHash .\source\reference.mp4 -Algorithm SHA256).Hash.ToLowerInvariant()
```

## Local CLI

Validate before the preservation run:

```text
python <skill-root>/scripts/video_remix.py validate-faithful-plan <faithful-rebuild-plan.json> --json
python <skill-root>/scripts/video_remix.py faithful-rebuild <faithful-rebuild-plan.json> --project-root <project-dir> [--output-dir faithful-rebuild] [--ffmpeg <path>] [--ffprobe <path>] [--timeout-seconds <seconds>] --json
python <skill-root>/scripts/video_remix.py faithful-evidence <faithful-rebuild-plan.json> --project-root <project-dir> [--output-dir faithful-evidence] [--ffmpeg <path>] [--ffprobe <path>] [--timeout-seconds <seconds>] [--max-panels 24] --json
```

Resolve `<skill-root>` to the absolute installed Skill directory; it is a
placeholder, not a console command or a requirement to change the current
working directory.

The plan itself carries `rights_confirmed: true`; there is no rights flag for
`faithful-rebuild`. Its output directory must be a new safe direct child of
`project-root`. A successful run publishes `replica.mp4` and
`rebuild-summary.json` with `completion: faithful_source_preservation`.

v0.9.1 binds the preservation summary to the raw input-plan SHA-256, canonical
plan SHA-256, executor SHA-256, invocation-policy SHA-256, workflow version,
Python version, and bounded FFmpeg/FFprobe provenance. It records no tool or
installation path in the public result.

`faithful-evidence` is a separate, local, review-support operation. It samples
deterministic frames, draws the already declared inventory regions, and
publishes `contact-sheet.png` plus `faithful-evidence.json`. It performs no OCR
or semantic inference and cannot prove that the human inventory contains every
visible text item. A missing panel, truncation, or newly noticed text must be
resolved by editing and re-reviewing the plan before preservation acceptance.

Normal failures clean private staging and never publish the final target. If a
hostile concurrent process replaces a staged artifact with a hard link or
reparse point, cleanup deliberately refuses to follow or delete the suspicious
entry; an ignored `.rrv-faithful-*` forensic directory may remain. Verify that
it is the expected project-root child before removing it manually. It is never
an accepted or published result.

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
