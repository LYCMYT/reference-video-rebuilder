# Replacement asset contract

## Common fields

`assets/schemas/asset-manifest.schema.json` is the Draft 2020-12 structural
source of truth for a manifest. The CLI validates it before applying
template-dependent path, slot, and media checks; unknown top-level fields and
unknown fields on an asset item are rejected.

Require each asset mapping to contain:

- `slot_id`;
- exactly one of `path` or approved `provider_asset_id`;
- `media_type`;
- `sha256` when already ingested locally; otherwise compute it during ingestion;
- `rights_confirmed`;
- `cloud_upload_allowed`;
- `processor` or `auto`;
- optional `notes` and quality constraints.

Never store credentials in an asset manifest. Resolve every local path against the declared project root and reject absolute or relative paths that escape that root.
Each mapped asset's `media_type` must also appear in the target slot's
`accepted_media` list; being globally supported is not sufficient.
`provider_asset_id` represents a cloud asset and therefore requires
`cloud_upload_allowed: true`; `local-only` manifests still forbid provider IDs.

## Input routing

### Model identity

Prefer several clean identity references with consistent age, hairstyle, makeup, and lighting. Record which image is authoritative. Do not infer permission from file possession.

### Garments

Classify the input before processing:

- current model already wearing the garment;
- another person wearing it;
- mannequin;
- flat lay;
- product-only image;
- screenshot;
- text description.

Record observable requirements: color, silhouette, neckline, sleeve, length, print, texture, buttons, pockets, logo, and accessories. Mark unverifiable hidden details as unknown.

### Products and props

Prefer original product images for deterministic placement. Preserve text and logos with direct compositing when accuracy matters. Do not let a generative model freely rewrite product labels.

### Backgrounds

Record fit policy, safe areas, horizon, lighting direction, depth assumptions, and whether generation or deterministic placement is allowed.

### Text and logos

Require exact user-approved strings and source artwork. Detect accidental reuse of source-platform account text.

### Audio

Record whether to preserve, replace, loop, trim, duck, normalize, or mute. Validate rights and duration. Avoid discontinuous cuts through a continuous musical phrase unless approved.

When preserving a source stream locally, extract only the selected audio stream with
FFmpeg stream copy into `audio-original.mka`. Declare this artifact as
`media_type: audio/x-matroska` and `container: matroska`; it is a generic
lossless-container wrapper and does not imply a particular audio codec. Strip
container metadata and chapters during extraction (`-map_metadata -1` and
`-map_chapters -1`) so source titles, artists, comments, and account identifiers
do not enter the reusable project.

## Validation failures

Fail before generation when:

- a required slot is missing;
- a slot is mapped more than once without an explicit sequence;
- the media type is unsupported;
- the media type is not accepted by the referenced slot;
- a path escapes the project allowlist;
- the asset is unreadable or too small for the requested output;
- rights are not confirmed;
- cloud upload would violate the asset policy.
- a provider asset is declared without cloud upload permission.
