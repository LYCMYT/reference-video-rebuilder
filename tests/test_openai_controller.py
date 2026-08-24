import base64
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "reference-video-rebuilder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

try:
    from PIL import Image, PngImagePlugin
except ImportError:  # pragma: no cover
    Image = None
    PngImagePlugin = None

import openai_image_controller  # noqa: E402
import rrv_assets  # noqa: E402
import rrv_generation  # noqa: E402
import rrv_openai_controller as controller  # noqa: E402
import rrv_runtime  # noqa: E402
from tests.test_generation import template_document  # noqa: E402

try:  # Optional controller dependency, installed by requirements-dev.txt.
    import httpx
    import openai
except ImportError:  # pragma: no cover
    httpx = None
    openai = None


class _FakeImages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def edit(self, **kwargs):
        self.calls.append(kwargs)
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class _FakeClient:
    def __init__(self, responses):
        self.images = _FakeImages(responses)
        self.closed = False

    def close(self):
        self.closed = True


@unittest.skipUnless(Image is not None, "Pillow is installed from requirements-runtime.txt")
class OpenAIControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.reference_pack = self.root / "reference-pack"
        self.reference_pack.mkdir()

    def _write_json(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return relative

    def _write_reference(self, name, color=(30, 100, 220), *, metadata=False):
        target = self.reference_pack / name
        with Image.new("RGB", (12, 10), color) as image:
            if metadata:
                info = PngImagePlugin.PngInfo()
                info.add_text("private-reference-note", "PRIVATE_REFERENCE_METADATA")
                image.save(target, format="PNG", pnginfo=info)
            else:
                image.save(target, format="PNG")
        return target

    def _request(self, targets):
        return {
            "schema_version": "0.6.0",
            "privacy_profile": "controller-cloud",
            "execution_profile": "controller-managed",
            "adapter_id": controller.ADAPTER_ID,
            "adapter_version": controller.ADAPTER_VERSION,
            "controller_label": "OpenAI GPT Image 2",
            "cloud_upload_confirmed": True,
            "tasks": [
                {
                    "target_slot_id": target,
                    "kind": "reference-guided-still",
                    "references": [
                        {"source_filename": f"reference-{index:02d}.png", "role": "reference"}
                    ],
                    "instructions": f"Create approved still {index}; PRIVATE_BRIEF_{index}.",
                    "passthrough": False,
                    "omit": False,
                }
                for index, target in enumerate(targets, start=1)
            ],
        }

    def _prepare(self, count=1):
        targets = tuple(f"look.{index:02d}" for index in range(1, count + 1))
        template = template_document(
            [
                {
                    "id": target,
                    "type": "image",
                    "required": True,
                    "accepted_media": ["image/png"],
                }
                for target in targets
            ]
        )
        self._write_json("template.ir.json", template)
        for index in range(1, count + 1):
            self._write_reference(
                f"reference-{index:02d}.png",
                (index * 13, 60, 180),
                metadata=index == 1,
            )
        self._write_json("generation-request.json", self._request(targets))
        prepared = rrv_generation.prepare_generation(
            "template.ir.json",
            "generation-request.json",
            project_root=self.root,
            reference_pack="reference-pack",
            generation_rights_confirmed=True,
            output_dir="generation-plan",
        )
        review_path = self.root / prepared["artifacts"]["review_template"]["path"]
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review.update(
            {
                "decision": "approved",
                "input_contact_sheet_reviewed": True,
                "request_reviewed": True,
                "execution_profile_confirmed": True,
            }
        )
        for task in review["tasks"]:
            task.update(
                {
                    "decision": "accept",
                    "references_confirmed": True,
                    "instruction_scope_confirmed": True,
                    "rights_confirmed": True,
                }
            )
        approved_review = self._write_json("generation-plan/approved-review.json", review)
        return prepared["artifacts"]["generation_plan"]["path"], approved_review

    def _provider_png(self, color=(120, 50, 210), *, size=(1024, 1536), metadata=True):
        buffer = io.BytesIO()
        with Image.new("RGB", size, color) as image:
            if metadata:
                info = PngImagePlugin.PngInfo()
                info.add_text("private-provider-id", "request_PRIVATE_123")
                image.save(buffer, format="PNG", pnginfo=info)
            else:
                image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def _run(self, plan, review, fake, **kwargs):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-private"}, clear=False), mock.patch.object(
            controller, "_default_client_factory", return_value=fake
        ):
            return controller.run_openai_generation(
                plan,
                review,
                project_root=self.root,
                output_dir=kwargs.pop("output_dir", "openai-results"),
                generation_rights_confirmed=kwargs.pop("generation_rights_confirmed", True),
                cloud_upload_confirmed=kwargs.pop("cloud_upload_confirmed", True),
                billable_requests_confirmed=kwargs.pop("billable_requests_confirmed", True),
                max_billable_requests=kwargs.pop("max_billable_requests", 32),
                **kwargs,
            )

    def test_preflight_is_local_read_only_and_does_not_touch_env_or_sdk(self):
        plan, review = self._prepare()
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        with mock.patch.object(os.environ, "get", side_effect=AssertionError("env accessed")), mock.patch.object(
            controller, "_default_client_factory", side_effect=AssertionError("SDK accessed")
        ):
            result = controller.preflight_openai_generation(
                plan,
                review,
                project_root=self.root,
                generation_rights_confirmed=True,
            )
        after = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        self.assertEqual(before, after)
        self.assertEqual(result["counts"]["generation_tasks"], 1)
        self.assertEqual(result["adapter"], {"id": controller.ADAPTER_ID, "version": controller.ADAPTER_VERSION})

    def test_preflight_never_spools_private_media_to_os_temp(self):
        plan, review = self._prepare()
        with mock.patch.object(
            rrv_assets.tempfile,
            "SpooledTemporaryFile",
            side_effect=AssertionError("preflight attempted an OS-temp-backed snapshot"),
        ):
            result = controller.preflight_openai_generation(
                plan,
                review,
                project_root=self.root,
                generation_rights_confirmed=True,
            )
        self.assertEqual(result["counts"]["generation_tasks"], 1)

    def test_all_confirmations_fail_before_root_env_or_client(self):
        cases = [
            {"generation_rights_confirmed": False},
            {"cloud_upload_confirmed": False},
            {"billable_requests_confirmed": False},
            {"max_billable_requests": 0},
            {"max_billable_requests": 33},
            {"max_billable_requests": True},
        ]
        defaults = {
            "generation_rights_confirmed": True,
            "cloud_upload_confirmed": True,
            "billable_requests_confirmed": True,
            "max_billable_requests": 1,
        }
        for mutation in cases:
            values = {**defaults, **mutation}
            with self.subTest(mutation=mutation), mock.patch.object(
                rrv_assets, "_safe_project_root", side_effect=AssertionError("root accessed")
            ), mock.patch.object(os.environ, "get", side_effect=AssertionError("env accessed")), mock.patch.object(
                controller, "_default_client_factory", side_effect=AssertionError("client accessed")
            ):
                with self.assertRaises(rrv_runtime.RRVError):
                    controller.run_openai_generation(
                        "PRIVATE_PLAN",
                        "PRIVATE_REVIEW",
                        project_root="PRIVATE_ROOT",
                        **values,
                    )

    def test_fixed_provider_contract_and_metadata_free_atomic_output(self):
        plan, review = self._prepare()
        fake = _FakeClient([{"data": [{"b64_json": self._provider_png()}]}])
        result = self._run(plan, review, fake, max_billable_requests=1)
        self.assertEqual(result["counts"]["billable_requests"], 1)
        self.assertEqual(len(fake.images.calls), 1)
        call = fake.images.calls[0]
        self.assertEqual(
            {key: call[key] for key in ("model", "n", "quality", "size", "output_format", "background", "response_format")},
            {
                "model": controller.MODEL,
                "n": 1,
                "quality": "high",
                "size": "1024x1536",
                "output_format": "png",
                "background": "opaque",
                "response_format": "b64_json",
            },
        )
        self.assertEqual(call["extra_body"], {"moderation": "auto"})
        self.assertNotIn("input_fidelity", call)
        self.assertEqual(call["image"][0][0], "input-01.png")
        with Image.open(io.BytesIO(call["image"][0][1])) as uploaded:
            self.assertNotIn("private-reference-note", uploaded.info)
            self.assertEqual(dict(uploaded.getexif()), {})
        self.assertIn("PRIVATE_BRIEF_1", call["prompt"])
        output = self.root / "openai-results" / "look.01.png"
        self.assertTrue(output.is_file())
        with Image.open(output) as image:
            self.assertEqual(image.size, (1024, 1536))
            self.assertEqual(image.mode, "RGB")
            self.assertNotIn("private-provider-id", image.info)
            self.assertEqual(dict(image.getexif()), {})
        self.assertEqual(list((self.root / "openai-results").iterdir()), [output])

    def test_twelve_tasks_use_exactly_twelve_sequential_calls(self):
        plan, review = self._prepare(12)
        response = {"data": [{"b64_json": self._provider_png(metadata=False)}]}
        fake = _FakeClient([response for _ in range(12)])
        result = self._run(plan, review, fake, max_billable_requests=12)
        self.assertEqual(result["counts"]["billable_requests"], 12)
        self.assertEqual(len(fake.images.calls), 12)
        self.assertEqual(len(list((self.root / "openai-results").glob("*.png"))), 12)
        self.assertEqual([asset["slot_id"] for asset in result["assets"]], [f"look.{i:02d}" for i in range(1, 13)])

    def test_request_cap_fails_before_env_client_or_stage(self):
        plan, review = self._prepare(2)
        with mock.patch.object(os.environ, "get", side_effect=AssertionError("env accessed")), mock.patch.object(
            controller, "_default_client_factory", side_effect=AssertionError("client accessed")
        ):
            with self.assertRaises(rrv_runtime.RRVError):
                controller.run_openai_generation(
                    plan,
                    review,
                    project_root=self.root,
                    generation_rights_confirmed=True,
                    cloud_upload_confirmed=True,
                    billable_requests_confirmed=True,
                    max_billable_requests=1,
                )
        self.assertFalse((self.root / "openai-generation-result-pack").exists())
        self.assertFalse(any(path.name.startswith(".rrv-openai-generation-") for path in self.root.iterdir()))

    def test_provider_failure_after_one_paid_call_publishes_nothing_and_does_not_retry(self):
        plan, review = self._prepare(2)
        private = RuntimeError(r"C:\PRIVATE\request-id-secret")
        fake = _FakeClient(
            [
                {"data": [{"b64_json": self._provider_png(metadata=False)}]},
                private,
            ]
        )
        with self.assertRaises(rrv_runtime.RRVError) as caught:
            self._run(plan, review, fake, max_billable_requests=2)
        self.assertEqual(caught.exception.code, rrv_runtime.ERR_TOOL_EXECUTION)
        self.assertNotIn("PRIVATE", caught.exception.message)
        self.assertEqual(len(fake.images.calls), 2)
        self.assertTrue(fake.closed)
        self.assertFalse((self.root / "openai-results").exists())
        self.assertFalse(any(path.name.startswith(".rrv-openai-generation-") for path in self.root.iterdir()))

    def test_client_closes_if_staging_fails_before_any_request(self):
        plan, review = self._prepare()
        fake = _FakeClient([])
        with mock.patch.object(
            controller.rrv_propose,
            "_new_staging_directory",
            side_effect=rrv_runtime.RRVError(
                rrv_runtime.ERR_TOOL_EXECUTION, "simulated bounded staging failure"
            ),
        ):
            with self.assertRaises(rrv_runtime.RRVError):
                self._run(plan, review, fake, max_billable_requests=1)
        self.assertTrue(fake.closed)
        self.assertEqual(fake.images.calls, [])

    def test_no_fallible_reference_check_runs_after_atomic_publication(self):
        plan, review = self._prepare()
        fake = _FakeClient([{"data": [{"b64_json": self._provider_png(metadata=False)}]}])
        original_publish = controller.rrv_propose._publish_stage
        original_assert = controller.rrv_assets._assert_pack_live
        state = {"published": False}

        def publish(*args, **kwargs):
            original_publish(*args, **kwargs)
            state["published"] = True

        def assert_live(*args, **kwargs):
            if state["published"]:
                raise AssertionError("fallible check ran after publication")
            return original_assert(*args, **kwargs)

        with mock.patch.object(controller.rrv_propose, "_publish_stage", side_effect=publish), mock.patch.object(
            controller.rrv_assets, "_assert_pack_live", side_effect=assert_live
        ):
            result = self._run(plan, review, fake, max_billable_requests=1)
        self.assertEqual(result["counts"]["output_assets"], 1)
        self.assertTrue((self.root / "openai-results" / "look.01.png").is_file())

    def test_invalid_base64_and_wrong_dimensions_publish_nothing(self):
        plan, review = self._prepare()
        for name, encoded in (
            ("base64", "not base64 PRIVATE"),
            ("dimensions", self._provider_png(size=(16, 16), metadata=False)),
        ):
            fake = _FakeClient([{"data": [{"b64_json": encoded}]}])
            with self.subTest(name=name), self.assertRaises(rrv_runtime.RRVError):
                self._run(plan, review, fake, output_dir=f"output-{name}", max_billable_requests=1)
            self.assertFalse((self.root / f"output-{name}").exists())

    def test_oversized_provider_header_is_rejected_before_pixel_decode(self):
        class OversizedHeader:
            format = "PNG"
            n_frames = 1
            size = (50_000, 50_000)

            def __init__(self):
                self.load_called = False

            def load(self):
                self.load_called = True
                raise AssertionError("oversized pixels must not be decoded")

            def close(self):
                pass

        oversized = OversizedHeader()
        with mock.patch("PIL.Image.open", return_value=oversized):
            with self.assertRaises(rrv_runtime.RRVError) as caught:
                controller._write_sanitized_output(None, Path("never-written.png"), b"bounded")
        self.assertEqual(caught.exception.code, rrv_runtime.ERR_TOOL_EXECUTION)
        self.assertFalse(oversized.load_called)

    def test_existing_output_is_rejected_before_env_and_provider(self):
        plan, review = self._prepare()
        (self.root / "existing-output").mkdir()
        with mock.patch.object(os.environ, "get", side_effect=AssertionError("env accessed")), mock.patch.object(
            controller, "_default_client_factory", side_effect=AssertionError("client accessed")
        ):
            with self.assertRaises(rrv_runtime.RRVError) as caught:
                controller.run_openai_generation(
                    plan,
                    review,
                    project_root=self.root,
                    output_dir="existing-output",
                    generation_rights_confirmed=True,
                    cloud_upload_confirmed=True,
                    billable_requests_confirmed=True,
                    max_billable_requests=1,
                )
        self.assertEqual(caught.exception.code, rrv_runtime.ERR_OUTPUT_EXISTS)

    def test_missing_key_is_stable_and_does_not_create_stage(self):
        plan, review = self._prepare()
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(rrv_runtime.RRVError) as caught:
                controller.run_openai_generation(
                    plan,
                    review,
                    project_root=self.root,
                    generation_rights_confirmed=True,
                    cloud_upload_confirmed=True,
                    billable_requests_confirmed=True,
                    max_billable_requests=1,
                )
        self.assertEqual(caught.exception.code, rrv_runtime.ERR_CAPABILITY_UNAVAILABLE)
        self.assertNotIn(str(self.root), caught.exception.message)
        self.assertFalse(any(path.name.startswith(".rrv-openai-generation-") for path in self.root.iterdir()))

    def test_cli_failure_json_does_not_reflect_exception_or_path(self):
        private = r"C:\PRIVATE\prompt-and-request-id"
        stdout = io.StringIO()
        with mock.patch.object(
            openai_image_controller.rrv_openai_controller,
            "run_openai_generation",
            side_effect=RuntimeError(private),
        ), mock.patch("sys.stdout", stdout):
            code = openai_image_controller.main(
                [
                    "run",
                    private,
                    private,
                    "--project-root",
                    private,
                    "--generation-rights-confirmed",
                    "--cloud-upload-confirmed",
                    "--billable-requests-confirmed",
                    "--max-billable-requests",
                    "1",
                    "--json",
                ]
            )
        self.assertEqual(code, 2)
        self.assertNotIn("PRIVATE", stdout.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["error"]["code"], rrv_runtime.ERR_TOOL_EXECUTION)

    def test_cli_preflight_success_returns_only_compact_reviewed_counts(self):
        plan, review = self._prepare()
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            code = openai_image_controller.main(
                [
                    "preflight",
                    plan,
                    review,
                    "--project-root",
                    str(self.root),
                    "--generation-rights-confirmed",
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(
            set(payload["result"]),
            {"schema_version", "operation", "approved", "adapter", "counts"},
        )
        self.assertEqual(payload["result"]["counts"], {"generation_tasks": 1, "approved_references": 1})

    @unittest.skipUnless(openai is not None and httpx is not None, "optional OpenAI SDK is installed")
    def test_installed_sdk_accepts_fixed_edit_contract_without_network(self):
        captured = {}
        encoded = self._provider_png(metadata=False)

        def handler(request):
            captured["request"] = request
            return httpx.Response(200, json={"created": 1, "data": [{"b64_json": encoded}]})

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        client = openai.OpenAI(api_key="sk-test-only", max_retries=0, timeout=5, http_client=http_client)
        try:
            tiny = io.BytesIO()
            with Image.new("RGB", (2, 2), (20, 30, 40)) as image:
                image.save(tiny, format="PNG")
            result = controller._call_image_edit(
                client,
                uploads=[("input-01.png", tiny.getvalue(), "image/png")],
                prompt="approved test prompt",
            )
        finally:
            client.close()
        self.assertEqual(result, encoded)
        body = captured["request"].read()
        self.assertIn(b'name="moderation"', body)
        self.assertIn(b"auto", body)
        self.assertIn(controller.MODEL.encode("ascii"), body)
        self.assertNotIn(b"input_fidelity", body)

    @unittest.skipUnless(openai is not None and httpx is not None, "optional OpenAI SDK is installed")
    def test_default_sdk_client_pins_endpoint_and_ignores_proxy_environment(self):
        captured = {}
        encoded = self._provider_png(metadata=False)

        def handler(request):
            captured["request"] = request
            return httpx.Response(200, json={"created": 1, "data": [{"b64_json": encoded}]})

        pollution = {
            "OPENAI_BASE_URL": "https://unapproved.example.invalid/v1",
            "OPENAI_ORG_ID": "PRIVATE_ORG",
            "OPENAI_PROJECT_ID": "PRIVATE_PROJECT",
            "OPENAI_ADMIN_KEY": "PRIVATE_ADMIN",
            "OPENAI_WEBHOOK_SECRET": "PRIVATE_WEBHOOK",
            "HTTP_PROXY": "http://unapproved.example.invalid:8080",
            "HTTPS_PROXY": "http://unapproved.example.invalid:8080",
            "SSL_CERT_FILE": r"C:\PRIVATE\ca.pem",
        }
        with mock.patch.dict(os.environ, pollution, clear=False):
            client = controller._default_client_factory("sk-test-only", 5)
        try:
            self.assertEqual(str(client.base_url), "https://api.openai.com/v1/")
            self.assertEqual(client.organization, "")
            self.assertEqual(client.project, "")
            self.assertEqual(client.max_retries, 0)
            self.assertFalse(client._client._trust_env)
        finally:
            client.close()

        isolated_transport = httpx.Client(
            transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False
        )
        request_client = openai.OpenAI(
            api_key="sk-test-only",
            organization="",
            project="",
            base_url="https://api.openai.com/v1",
            default_headers={
                "OpenAI-Organization": openai.omit,
                "OpenAI-Project": openai.omit,
            },
            max_retries=0,
            timeout=5,
            http_client=isolated_transport,
        )
        try:
            tiny = io.BytesIO()
            with Image.new("RGB", (2, 2), (20, 30, 40)) as image:
                image.save(tiny, format="PNG")
            controller._call_image_edit(
                request_client,
                uploads=[("input-01.png", tiny.getvalue(), "image/png")],
                prompt="approved test prompt",
            )
            headers = {name.lower() for name in captured["request"].headers}
            self.assertNotIn("openai-organization", headers)
            self.assertNotIn("openai-project", headers)
        finally:
            request_client.close()

    def test_custom_sdk_headers_and_logging_environment_fail_closed(self):
        for name, value in (
            ("OPENAI_CUSTOM_HEADERS", "X-Private: secret"),
            ("OPENAI_LOG", "debug"),
            ("SSLKEYLOGFILE", r"C:\PRIVATE\tls-secrets.log"),
        ):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: value}, clear=False):
                with self.assertRaises(rrv_runtime.RRVError) as caught:
                    controller._default_client_factory("sk-test-only", 5)
            self.assertEqual(caught.exception.code, rrv_runtime.ERR_CAPABILITY_UNAVAILABLE)
            self.assertNotIn(value, caught.exception.message)


if __name__ == "__main__":
    unittest.main()
