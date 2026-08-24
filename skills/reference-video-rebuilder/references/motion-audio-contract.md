# Motion and audio rebuild contract (v0.8)

## Status and purpose

Use this contract to prevent a structural reconstruction from being accepted as
a performance, voice, or lip-sync reconstruction. It defines the v0.8
Template IR 0.3.0 acceptance boundary; it does **not** install or integrate a
motion controller.

The bundled deterministic renderer currently composites static images, 2D
layers, transitions, and a selected audio track. It has no subject-motion,
voice-imitation, or lip-sync engine. Moving, scaling, or cross-fading a static
image is not character action replication.

An external controller such as Runway may be considered in a future,
separately approved adapter integration. It is not installed, connected, or
supported by this repository today. Do not describe it as a current executor,
upload path, or acceptance route.

## Template IR 0.3.0 field

Every Template IR 0.3.0 must contain exactly one `rebuild_requirements` object:

```json
{
  "rebuild_requirements": {
    "motion_required": true,
    "motion_mode": "video-to-video",
    "audio_mode": "preserve-reference",
    "lip_sync_required": false,
    "voice_likeness_rights_confirmed": false
  }
}
```

Use only these values:

| Field | Type / allowed values | Meaning |
| --- | --- | --- |
| `motion_required` | boolean | `true` only when the result must reproduce continuous subject action, including body, hand, face, or performance motion. |
| `motion_mode` | `static`, `layout-only`, `pose-transfer`, `video-to-video` | The minimum execution class needed for the requested subject motion. `layout-only` describes graphic/camera-like motion of still assets, not character action. |
| `audio_mode` | `mute`, `preserve-reference`, `replace-upload`, `rebuild-sfx`, `clone-authorized-voice` | The intended audio treatment. `preserve-reference` copies the approved reference track; it does not imitate a voice. |
| `lip_sync_required` | boolean | `true` only when visible mouth movement must synchronize to the selected audio. |
| `voice_likeness_rights_confirmed` | boolean | `true` only after rights to clone the relevant voice/likeness are explicitly confirmed. |

Apply these schema invariants before any output is accepted:

- `motion_required: false` permits only `motion_mode: static` or
  `layout-only`.
- `motion_required: true` requires `motion_mode: pose-transfer` or
  `video-to-video`; `static` and `layout-only` are invalid.
- `lip_sync_required: true` requires `motion_required: true` and an audio mode
  other than `mute`.
- `audio_mode: clone-authorized-voice` requires
  `voice_likeness_rights_confirmed: true`; a true value under another audio
  mode does not authorize voice cloning.
- An unknown, omitted, contradictory, or unsupported value is a P0 failure.
  Never infer a less demanding mode to make a render pass.

`clone-authorized-voice` additionally requires a separately recorded authorization for
the source speaker/voice and a reviewed controller declaration. Do not treat
rights to a reference video, likeness, music, or an uploaded audio file as
permission to clone a voice.

## Capability and acceptance matrix

| Requested requirement | Current deterministic renderer | Required verdict until a controller is integrated |
| --- | --- | --- |
| `motion_required: false`, `motion_mode: static` or `layout-only`, `lip_sync_required: false` | May render the structural timeline subject to the ordinary asset and QA gates. | Accept only the claims actually reviewed. |
| `motion_required: true`, `motion_mode: pose-transfer` | No pose/performance transfer capability. | Fail closed. |
| `motion_required: true`, `motion_mode: video-to-video` | No temporal video-to-video generation capability. | Fail closed. |
| `lip_sync_required: true` | No audio-driven mouth/face animation capability. | Fail closed. |
| `audio_mode: preserve-reference` | May retain an approved audio track where the existing asset contract permits it. | Call it audio preservation, never voice imitation. |
| `audio_mode: replace-upload` | May use an approved user-uploaded track where the existing asset contract permits it. | Call it replacement audio, never voice imitation. |
| `audio_mode: rebuild-sfx` | No audio-rebuild/SFX synthesis capability. | Fail closed. |
| `audio_mode: clone-authorized-voice` | No voice-cloning capability. | Fail closed. |

Template IR 0.2.0 has no `rebuild_requirements` object. Treat every legacy
0.2 render as `structure_only_unclaimed`: it may show timing, 2D composition,
static-look replacement, effects, and selected audio, but it must not claim
subject motion replication, voice imitation, or lip sync. Changing only the
schema-version string does not upgrade a legacy template.

## Required evidence and QA

For a requested pose-transfer, video-to-video, `clone-authorized-voice`, or
lip-sync result,
require all of the following before final acceptance:

- a reviewed Template IR 0.3.0 with the exact requirements above;
- an approved controller declaration naming the actual controller/model and
  version, upload scope, and rights boundary;
- source, likeness, audio, and (when applicable) voice authorization records;
- a full-playback visual review covering face, hands, limbs, garment continuity,
  temporal artifacts, and the requested performance motion;
- audio review that distinguishes preservation, replacement, voice cloning,
  and mouth/audio synchronization; and
- a P0 failure if the rendered mechanism or evidence cannot satisfy the
  declared requirement.

Do not replace these checks with contact sheets, audio-stream presence, static
frame similarity, pan/zoom keyframes, or a provider's generic marketing claim.

## Portal-reveal benchmark classification

For the current portal-reveal reference, freeze the requested result as:

```json
{
  "motion_required": true,
  "motion_mode": "video-to-video",
  "audio_mode": "preserve-reference",
  "lip_sync_required": false,
  "voice_likeness_rights_confirmed": false
}
```

The existing portal reconstruction may be kept only as a
`structure_only_unclaimed` benchmark: its timing, portal layers, static
replacement appearance, and preserved track can be reviewed, but its still
images cannot satisfy the required subject action. It must fail the v0.8 final
motion gate until a reviewed video-to-video controller produces and passes a
new result.
