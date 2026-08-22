# Template IR renderer contract

Template IR 0.2.0 is the minimum executable renderer contract. The bundled
`0.2.0-alpha` runtime implements a deterministic S1 subset plus technical video
QA. Semantic source analysis and asset generation remain agent-assisted, and
the schema intentionally describes some features that the current renderer
will reject rather than approximate.

## Alpha renderer subset

The bundled renderer accepts reviewed S1 templates with local JPEG, PNG, or
WebP render-ready layers; `contain`, `cover`, or `stretch` layout; hold, linear,
or cubic-bezier transforms; normal blending; rect/polygon masks; and a finite
horizontal non-repeating carousel. It emits H.264/yuv420p at 720x1280 and/or
1080x1920 and can mux the supported local audio types.

The broader schema also preserves forward-compatible authoring intent. A
schema-valid feature outside the subset (for example HEVC, 10-bit output,
rounded/alpha masks, non-normal blend modes, or a non-horizontal/repeating
carousel) must fail during zero-write render preflight. It must never be
silently approximated or discovered only after master frames are written.

## Validation boundary

`assets/schemas/template-ir.schema.json` is Draft 2020-12 and owns object
shape, required fields, enums, and rejection of unknown properties.
`scripts/video_remix.py` owns cross-field semantics. It requires `jsonschema`
for complete validation and reports a clear dependency error when that package
is absent. JSON input rejects `NaN`, `Infinity`, and `-Infinity`; every number
must be finite.

## Top-level contract

Required fields are `schema_version`, `template_id`, `coordinate_space`,
`canvas`, `source`, `support`, `tracks`, `slots`, `layers`, `remove_layers`,
`events`, `audio`, and `outputs`. The current schema version is exactly
`0.2.0`; this contract supports only major version `0`.

`coordinate_space` is always `canvas-pixels`. `canvas` explicitly supplies
`width`, `height`, a fallback `background` color, and a source-coordinate
`source_rect`. `source` supplies positive source geometry, duration, FPS, and
a 64-character lowercase SHA-256. The example's all-zero hash is intentionally
a placeholder and must be replaced before freezing.

There is no implicit `source-fit` behavior: `canvas.source_rect` and the
canvas must have the same aspect ratio (within floating-point tolerance). A
template that violates this is rejected rather than leaving a renderer to
invent stretch, crop, or letterbox behavior.

All frame ranges are half-open `[start_frame, end_frame)`, bounded by source
duration, strictly ascending within their array, and non-overlapping.

## Slots, tracks, and layers

Slots are only user-input or generated-asset declarations: `id`, semantic
`type`, `required`, and accepted media. They contain no placement or timeline
truth. `accepted_media` is limited to the shared supported image, video, and
audio MIME-type enum used by the asset manifest. A layer is the actual renderer instance and always supplies `id`,
`track_id`, `source`, `active_ranges`, `layout`, `transform`, nullable `mask`,
`blend`, and `z_offset`.

`source` is `{slot_id, representation}` where representation is `raw` or
`render-ready`. A flat garment asset must never be directly rendered as a
person: garment layers require `render-ready`. An identity slot may be input to
generation without becoming its own raw visual layer.

Each track provides `id`, `type`, `z_index`, and `overlap_policy` (`forbid` or
`allow`). Renderers sort layers deterministically by
`(track.z_index, layer.z_offset, layer.id)`. A `forbid` track cannot contain
overlapping layer ranges.

`layout` is `{box, fit, object_position}`; fit is `contain`, `cover`, or
`stretch`. `blend` is `{mode, opacity}` with opacity in `[0, 1]`; modes are
`normal`, `multiply`, `screen`, `overlay`, `darken`, and `lighten`.

Transforms have a canvas-pixel `anchor` and complete keyframes. Every
keyframe supplies `frame`, `translate_x`, `translate_y`, `scale_x`, `scale_y`,
`rotation_deg`, `opacity`, and an easing object. Easing is `{type: hold}`,
`{type: linear}`, or `{type: cubic-bezier, control_points: [x1, y1, x2, y2]}`.
Keyframes are strictly increasing, in duration, and begin no later than the
layer's first active range.

Carousel tracks may add `group_layout`, `group_transform`, and `clip_mask`.
`group_layout.item_slots` fixes item order and base geometry; the group
transform moves all of those instances. The validator checks that each item
slot maps to one matching layer with the declared origin, size, and gap.

Masks are null or one of `rect`, `rounded-rect`, `polygon`, or `alpha-asset`.
Every mask states `space` (`canvas` or `layer`), `feather_px`, and `invert`.
Alpha-asset masks refer to a declared slot and use `alpha` or `luma` mode.

If an optional slot has no mapped asset, its associated layer is skipped. The
canvas background remains the deterministic fallback; a renderer must not
invent a replacement asset for an unmapped optional slot.

## Remove geometry

`remove_layers` record source-coordinate regions rather than creative content.
Every region has an active range, `operation`, and static source geometry
(`rect`, `rounded-rect`, or `polygon`) that must stay within source bounds.
Regions of one remove layer are a spatial union, so their time ranges may
overlap; each range is independently bounded and non-empty.

- There may be zero or one `crop-source-before-analysis` layer. When present,
  it has exactly one full-duration `keep` rect, and that rect exactly equals
  `canvas.source_rect`.
- `mask-and-rebuild` regions use `operation: remove`.
- `exclude-from-reconstruction` regions use `operation: remove`.

## Events and audio

Events are edit and QA annotations; layers remain the rendering truth. Each
event is exactly `{id, frame, type: "slot-switch", track_id, slot_id,
transition: {type: "cut", duration_frames: 0}}`. All references must exist,
events must be strictly ordered by `(frame, id)`, and one track cannot have two
slot switches at the same frame. Every switch maps to exactly one layer on the
same track whose source slot matches and whose active range starts on the event
frame. Conversely, every render-ready garment layer on every `subject` track
must have a matching switch for each active-range start.

`audio` is independent of visual tracks. It contains `slot_id`, timeline start
and end frames, source in/out milliseconds, playback rate, loop flag, gain,
and fade lengths. Its timeline and fades must fit duration. For non-looping
audio, source coverage after playback-rate adjustment must be within one video
frame of the timeline duration.

## Outputs

Each output has a unique `id`, even `width` and `height`, `codec` (`h264` or
`hevc`), `pixel_format` (`yuv420p` or `yuv420p10le`), `audio_codec` (`aac` or
`opus`), `filename`, and explicit `reframe`. Reframe specifies `contain`,
`cover`, or `stretch`, object position, and fallback background. Matching
aspect ratios may still explicitly use `contain` or `stretch`.
