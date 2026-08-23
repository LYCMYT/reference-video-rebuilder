# Replacement asset contract

## Scope and versions

Asset Manifest schema is the structural source of truth. Manifest 0.1.0 is a
legacy compatibility format. The strict v0.5.0 asset-pack workflow publishes
only Asset Manifest 0.2.0.

Manifest 0.2.0 is local-only. Every asset must use a project-root-relative
local path, carry its SHA-256, have rights_confirmed true, and have
cloud_upload_allowed false. provider_asset_id is forbidden. The frozen
workflow publishes only opaque flat copies under frozen-assets; it never
renders directly from the mutable source pack.

Manifest 0.2.0 is a locally asserted byte contract. Its hashes detect asset
drift; they do not authenticate an approver or prove that freeze-assets created
the file. The governed workflow therefore retains Proposal, Review, and
asset-freeze-report.json as one local audit packet. A writer with project
access can author or replace all of those files. Enforce independent approval
with a trusted signature or access-controlled immutable store, not bare JSON
booleans and SHA-256 values.

The schema intentionally has no quality constraints property. Asset items may
contain slot_id, one path or provider_asset_id in legacy mode, media_type,
sha256, rights_confirmed, cloud_upload_allowed, processor, and optional notes.
Additional fields are rejected. Put visual-quality expectations in Template IR
and human QA, not in the manifest.

Validate-assets is a declaration preflight: it checks the manifest against the
Template IR, project containment, file availability, and applicable hashes. It
does not sniff media bytes, and renderer 0.2 remains authoritative for its
stricter snapshot/link boundary. Do not treat a declared media_type as proof
of actual file content. The strict propose-assets scanner is the media
inspection boundary; freeze-assets safely scans the pack again before copying.

## Strict local asset-pack workflow

Start from a validated Template IR whose path is normalized relative to the
project root:

~~~text
propose-assets <template> --project-root <root> --asset-pack <direct-child> --output-dir <direct-child> --asset-pack-rights-confirmed --ffprobe <ffprobe> --timeout <seconds> --json
~~~

The rights flag is required before the workflow reads the project or pack.
asset-pack and output-dir name direct project-root children only. Template,
Proposal, and Review paths must be normalized project-root-relative paths.
Reject absolute, drive-rooted, UNC, nested, dot-segment, and unsafe paths as
applicable before scanning or publishing.

The pack contains direct regular files only:

- static JPEG, PNG, or WebP images;
- WAV, MP3, M4A, or MKA audio accepted only after local ffprobe inspection
  through pipe:0.

The entire proposal fails closed for unknown files, sidecars, videos,
animations, directories, links, reparse points, unreadable files, or unsafe
paths. No OCR, EXIF/tag interpretation, visual recognition, fuzzy naming, or
semantic inference is allowed.

Candidate selection is mechanical: a file is a candidate only when its exact
filename stem equals the Template slot_id and its inspected media type appears
in that slot's accepted_media. A zero, multiple, or incompatible match remains
missing, ambiguous, or incompatible; do not repair it with a guess.

Propose-assets writes these local review artifacts:

- asset-pack-proposal.json;
- asset-review-decision.template.json;
- asset-contact-sheet.png.

The contact sheet and JSON packet are review evidence, not a GUI and not an
approval. Proposal schema 0.5.0 fixes privacy_profile to local-only and
review_required to true. It binds the Template snapshot and complete scanned
inventory with SHA-256 values.

## Human review and freeze

Review every Template slot. An approved review must bind the exact Proposal
SHA-256, confirm that the contact sheet was reviewed and processing is
local-only, and contain one decision per slot.

For each use decision, explicitly confirm all four facts:

1. the intended content;
2. media compatibility with the slot;
3. render readiness for the slot representation;
4. rights for that specific asset.

An optional slot may be omitted only with an explicit omission confirmation.
Required, missing, ambiguous, incompatible, and unresolved slots cannot pass
freeze. The processor is an explicit safe-slug review choice, not an inferred
semantic classification.

Validate both packets before freezing:

~~~text
validate-asset-proposal <proposal.json> --json
validate-asset-review <review.json> --json
freeze-assets <project-relative-proposal> <project-relative-review> --project-root <root> --output-dir frozen-assets --ffprobe <ffprobe> --timeout <seconds> --json
~~~

Freeze-assets verifies the Proposal, Review, Template, and inventory bindings,
safely rescans the asset pack, and refuses inventory drift. It atomically
publishes frozen-assets/assets.json as Asset Manifest 0.2.0, flat opaque asset
copies with SHA-256 values, and asset-freeze-report.json. On validation,
rescan, rights, path, or copy failure, publish no partial frozen-assets
directory.

## Rendering boundary

Renderer 0.2.0 consumes frozen Asset Manifest 0.2.0 assets from verified byte
snapshots. Images decode from that snapshot and audio reaches FFmpeg only as
the verified stdin input pipe:0. Renderer 0.1 legacy path-based Asset Manifest
0.1.0 behavior remains compatibility-only; it is not the security claim of
the v0.5 workflow.

Windows is the audited strong boundary for asset-pack scan, rescan, and
frozen-assets publication: reparse-point checks and guarded snapshot/copy
behavior are part of that contract. Other systems remain observable and fail
closed where supported, but do not claim an equivalent Windows NT no-delete
guarantee. Rendering binds consumed asset bytes to their manifest hashes, but
its frame/output path containment assumes no hostile concurrent filesystem
mutation. Do not treat this Alpha as a sandbox for an untrusted local writer.

## Input guidance

Use a filename that exactly matches the intended slot. For example, if a
Template IR contains model.identity, outfit.01 through outfit.12, product.01
through product.12, background, and audio slots, use matching stems such as
model.identity.png, outfit.01.png, product.12.png, background.png, and
audio.mka. Names alone do not establish identity, garment, product, or rights.

Use original approved product artwork when labels and logos require exact
placement. Record unknown visual details as unknown during human review rather
than claiming the workflow verified them.

## Failure conditions

Fail before rendering when:

- the required rights flag or a required human confirmation is absent;
- a path or output target violates the project-root/direct-child policy;
- the pack contains unsupported, animated, video, sidecar, linked, or unsafe
  content;
- exact-name candidate selection is missing, ambiguous, or media-incompatible;
- Proposal, Review, Template, or rescanned inventory hashes do not bind;
- a required slot is omitted, unresolved, or mapped more than once;
- a frozen manifest is not 0.2.0 local-only with hashes for every copied
  asset;
- a declared manifest mapping does not match the Template slot contract.

Never use this workflow for cloud upload, generation, arbitrary video input,
or automatic approval.
