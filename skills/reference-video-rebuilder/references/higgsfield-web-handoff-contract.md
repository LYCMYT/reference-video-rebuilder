# Higgsfield web handoff contract (v0.10.1-alpha)

## Scope and claim boundary

v0.10.1 adds a no-API-key, user-operated browser handoff above the unchanged
v0.10 local temporal review chain. The local CLI does not open or control a
browser, authenticate, upload, submit, poll, download, retry, or call a provider
API. It prepares exact local files, records a capped confirmation immediately
before a manual browser action, and normalizes one manually downloaded video.

The fixed surface is Higgsfield Motion Control at
`https://higgsfield.ai/ai/video/motion`, model declaration
`kling-3.0-motion-control`, and `720p`. These strings are local routing
constraints, not provider proof. Every plan, receipt, and normalized result
keeps `provider_provenance: unattested-user-operated-web` and
`browser_submission_attested: false`.

The bridge supports only `pose-transfer|video-to-video` with
`mute|preserve-reference`. It does not support or claim voice cloning, lip sync,
rebuilt SFX, provider receipts, automatic semantic action approval, or automatic
retry.

## Exact-byte cloud reauthorization

The frozen Asset Manifest 0.2 and v0.10 Temporal Plan remain `local-only`; their
`cloud_upload_allowed: false` fields are not rewritten. A private v0.10.1
Handoff Request supplies two new, expiring, exact-byte authorizations scoped to
one Higgsfield Motion Control upload, one output ID, and one purpose:

- the selected frozen character-image slot and its current SHA-256;
- the approved action-reference MP4 and its current SHA-256.

Both records require rights and cloud-upload confirmation. Their expiry must be
current at preparation and again immediately before the browser action. This
narrow reauthorization supersedes the Manifest local-only restriction for only
those two derived upload files and only that one user-operated upload. It does
not authorize other slots, audio, packets, prompts, providers, models, outputs,
retention purposes, retries, or future actions.

The raw prompt remains only in the private Request. The Handoff Plan binds the
complete private Request SHA-256; it stores neither prompt text nor a separate,
guessable prompt digest, provider job IDs, URLs, cookies, credentials, account
identifiers, or browser output.

## Local commands and artifacts

Resolve `<skill-root>` to the installed Skill directory. All packet arguments
are normalized project-root-relative paths when the guarded core reads them.

```text
python <skill-root>/scripts/video_remix.py validate-higgsfield-web-handoff-request <request.json> --json

python <skill-root>/scripts/video_remix.py prepare-higgsfield-web-handoff <temporal-plan.json> <approved-plan-review.json> <handoff-request.json> --project-root <project-dir> --reference-pack <direct-child> --web-handoff-rights-confirmed [--output-dir higgsfield-web-handoff] [--ffmpeg <path>] [--ffprobe <path>] [--timeout-seconds <seconds>] --json

python <skill-root>/scripts/video_remix.py validate-higgsfield-web-handoff-plan <handoff-plan.json> --json

python <skill-root>/scripts/video_remix.py record-higgsfield-web-action <handoff-plan.json> --project-root <project-dir> --max-credits <cap> --observed-cost-credits <current-ui-cost> --available-credits-before <current-ui-balance> --cloud-upload-confirmed --billable-action-confirmed [--output-dir higgsfield-web-browser-receipt] --json

python <skill-root>/scripts/video_remix.py validate-higgsfield-web-browser-receipt <browser-receipt.json> --json

python <skill-root>/scripts/video_remix.py normalize-higgsfield-download <handoff-plan.json> <browser-receipt.json> --project-root <project-dir> --downloaded-pack <new-direct-child> --reference-pack <approved-direct-child> --downloaded-result-rights-confirmed [--output-result-pack higgsfield-temporal-result] [--ffmpeg <path>] [--ffprobe <path>] [--timeout-seconds <seconds>] --json
```

Preparation publishes exactly:

```text
higgsfield-web-handoff/
├── higgsfield-web-handoff-plan.json
└── upload/
    ├── character.png
    └── motion-reference.mp4
```

`character.png` is orientation-corrected, pixel-reconstructed, and stripped of
metadata. `motion-reference.mp4` contains only the approved H.264 action video;
its audio and inherited metadata are removed. Under `preserve-reference`, the
approved reference audio is grafted back locally during normalization, so it is
not uploaded through this handoff.

The local browser receipt is a pre-submit confirmation card. Its
`projected_remaining_credits_after` is arithmetic, not an observed post-charge
balance. The receipt explicitly does not attest that upload, submission,
charging, provider execution, or download occurred.

The Request-to-action and receipt-to-result transitions are each single-use.
After all live cost/upload checks pass, `record-higgsfield-web-action`
atomically creates an ignored private marker keyed by the complete Handoff
Request hash; copies or separately prepared Plans from that same Request cannot
issue another receipt. Before result work, normalization creates a second
private marker keyed by the exact receipt hash; only one concurrent or later
normalization can proceed. These markers contain hashes and timestamps only,
are never public artifacts, and are terminal even if later local work fails or
the process crashes. Do not delete `.rrv-higgsfield-web-*-use-*` state to retry;
create a fresh Request, Plan, live confirmation, receipt, and result pack.

Normalization accepts one new direct-child downloaded pack containing exactly
one ordinary video. It snapshots and fully decodes the file, strips inherited
metadata, normalizes it to the approved v0.10 output profile and exact timing,
and publishes exactly `temporal-replacement.mp4`. That one-file result pack must
still enter `propose-temporal-results`, full-playback Results Review,
`freeze-temporal-delivery`, and `verify-temporal-delivery`.

## Mandatory action-time browser gate

Immediately before any upload, prompt entry, or billable Generate click, reread
the live page and confirm all of the following together:

1. origin/path, Motion Control surface, model, and `720p` still match the Plan;
2. the only upload files are the exact planned `character.png` and
   `motion-reference.mp4`, and the private prompt is the reviewed Request text;
3. the currently displayed cost and balance are freshly observed;
4. the displayed cost is at most the Request's `max_credits` and no greater than
   the available balance;
5. the user explicitly confirms this exact cloud upload and this one billable
   action at the displayed cost.

If cost, balance, model, surface, resolution, upload set, prompt, or authorization
differs, stop. A previous confirmation at a lower cost is not valid after the
displayed price changes. Do not auto-retry an unknown, failed, or timed-out
submission; a retry requires a fresh Request, Handoff Plan, live confirmation,
new result pack, and new v0.10 proposal/review.

The browser's displayed cost/balance and any local receipt are operational
observations, not provider invoices or provenance. Never place cookies, tokens,
job IDs, URLs, prompts, account details, screenshots, or provider output text in
public CLI JSON.
