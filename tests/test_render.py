import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PIL import Image
except ImportError:  # The production dependency is declared in requirements-runtime.txt.
    Image = None


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "reference-video-rebuilder" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rrv_render
import video_remix


def keyframe(frame=0, *, x=0, y=0, scale_x=1, scale_y=1, rotation=0, opacity=1, easing="hold"):
    if isinstance(easing, str):
        easing = {"type": easing}
    return {
        "frame": frame,
        "translate_x": x,
        "translate_y": y,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "rotation_deg": rotation,
        "opacity": opacity,
        "easing": easing,
    }


def layer(
    layer_id,
    track_id,
    slot_id,
    *,
    box=(0, 0, 8, 4),
    fit="stretch",
    ranges=None,
    transform=None,
    z_offset=0,
    blend="normal",
    mask=None,
):
    return {
        "id": layer_id,
        "track_id": track_id,
        "source": {"slot_id": slot_id, "representation": "raw"},
        "active_ranges": ranges or [{"start_frame": 0, "end_frame": 3}],
        "layout": {
            "box": {"x": box[0], "y": box[1], "width": box[2], "height": box[3]},
            "fit": fit,
            "object_position": {"x": 0.5, "y": 0.5},
        },
        "transform": transform
        or {"anchor": {"x": 0, "y": 0}, "keyframes": [keyframe()]},
        "mask": mask,
        "blend": {"mode": blend, "opacity": 1},
        "z_offset": z_offset,
    }


def template(slots, tracks, layers, *, duration=3, width=8, height=4, outputs=None):
    slots = list(slots)
    if not any(slot.get("id") == "audio" for slot in slots):
        slots.append(
            {"id": "audio", "type": "audio", "required": False, "accepted_media": ["audio/wav"]}
        )
    if outputs is None:
        outputs = [
            {
                "id": "vertical-720",
                "width": 720,
                "height": 1280,
                "codec": "h264",
                "pixel_format": "yuv420p",
                "audio_codec": "aac",
                "filename": "deliveries/default.mp4",
                "reframe": {
                    "mode": "contain",
                    "object_position": {"x": 0.5, "y": 0.5},
                    "background": "#ffffff",
                },
            }
        ]
    return {
        "schema_version": "0.2.0",
        "template_id": "render-test",
        "coordinate_space": "canvas-pixels",
        "canvas": {
            "width": width,
            "height": height,
            "background": "#ffffff",
            "source_rect": {"x": 0, "y": 0, "width": width, "height": height},
        },
        "source": {
            "duration_frames": duration,
            "fps": 10,
            "width": width,
            "height": height,
            "source_sha256": "0" * 64,
        },
        "support": {"level": "S1", "confidence": 1, "warnings": []},
        "tracks": tracks,
        "slots": slots,
        "layers": layers,
        "remove_layers": [],
        "events": [],
        "audio": {
            "slot_id": "audio",
            "timeline_start_frame": 0,
            "timeline_end_frame": duration,
            "source_in_ms": 0,
            "source_out_ms": duration * 100,
            "playback_rate": 1,
            "loop": False,
            "gain_db": 0,
            "fade_in_frames": 0,
            "fade_out_frames": 0,
        },
        "outputs": outputs,
    }


@unittest.skipUnless(Image is not None, "Pillow is installed from requirements-runtime.txt")
class RendererTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def image(self, relative, color, size=(2, 2)):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", size, color).save(path)
        return path

    def manifest(self, paths):
        return {
            "schema_version": "0.1.0",
            "template_id": "render-test",
            "privacy_profile": "local-only",
            "assets": [
                {
                    "slot_id": slot_id,
                    "path": path.relative_to(self.root).as_posix(),
                    "media_type": "image/png",
                    "rights_confirmed": True,
                    "cloud_upload_allowed": False,
                    "processor": "direct",
                }
                for slot_id, path in paths.items()
            ],
        }

    def renderer(self, document, manifest):
        self.assertEqual(video_remix.validate_template_data(document), [])
        self.assertEqual(
            video_remix.validate_assets_data(
                document, manifest, self.root / "assets.json", check_files=True, project_root=self.root
            ),
            [],
        )
        assets = rrv_render.resolve_local_assets(document, manifest, self.root)
        return rrv_render.S1Renderer(document, assets), assets

    def master_sequence(self, duration, *, directory="render/master-frames", color="#ffffff"):
        frame_dir = self.root / directory
        frame_dir.mkdir(parents=True)
        for frame in range(duration):
            image = Image.new("RGBA", (8, 4), color)
            try:
                image.save(frame_dir / (rrv_render.FRAME_FILENAME_PATTERN % frame))
            finally:
                image.close()
        return frame_dir

    def assert_zero_write_project_failure(self, document, manifest, case_name, error_type):
        case_root = self.root / "preflight" / case_name
        frame_directory = f"preflight/{case_name}/master"
        document["outputs"][0]["filename"] = f"preflight/{case_name}/result.mp4"
        calls = []
        with self.assertRaises(error_type):
            rrv_render.render_project(
                document,
                manifest,
                self.root,
                frame_directory=frame_directory,
                encoder_runner=lambda arguments: calls.append(arguments),
            )
        self.assertEqual(calls, [])
        self.assertFalse(case_root.exists(), f"preflight failure wrote into {case_root}")

    def test_frame_switch_uses_half_open_integer_ranges(self):
        red = self.image("assets/red.png", "#ff0000")
        blue = self.image("assets/blue.png", "#0000ff")
        slots = [
            {"id": "red", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "blue", "type": "image", "required": True, "accepted_media": ["image/png"]},
        ]
        tracks = [{"id": "main", "type": "prop", "z_index": 0, "overlap_policy": "forbid"}]
        document = template(
            slots,
            tracks,
            [
                layer("red", "main", "red", ranges=[{"start_frame": 0, "end_frame": 1}]),
                layer("blue", "main", "blue", ranges=[{"start_frame": 1, "end_frame": 3}]),
            ],
        )
        renderer, _ = self.renderer(document, self.manifest({"red": red, "blue": blue}))
        self.assertEqual(renderer.render_frame(0).getpixel((4, 2)), (255, 0, 0, 255))
        self.assertEqual(renderer.render_frame(1).getpixel((4, 2)), (0, 0, 255, 255))
        frames = renderer.write_master_frames(self.root, "render/master", debug_bounds=True)
        self.assertEqual([path.name for path in frames], ["frame_000000.png", "frame_000001.png", "frame_000002.png"])
        with Image.open(frames[0]) as debug_frame:
            self.assertNotEqual(debug_frame.convert("RGBA").getpixel((0, 0)), (255, 0, 0, 255))

    def test_track_z_order_wins_over_input_layer_order(self):
        red = self.image("assets/red.png", "#ff0000")
        blue = self.image("assets/blue.png", "#0000ff")
        slots = [
            {"id": "red", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "blue", "type": "image", "required": True, "accepted_media": ["image/png"]},
        ]
        tracks = [
            {"id": "back", "type": "background", "z_index": 0, "overlap_policy": "allow"},
            {"id": "front", "type": "prop", "z_index": 10, "overlap_policy": "allow"},
        ]
        document = template(
            slots,
            tracks,
            [layer("front", "front", "blue"), layer("back", "back", "red")],
        )
        renderer, _ = self.renderer(document, self.manifest({"red": red, "blue": blue}))
        self.assertEqual(renderer.render_frame(0).getpixel((4, 2)), (0, 0, 255, 255))

    def test_contain_cover_and_stretch_follow_layout_fit(self):
        wide = self.image("assets/wide.png", "#ff0000", size=(4, 2))
        slots = [{"id": "image", "type": "image", "required": True, "accepted_media": ["image/png"]}]
        tracks = [{"id": "main", "type": "prop", "z_index": 0, "overlap_policy": "allow"}]

        contain = template(slots, tracks, [layer("image", "main", "image", box=(0, 0, 4, 4), fit="contain")], width=4, height=4)
        renderer, _ = self.renderer(contain, self.manifest({"image": wide}))
        contained = renderer.render_frame(0)
        self.assertEqual(contained.getpixel((0, 0)), (255, 255, 255, 255))
        self.assertEqual(contained.getpixel((0, 1)), (255, 0, 0, 255))

        cover = copy.deepcopy(contain)
        cover["layers"][0]["layout"]["fit"] = "cover"
        renderer, _ = self.renderer(cover, self.manifest({"image": wide}))
        self.assertEqual(renderer.render_frame(0).getpixel((0, 0)), (255, 0, 0, 255))

        two_color = self.root / "assets/two-color.png"
        image = Image.new("RGBA", (2, 1), "#ff0000")
        image.putpixel((1, 0), (0, 0, 255, 255))
        image.save(two_color)
        stretch = copy.deepcopy(contain)
        stretch["layers"][0]["layout"]["fit"] = "stretch"
        renderer, _ = self.renderer(stretch, self.manifest({"image": two_color}))
        stretched = renderer.render_frame(0)
        self.assertEqual(stretched.getpixel((0, 2)), (255, 0, 0, 255))
        self.assertEqual(stretched.getpixel((3, 2)), (0, 0, 255, 255))

    def test_keyframes_cover_hold_linear_and_cubic_bezier(self):
        linear = {
            "anchor": {"x": 0, "y": 0},
            "keyframes": [keyframe(0, x=0, opacity=0, easing="linear"), keyframe(10, x=10, opacity=1)],
        }
        state = rrv_render.evaluate_transform(linear, 5)
        self.assertEqual(state.translate_x, 5)
        self.assertEqual(state.opacity, 0.5)

        hold = copy.deepcopy(linear)
        hold["keyframes"][0]["easing"] = {"type": "hold"}
        self.assertEqual(rrv_render.evaluate_transform(hold, 5).translate_x, 0)

        cubic = copy.deepcopy(linear)
        cubic["keyframes"][0]["easing"] = {
            "type": "cubic-bezier",
            "control_points": [0.25, 0.1, 0.25, 1],
        }
        self.assertGreater(rrv_render.evaluate_transform(cubic, 5).translate_x, 5)

    def test_carousel_group_transform_and_canvas_clip(self):
        red = self.image("assets/red.png", "#ff0000")
        blue = self.image("assets/blue.png", "#0000ff")
        slots = [
            {"id": "red", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "blue", "type": "image", "required": True, "accepted_media": ["image/png"]},
        ]
        tracks = [
            {
                "id": "carousel",
                "type": "carousel",
                "z_index": 0,
                "overlap_policy": "allow",
                "group_layout": {
                    "type": "carousel",
                    "origin": {"x": 0, "y": 0},
                    "item_slots": ["red", "blue"],
                    "item_width": 2,
                    "item_height": 2,
                    "gap": 2,
                    "direction": "horizontal",
                    "repeat": "none",
                },
                "group_transform": {
                    "anchor": {"x": 0, "y": 0},
                    "keyframes": [keyframe(0, x=0, easing="linear"), keyframe(1, x=-2)],
                },
                "clip_mask": {
                    "type": "rect",
                    "space": "canvas",
                    "rect": {"x": 0, "y": 0, "width": 4, "height": 3},
                    "feather_px": 0,
                    "invert": False,
                },
            }
        ]
        document = template(
            slots,
            tracks,
            [
                layer("red", "carousel", "red", box=(0, 0, 2, 2)),
                layer("blue", "carousel", "blue", box=(4, 0, 2, 2), z_offset=1),
            ],
            width=8,
            height=3,
        )
        renderer, _ = self.renderer(document, self.manifest({"red": red, "blue": blue}))
        self.assertEqual(renderer.render_frame(0).getpixel((4, 1)), (255, 255, 255, 255))
        self.assertEqual(renderer.render_frame(1).getpixel((2, 1)), (0, 0, 255, 255))

    def test_carousel_work_buffer_brings_off_canvas_item_into_view(self):
        red = self.image("assets/red.png", "#ff0000")
        blue = self.image("assets/blue.png", "#0000ff")
        slots = [
            {"id": "red", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "blue", "type": "image", "required": True, "accepted_media": ["image/png"]},
        ]
        tracks = [
            {
                "id": "carousel",
                "type": "carousel",
                "z_index": 0,
                "overlap_policy": "allow",
                "group_layout": {
                    "type": "carousel",
                    "origin": {"x": 0, "y": 0},
                    "item_slots": ["red", "blue"],
                    "item_width": 2,
                    "item_height": 2,
                    "gap": 4,
                    "direction": "horizontal",
                    "repeat": "none",
                },
                "group_transform": {
                    "anchor": {"x": 0, "y": 0},
                    "keyframes": [keyframe(0, x=0, easing="linear"), keyframe(1, x=-4)],
                },
                "clip_mask": {
                    "type": "rect",
                    "space": "canvas",
                    "rect": {"x": 0, "y": 0, "width": 4, "height": 2},
                    "feather_px": 0,
                    "invert": False,
                },
            }
        ]
        document = template(
            slots,
            tracks,
            [
                layer("red", "carousel", "red", box=(0, 0, 2, 2)),
                layer("blue", "carousel", "blue", box=(6, 0, 2, 2), z_offset=1),
            ],
            width=4,
            height=2,
        )
        renderer, _ = self.renderer(document, self.manifest({"red": red, "blue": blue}))
        self.assertEqual(renderer.render_frame(0).getpixel((2, 1)), (255, 255, 255, 255))
        self.assertEqual(renderer.render_frame(1).getpixel((2, 1)), (0, 0, 255, 255))

    def test_carousel_unsupported_repeat_canvas_mask_and_group_motion_fail_closed(self):
        red = self.image("assets/red.png", "#ff0000")
        blue = self.image("assets/blue.png", "#0000ff")
        slots = [
            {"id": "red", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "blue", "type": "image", "required": True, "accepted_media": ["image/png"]},
        ]
        tracks = [
            {
                "id": "carousel",
                "type": "carousel",
                "z_index": 0,
                "overlap_policy": "allow",
                "group_layout": {
                    "type": "carousel",
                    "origin": {"x": 0, "y": 0},
                    "item_slots": ["red", "blue"],
                    "item_width": 2,
                    "item_height": 2,
                    "gap": 0,
                    "direction": "horizontal",
                    "repeat": "none",
                },
                "group_transform": {
                    "anchor": {"x": 0, "y": 0},
                    "keyframes": [keyframe(0, x=0, easing="linear"), keyframe(1, x=-1)],
                },
                "clip_mask": {
                    "type": "rect",
                    "space": "canvas",
                    "rect": {"x": 0, "y": 0, "width": 4, "height": 2},
                    "feather_px": 0,
                    "invert": False,
                },
            }
        ]
        base = template(
            slots,
            tracks,
            [
                layer("red", "carousel", "red", box=(0, 0, 2, 2)),
                layer("blue", "carousel", "blue", box=(2, 0, 2, 2), z_offset=1),
            ],
            width=4,
            height=2,
        )
        cases = {
            "repeat": lambda document: document["tracks"][0]["group_layout"].__setitem__("repeat", "loop"),
            "canvas-mask": lambda document: document["layers"][0].__setitem__(
                "mask",
                {
                    "type": "rect",
                    "space": "canvas",
                    "rect": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "feather_px": 0,
                    "invert": False,
                },
            ),
            "vertical-motion": lambda document: document["tracks"][0]["group_transform"]["keyframes"][1].__setitem__(
                "translate_y", 1
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                document = copy.deepcopy(base)
                mutate(document)
                assets = rrv_render.resolve_local_assets(document, self.manifest({"red": red, "blue": blue}), self.root)
                with self.assertRaises(rrv_render.UnsupportedFeatureError):
                    rrv_render.S1Renderer(document, assets)

    def test_carousel_z_interleaving_is_rejected_as_an_atomic_composite(self):
        red = self.image("assets/red.png", "#ff0000")
        green = self.image("assets/green.png", "#00ff00")
        blue = self.image("assets/blue.png", "#0000ff")
        slots = [
            {"id": "red", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "green", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "blue", "type": "image", "required": True, "accepted_media": ["image/png"]},
        ]
        tracks = [
            {
                "id": "carousel",
                "type": "carousel",
                "z_index": 0,
                "overlap_policy": "allow",
                "group_layout": {
                    "type": "carousel",
                    "origin": {"x": 0, "y": 0},
                    "item_slots": ["red", "blue"],
                    "item_width": 2,
                    "item_height": 2,
                    "gap": 0,
                    "direction": "horizontal",
                    "repeat": "none",
                },
                "group_transform": {"anchor": {"x": 0, "y": 0}, "keyframes": [keyframe()]},
                "clip_mask": {
                    "type": "rect",
                    "space": "canvas",
                    "rect": {"x": 0, "y": 0, "width": 4, "height": 2},
                    "feather_px": 0,
                    "invert": False,
                },
            },
            {"id": "middle", "type": "prop", "z_index": 0, "overlap_policy": "allow"},
        ]
        document = template(
            slots,
            tracks,
            [
                layer("red", "carousel", "red", box=(0, 0, 2, 2), z_offset=0),
                layer("green", "middle", "green", box=(0, 0, 4, 2), z_offset=1),
                layer("blue", "carousel", "blue", box=(2, 0, 2, 2), z_offset=2),
            ],
            width=4,
            height=2,
        )
        assets = rrv_render.resolve_local_assets(
            document, self.manifest({"red": red, "green": green, "blue": blue}), self.root
        )
        with self.assertRaises(rrv_render.UnsupportedFeatureError):
            rrv_render.S1Renderer(document, assets)

    def test_absent_optional_slot_is_skipped_and_debug_bounds_are_drawn(self):
        slots = [{"id": "optional", "type": "image", "required": False, "accepted_media": ["image/png"]}]
        tracks = [{"id": "main", "type": "prop", "z_index": 0, "overlap_policy": "allow"}]
        document = template(slots, tracks, [layer("optional", "main", "optional", box=(1, 1, 2, 2))])
        renderer, _ = self.renderer(document, self.manifest({}))
        self.assertEqual(renderer.render_frame(0).getpixel((4, 2)), (255, 255, 255, 255))
        # No bounds are emitted for skipped layers, preserving the clean fallback.
        self.assertEqual(renderer.render_frame(0, debug_bounds=True).getpixel((4, 2)), (255, 255, 255, 255))

    def test_rect_and_polygon_layer_masks_are_applied(self):
        image = self.image("assets/image.png", "#ff0000", size=(4, 4))
        slots = [{"id": "image", "type": "image", "required": True, "accepted_media": ["image/png"]}]
        tracks = [{"id": "main", "type": "prop", "z_index": 0, "overlap_policy": "allow"}]
        rect_mask = {
            "type": "rect",
            "space": "layer",
            "rect": {"x": 1, "y": 1, "width": 2, "height": 2},
            "feather_px": 0,
            "invert": False,
        }
        document = template(
            slots,
            tracks,
            [layer("image", "main", "image", box=(0, 0, 4, 4), mask=rect_mask)],
            width=4,
            height=4,
        )
        renderer, _ = self.renderer(document, self.manifest({"image": image}))
        masked = renderer.render_frame(0)
        self.assertEqual(masked.getpixel((0, 0)), (255, 255, 255, 255))
        self.assertEqual(masked.getpixel((1, 1)), (255, 0, 0, 255))

        polygon = copy.deepcopy(document)
        polygon["layers"][0]["mask"] = {
            "type": "polygon",
            "space": "layer",
            "points": [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 0, "y": 4}],
            "feather_px": 0,
            "invert": False,
        }
        renderer, _ = self.renderer(polygon, self.manifest({"image": image}))
        masked = renderer.render_frame(0)
        self.assertEqual(masked.getpixel((0, 0)), (255, 0, 0, 255))
        self.assertEqual(masked.getpixel((3, 3)), (255, 255, 255, 255))

    def test_asset_and_output_paths_cannot_escape_project_root(self):
        slot = {"id": "image", "type": "image", "required": True, "accepted_media": ["image/png"]}
        tracks = [{"id": "main", "type": "prop", "z_index": 0, "overlap_policy": "allow"}]
        document = template([slot], tracks, [layer("image", "main", "image")])
        manifest = self.manifest({})
        manifest["assets"] = [
            {
                "slot_id": "image",
                "path": "../escape.png",
                "media_type": "image/png",
                "rights_confirmed": True,
                "cloud_upload_allowed": False,
                "processor": "direct",
            }
        ]
        with self.assertRaises(rrv_render.PathPolicyError):
            rrv_render.resolve_local_assets(document, manifest, self.root)
        with self.assertRaises(rrv_render.PathPolicyError):
            rrv_render.resolve_project_path(self.root, "../out.mp4", purpose="output")

    def test_unsupported_blend_and_mask_fail_closed(self):
        image = self.image("assets/image.png", "#ff0000")
        slots = [{"id": "image", "type": "image", "required": True, "accepted_media": ["image/png"]}]
        tracks = [{"id": "main", "type": "prop", "z_index": 0, "overlap_policy": "allow"}]
        document = template(slots, tracks, [layer("image", "main", "image", blend="multiply")])
        assets = rrv_render.resolve_local_assets(document, self.manifest({"image": image}), self.root)
        with self.assertRaises(rrv_render.UnsupportedFeatureError):
            rrv_render.S1Renderer(document, assets)

        masked = copy.deepcopy(document)
        masked["layers"][0]["blend"]["mode"] = "normal"
        masked["layers"][0]["mask"] = {
            "type": "rounded-rect",
            "space": "layer",
            "rect": {"x": 0, "y": 0, "width": 2, "height": 2},
            "corner_radius_px": 1,
            "feather_px": 0,
            "invert": False,
        }
        with self.assertRaises(rrv_render.UnsupportedFeatureError):
            rrv_render.S1Renderer(masked, assets)

    def test_canvas_background_with_alpha_is_rejected_before_master_render(self):
        image = self.image("assets/image.png", "#ff0000")
        slots = [{"id": "image", "type": "image", "required": True, "accepted_media": ["image/png"]}]
        tracks = [{"id": "main", "type": "prop", "z_index": 0, "overlap_policy": "allow"}]
        document = template(slots, tracks, [layer("image", "main", "image")])
        document["canvas"]["background"] = "#ffffff00"
        assets = rrv_render.resolve_local_assets(document, self.manifest({"image": image}), self.root)
        with self.assertRaises(rrv_render.UnsupportedFeatureError):
            rrv_render.S1Renderer(document, assets)

    def test_unresolved_review_blocks_render_and_encode_before_writes(self):
        image = self.image("assets/review-required.png", "#336699")
        slots = [{"id": "image", "type": "image", "required": True, "accepted_media": ["image/png"]}]
        tracks = [{"id": "main", "type": "prop", "z_index": 0, "overlap_policy": "allow"}]
        document = template(
            slots,
            tracks,
            [layer("image", "main", "image", ranges=[{"start_frame": 0, "end_frame": 2}])],
            duration=2,
        )
        document["support"]["review_required"] = True
        manifest = self.manifest({"image": image})

        self.assertEqual(video_remix.validate_template_data(document), [])
        assets = rrv_render.resolve_local_assets(document, manifest, self.root)
        with self.assertRaisesRegex(rrv_render.RenderInputError, "review_required"):
            rrv_render.S1Renderer(document, assets)

        self.assert_zero_write_project_failure(
            document,
            manifest,
            "review-required",
            rrv_render.RenderInputError,
        )
        self.assertFalse((self.root / "render" / "master-frames").exists())
        with self.assertRaisesRegex(rrv_render.RenderInputError, "review_required"):
            rrv_render.encode_outputs(
                document,
                assets,
                self.root,
                self.root / "render" / "master-frames",
                runner=lambda arguments: self.fail(f"encoder was called: {arguments}"),
            )

    def test_project_preflight_rejects_schema_legal_unsupported_output_profiles_without_writes(self):
        image = self.image("assets/preflight-profile.png", "#336699")
        slots = [{"id": "image", "type": "image", "required": True, "accepted_media": ["image/png"]}]
        tracks = [{"id": "main", "type": "prop", "z_index": 0, "overlap_policy": "allow"}]
        cases = {
            "hevc": {"codec": "hevc"},
            "ten-bit": {"pixel_format": "yuv420p10le"},
            "unsupported-size": {"width": 640, "height": 1136},
        }
        for case_name, changes in cases.items():
            with self.subTest(case=case_name):
                document = template(
                    slots,
                    tracks,
                    [layer("image", "main", "image", ranges=[{"start_frame": 0, "end_frame": 2}])],
                    duration=2,
                )
                document["outputs"][0].update(changes)
                manifest = self.manifest({"image": image})
                self.assertEqual(video_remix.validate_template_data(document), [])
                self.assertEqual(
                    video_remix.validate_assets_data(
                        document,
                        manifest,
                        self.root / f"{case_name}.assets.json",
                        check_files=True,
                        project_root=self.root,
                    ),
                    [],
                )
                self.assert_zero_write_project_failure(
                    document, manifest, case_name, rrv_render.UnsupportedFeatureError
                )

    def test_project_preflight_rejects_bad_audio_type_and_filter_without_writes(self):
        source = self.root / "assets" / "source.bin"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"preflight only")
        visual = self.image("assets/preflight-audio.png", "#996633")
        tracks = [{"id": "main", "type": "prop", "z_index": 0, "overlap_policy": "allow"}]

        bad_type = template(
            [
                {"id": "image", "type": "image", "required": True, "accepted_media": ["image/png"]},
                {"id": "audio", "type": "audio", "required": True, "accepted_media": ["video/mp4"]},
            ],
            tracks,
            [layer("image", "main", "image", ranges=[{"start_frame": 0, "end_frame": 2}])],
            duration=2,
        )
        bad_type_manifest = self.manifest({"image": visual})
        bad_type_manifest["assets"].append(
            {
                "slot_id": "audio",
                "path": source.relative_to(self.root).as_posix(),
                "media_type": "video/mp4",
                "rights_confirmed": True,
                "cloud_upload_allowed": False,
                "processor": "direct",
            }
        )
        self.assertEqual(video_remix.validate_template_data(bad_type), [])
        self.assertEqual(
            video_remix.validate_assets_data(
                bad_type,
                bad_type_manifest,
                self.root / "bad-type.assets.json",
                check_files=True,
                project_root=self.root,
            ),
            [],
        )
        self.assert_zero_write_project_failure(
            bad_type, bad_type_manifest, "bad-audio-type", rrv_render.UnsupportedFeatureError
        )

        bad_filter = template(
            [
                {"id": "image", "type": "image", "required": True, "accepted_media": ["image/png"]},
                {"id": "audio", "type": "audio", "required": True, "accepted_media": ["audio/wav"]},
            ],
            tracks,
            [layer("image", "main", "image", ranges=[{"start_frame": 0, "end_frame": 2}])],
            duration=2,
        )
        bad_filter["audio"]["playback_rate"] = 2**40
        bad_filter["audio"]["loop"] = True
        bad_filter_manifest = copy.deepcopy(bad_type_manifest)
        bad_filter_manifest["assets"][1]["media_type"] = "audio/wav"
        self.assertEqual(video_remix.validate_template_data(bad_filter), [])
        self.assertEqual(
            video_remix.validate_assets_data(
                bad_filter,
                bad_filter_manifest,
                self.root / "bad-filter.assets.json",
                check_files=True,
                project_root=self.root,
            ),
            [],
        )
        self.assert_zero_write_project_failure(
            bad_filter, bad_filter_manifest, "bad-audio-filter", rrv_render.UnsupportedFeatureError
        )

    def test_project_preflight_keeps_supported_s1_happy_path(self):
        image = self.image("assets/gold.png", "#2288cc")
        slots = [{"id": "image", "type": "image", "required": True, "accepted_media": ["image/png"]}]
        tracks = [{"id": "main", "type": "prop", "z_index": 0, "overlap_policy": "allow"}]
        document = template(
            slots,
            tracks,
            [layer("image", "main", "image", ranges=[{"start_frame": 0, "end_frame": 2}])],
            duration=2,
        )
        document["outputs"][0]["filename"] = "preflight/gold/result.mp4"
        manifest = self.manifest({"image": image})
        calls = []
        summary = rrv_render.render_project(
            document,
            manifest,
            self.root,
            frame_directory="preflight/gold/master",
            encoder_runner=lambda arguments: calls.append(arguments),
        )
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(len(calls), 1)
        self.assertTrue((self.root / "preflight" / "gold" / "master" / "frame_000001.png").is_file())

    def test_master_frame_directory_never_overwrites_existing_target(self):
        image = self.image("assets/image.png", "#ff0000")
        slots = [{"id": "image", "type": "image", "required": True, "accepted_media": ["image/png"]}]
        tracks = [{"id": "main", "type": "prop", "z_index": 0, "overlap_policy": "allow"}]
        document = template(slots, tracks, [layer("image", "main", "image")])
        renderer, _ = self.renderer(document, self.manifest({"image": image}))
        target = self.root / "render" / "master-frames"
        target.mkdir(parents=True)
        with self.assertRaises(rrv_render.RenderInputError):
            renderer.write_master_frames(self.root)
        target.rmdir()
        renderer.write_master_frames(self.root)
        with self.assertRaises(rrv_render.RenderInputError):
            renderer.write_master_frames(self.root)

    def test_encode_requires_an_exact_contiguous_master_sequence(self):
        document = template([], [], [], duration=2)
        frame_dir = self.master_sequence(1)
        with self.assertRaises(rrv_render.RenderInputError):
            rrv_render.encode_outputs(document, {}, self.root, frame_dir, runner=lambda arguments: None)

        extra_dir = self.master_sequence(2, directory="render/extra-master")
        extra = Image.new("RGBA", (8, 4), "#ffffff")
        try:
            extra.save(extra_dir / "frame_000002.png")
        finally:
            extra.close()
        with self.assertRaises(rrv_render.RenderInputError):
            rrv_render.encode_outputs(document, {}, self.root, extra_dir, runner=lambda arguments: None)

    def test_encode_preflight_checks_every_profile_before_creating_output_parents(self):
        frame_dir = self.master_sequence(2)
        document = template([], [], [], duration=2)
        document["outputs"][0]["filename"] = "preflight/encode/first.mp4"
        unsupported = copy.deepcopy(document["outputs"][0])
        unsupported["id"] = "unsupported-second"
        unsupported["filename"] = "preflight/encode/second.mp4"
        unsupported["codec"] = "hevc"
        document["outputs"].append(unsupported)
        calls = []
        with self.assertRaises(rrv_render.UnsupportedFeatureError):
            rrv_render.encode_outputs(
                document,
                {},
                self.root,
                frame_dir,
                runner=lambda arguments: calls.append(arguments),
            )
        self.assertEqual(calls, [])
        self.assertFalse((self.root / "preflight" / "encode").exists())

    def test_encode_refuses_existing_master_asset_and_duplicate_output_paths(self):
        frame_dir = self.master_sequence(2)

        existing = template([], [], [], duration=2)
        existing_path = self.root / existing["outputs"][0]["filename"]
        existing_path.parent.mkdir(parents=True)
        existing_path.write_bytes(b"already exists")
        with self.assertRaises(rrv_render.RenderInputError):
            rrv_render.encode_outputs(existing, {}, self.root, frame_dir, runner=lambda arguments: None)

        inside_master = template([], [], [], duration=2)
        inside_master["outputs"][0]["filename"] = "render/master-frames/result.mp4"
        with self.assertRaises(rrv_render.PathPolicyError):
            rrv_render.encode_outputs(inside_master, {}, self.root, frame_dir, runner=lambda arguments: None)

        duplicate = template([], [], [], duration=2)
        duplicate["outputs"][0]["filename"] = "deliveries/duplicate.mp4"
        copied = copy.deepcopy(duplicate["outputs"][0])
        copied["id"] = "vertical-720-copy"
        duplicate["outputs"].append(copied)
        with self.assertRaises(rrv_render.RenderInputError):
            rrv_render.encode_outputs(duplicate, {}, self.root, frame_dir, runner=lambda arguments: None)

        asset_path = self.root / "assets" / "collision.mp4"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(b"input asset")
        collision = template([], [], [], duration=2)
        collision["outputs"][0]["filename"] = "assets/collision.mp4"
        assets = {
            "input": rrv_render.ResolvedAsset("input", asset_path, "image/png", "direct"),
        }
        with self.assertRaises(rrv_render.PathPolicyError):
            rrv_render.encode_outputs(collision, assets, self.root, frame_dir, runner=lambda arguments: None)

    def test_default_encoder_maps_runtime_timeout_without_changing_fake_runner_contract(self):
        frame_dir = self.master_sequence(2)
        document = template([], [], [], duration=2)
        with patch.object(rrv_render.rrv_runtime, "run_command") as run_command:
            rrv_render.encode_outputs(document, {}, self.root, frame_dir, timeout_seconds=7)
        command = run_command.call_args.args[0]
        self.assertEqual(command[0], "ffmpeg")
        self.assertEqual(run_command.call_args.kwargs["timeout_seconds"], 7.0)
        self.assertTrue(run_command.call_args.kwargs["check"])

        timeout_error = rrv_render.rrv_runtime.RRVError(
            rrv_render.rrv_runtime.ERR_TOOL_TIMEOUT, "deliberately timed out"
        )
        with patch.object(rrv_render.rrv_runtime, "run_command", side_effect=timeout_error):
            with self.assertRaisesRegex(rrv_render.EncoderError, "timed out"):
                rrv_render.encode_outputs(document, {}, self.root, frame_dir, timeout_seconds=3)

    def test_fake_encoder_receives_parameter_array_with_audio_trim_and_reframe(self):
        frame_dir = self.master_sequence(2)
        audio_file = self.root / "assets" / "source.wav"
        audio_file.parent.mkdir(parents=True)
        audio_file.write_bytes(b"not-decoded-by-fake")
        slots = [{"id": "audio", "type": "audio", "required": True, "accepted_media": ["audio/wav"]}]
        tracks = [{"id": "main", "type": "prop", "z_index": 0, "overlap_policy": "allow"}]
        outputs = [
            {
                "id": "vertical-720",
                "width": 720,
                "height": 1280,
                "codec": "h264",
                "pixel_format": "yuv420p",
                "audio_codec": "aac",
                "filename": "deliveries/result.mp4",
                "reframe": {
                    "mode": "contain",
                    "object_position": {"x": 0.5, "y": 0.5},
                    "background": "#ffffff",
                },
            }
        ]
        document = template(slots, tracks, [], duration=2, outputs=outputs)
        assets = {
            "audio": rrv_render.ResolvedAsset("audio", audio_file, "audio/wav", "direct"),
        }
        calls = []
        encoded = rrv_render.encode_outputs(
            document,
            assets,
            self.root,
            frame_dir,
            ffmpeg_bin="fake ffmpeg; never-a-shell-command",
            runner=lambda arguments: calls.append(arguments),
        )
        self.assertEqual(len(encoded), 1)
        self.assertEqual(calls[0][0], "fake ffmpeg; never-a-shell-command")
        self.assertIn("-n", calls[0])
        self.assertIn("-nostdin", calls[0])
        self.assertNotIn("-y", calls[0])
        self.assertEqual(calls[0][calls[0].index("-map_metadata") + 1], "-1")
        self.assertEqual(calls[0][calls[0].index("-map_chapters") + 1], "-1")
        self.assertEqual(calls[0][calls[0].index("-crf") + 1], "18")
        self.assertEqual(calls[0][calls[0].index("-preset") + 1], "medium")
        self.assertIn("-filter_complex", calls[0])
        self.assertIn("atrim=start=0:end=0.2", calls[0][calls[0].index("-filter_complex") + 1])
        self.assertIn("scale=720:1280", calls[0][calls[0].index("-vf") + 1])
        summary = rrv_render.build_run_summary(document, assets, self.root, frame_dir, encoded, debug_bounds=False)
        self.assertEqual(rrv_render.stable_summary_json(summary), rrv_render.stable_summary_json(summary))
        self.assertNotIn(str(self.root), rrv_render.stable_summary_json(summary))


if __name__ == "__main__":
    unittest.main()
