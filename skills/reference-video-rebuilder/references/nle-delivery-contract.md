# NLE derivative delivery contract (v0.9.1)

## Purpose and claim boundary

Use this path only to create a flat MP4 derivative intended for practical
import into Jianying or another common NLE. It is deliberately separate from a
faithful archive. Export re-encodes video and, when present, audio, so every
report must state `completion: nle_compatible_derivative` and
`bitstream_faithful: false`.

The profile is a technical compatibility baseline, not Jianying certification.
It does not create a proprietary Jianying project, draft, timeline, caption,
effect, transition, or editable layer package. A successful verifier result
does not prove behavior in every app release, device, account, or hardware
decoder; perform a real import test when that application-level guarantee is
required.

## Fixed profile

The only supported profile is `jianying-compatible-v1`:

- MP4 container with `+faststart`;
- H.264 High profile, 8-bit `yuv420p`;
- CFR at 24, 25, 30, 50, or 60 fps;
- one of `720x1280`, `1080x1920`, `1280x720`, or `1920x1080`;
- AAC-LC, 48 kHz, stereo when the input contains audio;
- no audio stream when the input is silent;
- inherited/user-authored metadata, chapters, and rotation cleared; and
- full local video/audio decode verification before acceptance.

The source must be an authorized, project-contained MP4 with exactly one video
stream, at most one audio stream, zero rotation, no subtitle/data/attachment
or other side streams, exact CFR at 24, 25, 30, 50, or 60 fps, at most 60
seconds, and one of the four dimensions above. The source, delivery, and output
directory must not be links, reparse points, hard links, unsafe paths, or
existing output targets. Both public operations require an explicit
`--rights-confirmed` flag before touching the project root or input.

## Local CLI

```text
python <skill-root>/scripts/video_remix.py jianying-export <source.mp4> --project-root <project-dir> --rights-confirmed [--output-dir jianying-delivery] [--profile jianying-compatible-v1] [--ffmpeg <path>] [--ffprobe <path>] [--timeout-seconds <seconds>] --json
python <skill-root>/scripts/video_remix.py jianying-verify <delivery.mp4> --project-root <project-dir> --rights-confirmed [--profile jianying-compatible-v1] [--ffmpeg <path>] [--ffprobe <path>] [--timeout-seconds <seconds>] --json
```

Resolve `<skill-root>` to the absolute installed Skill directory; it is a
placeholder and does not depend on the shell's current working directory.

Export atomically publishes `jianying-compatible-v1.mp4` and
`nle-delivery-report.json` in a new direct-child output directory. Verify is
read-only and validates the existing delivery against the same profile.
Normal failures publish no target. On detected hostile stage mutation, cleanup
may conservatively leave an ignored `.rrv-nle-*` forensic directory instead of
following or deleting a suspicious hard link/reparse point; inspect its exact
project-root location before manual removal.

## Acceptance

Accept the technical derivative only when export and independent verify both
pass, the entire video and selected audio decode, declared frame count and CFR
match the profile, rotation is absent, and the report is retained. Then perform
visual/audio playback review. If Jianying-specific acceptance is required,
also import that exact file into the exact target Jianying version and record
the application version and result separately.

Never call this output a faithful archive, original bitstream, lossless copy,
editable project, official preset, or guaranteed Jianying import. Retain the
faithful archive and its `rebuild-summary.json` as the audit source whenever
one exists.
