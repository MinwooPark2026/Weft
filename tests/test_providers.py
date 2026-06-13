"""Provider-layer tests: ComfyUI/Gemini (mocked urllib — never a real server),
the registry dictionary, aspect normalization, the @char character-sheet hook,
and the legacy ``images/openai`` layout fallback.
"""

from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from weft.assets import (
    GEN_IMG_SUBDIR,
    IMG_SUBDIRS,
    LEGACY_IMG_SUBDIRS,
    append_candidates,
    conform_to_aspect,
    find_character_sheet,
    generate_images,
)
from weft.picker.server import _save_pick, build_state
from weft.providers.comfyui_image import ComfyUIImage
from weft.providers.gemini_image import GeminiImage
from weft.providers.openai_image import OpenAIImage
from weft.providers.registry import (
    create_image_provider,
    create_tts_provider,
    image_provider_label,
    image_provider_options,
)


def _png_bytes(width: int, height: int, color=(200, 100, 50)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _png_size(blob: bytes) -> tuple[int, int]:
    from PIL import Image

    return Image.open(io.BytesIO(blob)).size

WORKFLOW = {
    "3": {"class_type": "KSampler", "inputs": {"seed": 42, "steps": 20, "positive": ["6", 0]}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "__WEFT_PROMPT__"}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
}


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _fake_comfy_server(state: dict, *, ready_after: int = 2):
    """A fake ``urlopen`` routing /prompt, /history/<id>, /view?…

    Each prompt id reports empty history until it was polled ``ready_after``
    times, which exercises the polling loop.
    """

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        path = urllib.parse.urlparse(url).path
        if path.endswith("/prompt"):
            body = json.loads(request.data.decode("utf-8"))
            state["queued"].append(body["prompt"])
            pid = f"pid-{len(state['queued'])}"
            return _FakeResponse(json.dumps({"prompt_id": pid}).encode("utf-8"))
        if "/history/" in path:
            pid = path.rsplit("/", 1)[-1]
            polls = state["polls"].setdefault(pid, 0) + 1
            state["polls"][pid] = polls
            if polls < ready_after:
                return _FakeResponse(b"{}")
            entry = {
                pid: {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"9": {"images": [{"filename": f"{pid}.png", "subfolder": "sub", "type": "output"}]}},
                }
            }
            return _FakeResponse(json.dumps(entry).encode("utf-8"))
        if path.endswith("/view"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            state["views"].append(query)
            return _FakeResponse(b"PNG:" + query["filename"][0].encode("utf-8"))
        raise AssertionError(f"unexpected URL {url}")

    return fake_urlopen


class ComfyUIImageTest(unittest.TestCase):
    def _workflow_file(self, tmp: str, payload: dict | str = WORKFLOW) -> Path:
        path = Path(tmp) / "workflow_api.json"
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=1)
        path.write_text(text, encoding="utf-8")
        return path

    def test_generate_substitutes_prompt_polls_and_varies_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = ComfyUIImage(workflow_path=str(self._workflow_file(tmp)), timeout=5, poll_interval=0)
            state = {"queued": [], "polls": {}, "views": []}
            fake_random = Mock()
            fake_random.randrange.side_effect = [111, 222]
            prompt = 'a "quoted" cat\nwith a newline'
            with patch("weft.providers.comfyui_image.urlopen", _fake_comfy_server(state)):
                with patch("weft.providers.comfyui_image.random", fake_random):
                    blobs = provider.generate(prompt, n=2)

            self.assertEqual([b"PNG:pid-1.png", b"PNG:pid-2.png"], blobs)
            self.assertEqual(2, len(state["queued"]))  # one queued run per candidate
            for graph in state["queued"]:
                # JSON-escape-safe substitution: quotes/newlines survive intact
                self.assertEqual(prompt, graph["6"]["inputs"]["text"])
            # no __WEFT_SEED__ in the template -> "seed" keys are varied per candidate
            seeds = [graph["3"]["inputs"]["seed"] for graph in state["queued"]]
            self.assertEqual([111, 222], seeds)
            # polling: each prompt id needed more than one /history call
            self.assertTrue(all(count >= 2 for count in state["polls"].values()))
            # downloads carried the history's filename/subfolder/type through /view
            self.assertEqual(["sub"], state["views"][0]["subfolder"])

    def test_seed_placeholder_replaces_quoted_and_bare_forms(self) -> None:
        quoted = dict(WORKFLOW)
        quoted["3"] = {"class_type": "KSampler", "inputs": {"seed": "__WEFT_SEED__", "steps": 20}}
        with tempfile.TemporaryDirectory() as tmp:
            provider = ComfyUIImage(workflow_path=str(self._workflow_file(tmp, quoted)))
            graph = provider._materialize("p", 123)
            self.assertEqual(123, graph["3"]["inputs"]["seed"])  # numeric, not the string "123"

            bare = json.dumps(quoted).replace('"__WEFT_SEED__"', "__WEFT_SEED__")
            provider = ComfyUIImage(workflow_path=str(self._workflow_file(tmp, bare)))
            graph = provider._materialize("p", 456)
            self.assertEqual(456, graph["3"]["inputs"]["seed"])

    def test_without_seed_placeholder_each_candidate_gets_distinct_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = ComfyUIImage(workflow_path=str(self._workflow_file(tmp)))
            first = provider._materialize("p", 7)["3"]["inputs"]["seed"]
            second = provider._materialize("p", 8)["3"]["inputs"]["seed"]
            self.assertEqual(7, first)
            self.assertEqual(8, second)
            self.assertNotEqual(first, second)

    def test_server_down_raises_actionable_korean_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = ComfyUIImage(workflow_path=str(self._workflow_file(tmp)), poll_interval=0)
            with patch("weft.providers.comfyui_image.urlopen", side_effect=URLError("connection refused")):
                with self.assertRaises(RuntimeError) as ctx:
                    provider.generate("p", n=1)
            self.assertIn("연결할 수 없습니다", str(ctx.exception))
            self.assertIn(provider.url, str(ctx.exception))

    def test_missing_workflow_env_and_file_raise_friendly_errors(self) -> None:
        with patch.dict(os.environ, {"COMFYUI_WORKFLOW": "", "COMFYUI_URL": "", "COMFYUI_TIMEOUT": ""}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                ComfyUIImage.from_env()
        self.assertIn("COMFYUI_WORKFLOW", str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx:
            ComfyUIImage(workflow_path="/no/such/workflow.json")
        self.assertIn("파일이 없습니다", str(ctx.exception))

    def test_workflow_without_prompt_placeholder_raises_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain = {"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a fixed prompt"}}}
            with self.assertRaises(RuntimeError) as ctx:
                ComfyUIImage(workflow_path=str(self._workflow_file(tmp, plain)))
            self.assertIn("__WEFT_PROMPT__", str(ctx.exception))

    def test_polling_times_out_with_korean_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = ComfyUIImage(workflow_path=str(self._workflow_file(tmp)), timeout=0, poll_interval=0)
            state = {"queued": [], "polls": {}, "views": []}
            with patch("weft.providers.comfyui_image.urlopen", _fake_comfy_server(state, ready_after=10**9)):
                with self.assertRaises(RuntimeError) as ctx:
                    provider.generate("p", n=1)
            self.assertIn("끝나지 않았습니다", str(ctx.exception))

    def test_from_env_reads_url_workflow_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflow = self._workflow_file(tmp)
            env = {
                "COMFYUI_URL": "http://localhost:9999/",
                "COMFYUI_WORKFLOW": str(workflow),
                "COMFYUI_TIMEOUT": "12",
            }
            with patch.dict(os.environ, env, clear=False):
                provider = ComfyUIImage.from_env()
            self.assertEqual("http://localhost:9999", provider.url)  # trailing slash stripped
            self.assertEqual(12.0, provider.timeout)

            with patch.dict(os.environ, {**env, "COMFYUI_TIMEOUT": "soon"}, clear=False):
                with self.assertRaises(RuntimeError) as ctx:
                    ComfyUIImage.from_env()
            self.assertIn("COMFYUI_TIMEOUT", str(ctx.exception))


class RegistryTest(unittest.TestCase):
    def test_create_image_provider_dispatches_comfyui_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflow = Path(tmp) / "wf.json"
            workflow.write_text(json.dumps(WORKFLOW), encoding="utf-8")
            with patch.dict(os.environ, {"COMFYUI_WORKFLOW": str(workflow), "COMFYUI_URL": "", "COMFYUI_TIMEOUT": ""}, clear=False):
                bundle = create_image_provider(provider_name="comfyui")
        self.assertIsInstance(bundle.provider, ComfyUIImage)
        self.assertEqual("comfyui", bundle.metadata["provider"])
        self.assertEqual("wf.json", bundle.metadata["model"])

    def test_create_image_provider_stub_needs_no_keys(self) -> None:
        bundle = create_image_provider(provider_name="stub", size="640x360", quality="low")
        self.assertEqual("stub", bundle.metadata["provider"])
        self.assertEqual("640x360", bundle.metadata["size"])

    def test_unknown_image_provider_lists_supported_names(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            create_image_provider(provider_name="dalle")
        message = str(ctx.exception)
        for name in ("openai", "gemini", "comfyui", "stub"):
            self.assertIn(name, message)

    def test_image_provider_label_resolves_without_api_keys(self) -> None:
        env = {"OPENAI_IMAGE_MODEL": "", "GEMINI_IMAGE_MODEL": "", "COMFYUI_WORKFLOW": "/x/wf_api.json"}
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual("gpt-image-2", image_provider_label("openai"))  # 기본값 = 현행 플래그십
            self.assertEqual("gemini-3.1-flash-image", image_provider_label("gemini"))
            self.assertEqual("wf_api.json", image_provider_label("comfyui"))
            self.assertEqual("stub-image", image_provider_label("stub"))
        with self.assertRaises(RuntimeError):
            image_provider_label("dalle")

    def test_image_provider_options_lists_models_and_defaults(self) -> None:
        env = {
            "IMAGE_PROVIDER": "gemini",
            "OPENAI_IMAGE_MODEL": "",
            "GEMINI_IMAGE_MODEL": "gemini-3-pro-image",
            "IMAGE_ASPECT": "",
        }
        with patch.dict(os.environ, env, clear=False):
            options = image_provider_options()
        self.assertEqual("gemini", options["provider"])
        self.assertEqual("gemini-3-pro-image", options["model"])
        self.assertEqual("16:9", options["aspect"])
        by_name = {p["name"]: p for p in options["providers"]}
        self.assertEqual({"openai", "gemini", "comfyui", "stub"}, set(by_name))
        # env default first, no duplicates
        self.assertEqual("gemini-3-pro-image", by_name["gemini"]["models"][0])
        self.assertEqual(1, by_name["gemini"]["models"].count("gemini-3-pro-image"))
        self.assertIn("gpt-image-1-mini", by_name["openai"]["models"])
        self.assertIn("gpt-image-2", by_name["openai"]["models"])

    def test_create_tts_provider_stub_and_unknown(self) -> None:
        bundle = create_tts_provider(provider_name="stub")
        self.assertEqual("stub", bundle.metadata["provider"])
        self.assertEqual("stub", bundle.metadata["voice_id"])
        with self.assertRaises(RuntimeError):
            create_tts_provider(provider_name="acme")

    def test_typecast_provider_env_moved_into_from_env(self) -> None:
        env = {
            "TYPECAST_API_KEY": "k",
            "TYPECAST_VOICE": "v",
            "TYPECAST_MODEL": "ssfm-v99",
            "TYPECAST_LANGUAGE": "eng",
            "TYPECAST_EMOTION": "happy",
        }
        with patch.dict(os.environ, env, clear=False):
            bundle = create_tts_provider(provider_name="typecast")
        self.assertEqual("typecast", bundle.metadata["provider"])
        self.assertEqual("v", bundle.metadata["voice_id"])
        self.assertEqual("ssfm-v99", bundle.metadata["model"])
        self.assertEqual("eng", bundle.metadata["language"])
        self.assertEqual("happy", bundle.metadata["emotion"])


class LegacyImageLayoutTest(unittest.TestCase):
    """Old projects generated into ``images/openai`` must keep working untouched."""

    def test_subdir_constants(self) -> None:
        self.assertEqual("images/gen", GEN_IMG_SUBDIR)
        self.assertIn("images/openai", LEGACY_IMG_SUBDIRS)
        self.assertEqual(GEN_IMG_SUBDIR, IMG_SUBDIRS[0])  # read order: new layout first

    def _legacy_project(self, root: Path) -> Path:
        (root / "VISUALS.json").write_text(
            json.dumps({"schema": "weft-visual-v1", "shots": [{"id": "s01", "source_kind": "image", "prompt": "p"}]}),
            encoding="utf-8",
        )
        (root / "NARRATION.json").write_text(
            json.dumps({"schema": "weft-narration-v1", "beats": [{"id": "b001", "kind": "narration", "text": "hi"}]}),
            encoding="utf-8",
        )
        legacy_dir = root / "SHOTS" / "s01" / "images" / "openai"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "candidate_001.png").write_bytes(b"old-1")
        (legacy_dir / "candidate_002.png").write_bytes(b"old-2")
        (legacy_dir / ".key").write_text("same-key", encoding="utf-8")
        return legacy_dir

    def test_generate_images_cache_hits_legacy_layout_and_keeps_legacy_pick(self) -> None:
        class FakeImageProvider:
            def __init__(self, **_kwargs) -> None:
                pass

            def cache_key(self, _prompt: str) -> str:
                return "same-key"

            def generate(self, _prompt: str, n: int = 2) -> list[bytes]:
                raise AssertionError("cache hit expected — generate must not be called")

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"IMAGE_PROVIDER": "openai", "OPENAI_API_KEY": "test"}, clear=False
        ):
            root = Path(tmp)
            self._legacy_project(root)
            (root / "PICKS.json").write_text(
                json.dumps({"schema": "weft-picks-v1", "selections": {}, "auto_picked": [], "overridden": []}),
                encoding="utf-8",
            )

            with patch("weft.providers.registry.OpenAIImage", FakeImageProvider):
                summary = generate_images(root, n=2, recompile=False)

            self.assertEqual(0, summary["made"])
            self.assertEqual(1, summary["cached"])
            picks = json.loads((root / "PICKS.json").read_text(encoding="utf-8"))
            # auto pick points at the file that actually exists (legacy dir)
            self.assertEqual("images/openai/candidate_001.png", picks["selections"]["s01"])
            self.assertFalse((root / "SHOTS" / "s01" / "images" / "gen").exists())

    def test_picker_state_and_save_pick_recognize_legacy_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy_project(root)
            (root / "PICKS.json").write_text(
                json.dumps(
                    {
                        "schema": "weft-picks-v1",
                        "selections": {"s01": "images/openai/candidate_002.png"},
                        "auto_picked": [],
                        "overridden": ["s01"],
                    }
                ),
                encoding="utf-8",
            )

            state = build_state(root)
            self.assertEqual(GEN_IMG_SUBDIR, state["img_subdir"])  # new generations land here
            shot = state["shots"][0]
            self.assertEqual(["candidate_001.png", "candidate_002.png"], shot["candidates"])
            self.assertEqual("candidate_002.png", shot["pick"])  # legacy selection recognized
            self.assertEqual(
                "images/openai/candidate_001.png", shot["candidate_paths"]["candidate_001.png"]
            )

            # re-picking a legacy candidate keeps pointing at the existing file
            _save_pick(root, "s01", "candidate_001.png")
            picks = json.loads((root / "PICKS.json").read_text(encoding="utf-8"))
            self.assertEqual("images/openai/candidate_001.png", picks["selections"]["s01"])


class GeminiImageTest(unittest.TestCase):
    """Gemini REST provider — urllib is always mocked, never a real API call."""

    @staticmethod
    def _ok_response(image_bytes: bytes, requests_log: list) -> "Mock":
        def fake_urlopen(request, timeout=None):
            requests_log.append(request)
            payload = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "here you go"},
                                {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(image_bytes).decode("ascii")}},
                            ]
                        }
                    }
                ]
            }
            return _FakeResponse(json.dumps(payload).encode("utf-8"))

        return fake_urlopen

    def test_generate_requests_native_aspect_and_decodes_b64(self) -> None:
        requests_log: list = []
        provider = GeminiImage(api_key="k", model="gemini-3.1-flash-image", aspect="16:9", image_size="1K")
        with patch("weft.providers.gemini_image.urlopen", self._ok_response(b"PNGDATA", requests_log)):
            blobs = provider.generate("a cat", n=2)

        self.assertEqual([b"PNGDATA", b"PNGDATA"], blobs)
        self.assertEqual(2, len(requests_log))  # one generateContent call per candidate
        request = requests_log[0]
        self.assertIn("models/gemini-3.1-flash-image:generateContent", request.full_url)
        self.assertEqual("k", request.headers.get("X-goog-api-key"))
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual("a cat", body["contents"][0]["parts"][0]["text"])
        image_cfg = body["generationConfig"]["responseFormat"]["image"]
        self.assertEqual("16:9", image_cfg["aspectRatio"])  # 16:9 는 네이티브 지정
        self.assertEqual("1K", image_cfg["imageSize"])

    def test_25_flash_image_omits_image_size(self) -> None:
        requests_log: list = []
        provider = GeminiImage(api_key="k", model="gemini-2.5-flash-image", aspect="16:9")
        with patch("weft.providers.gemini_image.urlopen", self._ok_response(b"PNG", requests_log)):
            provider.generate("p", n=1)
        body = json.loads(requests_log[0].data.decode("utf-8"))
        self.assertNotIn("imageSize", body["generationConfig"]["responseFormat"]["image"])

    def test_reference_images_are_inlined(self) -> None:
        requests_log: list = []
        provider = GeminiImage(api_key="k")
        with patch("weft.providers.gemini_image.urlopen", self._ok_response(b"PNG", requests_log)):
            provider.generate("with char", n=1, references=[b"SHEET"])
        parts = json.loads(requests_log[0].data.decode("utf-8"))["contents"][0]["parts"]
        self.assertEqual(2, len(parts))
        self.assertEqual(base64.b64encode(b"SHEET").decode("ascii"), parts[1]["inline_data"]["data"])
        self.assertEqual("image/png", parts[1]["inline_data"]["mime_type"])

    def test_missing_key_raises_korean_error(self) -> None:
        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                GeminiImage.from_env()
        self.assertIn("GEMINI_API_KEY", str(ctx.exception))
        self.assertIn("비어 있습니다", str(ctx.exception))

    def test_from_env_prefers_google_api_key_and_reads_model(self) -> None:
        env = {
            "GOOGLE_API_KEY": "google-key",
            "GEMINI_API_KEY": "gemini-key",
            "GEMINI_IMAGE_MODEL": "gemini-3-pro-image",
            "IMAGE_ASPECT": "9:16",
            "GEMINI_IMAGE_SIZE": "2K",
        }
        with patch.dict(os.environ, env, clear=False):
            provider = GeminiImage.from_env()
        self.assertEqual("google-key", provider.api_key)  # SDK 규칙: GOOGLE_API_KEY 우선
        self.assertEqual("gemini-3-pro-image", provider.model)
        self.assertEqual("9:16", provider.aspect)
        self.assertEqual("2K", provider.image_size)

    def test_http_error_raises_korean_error(self) -> None:
        def fail(request, timeout=None):
            raise HTTPError(request.full_url, 403, "forbidden", None, io.BytesIO(b"denied"))

        provider = GeminiImage(api_key="bad")
        with patch("weft.providers.gemini_image.urlopen", fail):
            with self.assertRaises(RuntimeError) as ctx:
                provider.generate("p", n=1)
        message = str(ctx.exception)
        self.assertIn("HTTP 403", message)
        self.assertIn("GEMINI_API_KEY", message)

    def test_400_falls_back_to_legacy_image_config_shape(self) -> None:
        requests_log: list = []
        ok = self._ok_response(b"PNG", requests_log)

        def fake_urlopen(request, timeout=None):
            body = json.loads(request.data.decode("utf-8"))
            if "responseFormat" in body["generationConfig"]:
                raise HTTPError(request.full_url, 400, "bad", None, io.BytesIO(b"unknown field responseFormat"))
            return ok(request, timeout)

        provider = GeminiImage(api_key="k", aspect="16:9")
        with patch("weft.providers.gemini_image.urlopen", fake_urlopen):
            blobs = provider.generate("p", n=1)
        self.assertEqual([b"PNG"], blobs)
        legacy_body = json.loads(requests_log[0].data.decode("utf-8"))
        self.assertEqual("16:9", legacy_body["generationConfig"]["imageConfig"]["aspectRatio"])

    def test_connection_error_raises_korean_error(self) -> None:
        provider = GeminiImage(api_key="k")
        with patch("weft.providers.gemini_image.urlopen", side_effect=URLError("no route")):
            with self.assertRaises(RuntimeError) as ctx:
                provider.generate("p", n=1)
        self.assertIn("연결할 수 없습니다", str(ctx.exception))

    def test_blocked_response_without_image_raises_korean_error(self) -> None:
        def fake_urlopen(request, timeout=None):
            return _FakeResponse(json.dumps({"candidates": [{"content": {"parts": [{"text": "no"}]}}]}).encode("utf-8"))

        provider = GeminiImage(api_key="k")
        with patch("weft.providers.gemini_image.urlopen", fake_urlopen):
            with self.assertRaises(RuntimeError) as ctx:
                provider.generate("p", n=1)
        self.assertIn("이미지가 없습니다", str(ctx.exception))

    def test_cache_key_differs_per_model_and_aspect(self) -> None:
        base = GeminiImage(api_key="k", model="gemini-3.1-flash-image", aspect="16:9")
        other_model = GeminiImage(api_key="k", model="gemini-3-pro-image", aspect="16:9")
        other_aspect = GeminiImage(api_key="k", model="gemini-3.1-flash-image", aspect="9:16")
        self.assertNotEqual(base.cache_key("p"), other_model.cache_key("p"))
        self.assertNotEqual(base.cache_key("p"), other_aspect.cache_key("p"))


class OpenAIImageModelTest(unittest.TestCase):
    def test_default_model_is_flagship_with_native_aspect_size(self) -> None:
        provider = OpenAIImage(api_key="k")
        self.assertEqual("gpt-image-2", provider.model)
        self.assertEqual("1920x1088", provider.size)  # 16의 배수 제약 → 저장 시 1920x1080 크롭

        portrait = OpenAIImage(api_key="k", aspect="9:16")
        self.assertEqual("1088x1920", portrait.size)

        legacy = OpenAIImage(api_key="k", model="gpt-image-1-mini", aspect="16:9")
        self.assertEqual("1536x1024", legacy.size)  # 구모델: 16:9 네이티브 없음 → 최근접 후 크롭
        legacy_portrait = OpenAIImage(api_key="k", model="gpt-image-1-mini", aspect="9:16")
        self.assertEqual("1024x1536", legacy_portrait.size)

    def test_explicit_size_wins_over_aspect(self) -> None:
        provider = OpenAIImage(api_key="k", size="1024x1024", aspect="16:9")
        self.assertEqual("1024x1024", provider.size)

    def test_cache_key_includes_model(self) -> None:
        mini = OpenAIImage(api_key="k", model="gpt-image-1-mini")
        flagship = OpenAIImage(api_key="k", model="gpt-image-2")
        self.assertNotEqual(mini.cache_key("p"), flagship.cache_key("p"))

    def test_references_route_through_images_edit(self) -> None:
        provider = OpenAIImage(api_key="k", model="gpt-image-1-mini", aspect="16:9")
        client = Mock()
        item = Mock()
        item.b64_json = base64.b64encode(b"EDITED").decode("ascii")
        client.images.edit.return_value = Mock(data=[item])
        provider._client = client

        blobs = provider.generate("prompt with char", n=1, references=[b"SHEET"])

        self.assertEqual([b"EDITED"], blobs)
        client.images.generate.assert_not_called()
        kwargs = client.images.edit.call_args.kwargs
        self.assertEqual("gpt-image-1-mini", kwargs["model"])
        self.assertEqual("1536x1024", kwargs["size"])
        self.assertEqual(b"SHEET", kwargs["image"].read())


class AspectNormalizationTest(unittest.TestCase):
    def test_center_crop_to_exact_16_9(self) -> None:
        out = conform_to_aspect(_png_bytes(1536, 1024), "16:9")
        self.assertEqual((1536, 864), _png_size(out))  # openai 3:2 → 정확한 16:9

    def test_matching_image_passes_through_unchanged(self) -> None:
        blob = _png_bytes(1280, 720)
        self.assertIs(blob, conform_to_aspect(blob, "16:9"))  # 무변환 (re-encode 없음)

    def test_non_image_bytes_pass_through(self) -> None:
        self.assertEqual(b"not-a-png", conform_to_aspect(b"not-a-png", "16:9"))

    def test_invalid_aspect_raises_korean_error(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            conform_to_aspect(_png_bytes(64, 64), "4:3")
        self.assertIn("IMAGE_ASPECT", str(ctx.exception))

    def test_generate_images_stores_exact_aspect_for_any_provider_size(self) -> None:
        class OddSizeProvider:
            def __init__(self, **_kwargs) -> None:
                pass

            def cache_key(self, _prompt: str) -> str:
                return "odd-key"

            def generate(self, _prompt: str, n: int = 2) -> list[bytes]:
                return [_png_bytes(1376, 768) for _ in range(n)]  # gemini 1K-ish, not exact 16:9

        env = {"IMAGE_PROVIDER": "openai", "OPENAI_API_KEY": "t", "IMAGE_ASPECT": "16:9", "IMAGE_SIZE": ""}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env, clear=False):
            root = Path(tmp)
            (root / "VISUALS.json").write_text(
                json.dumps({"schema": "weft-visual-v1", "shots": [{"id": "s01", "source_kind": "image", "prompt": "p"}]}),
                encoding="utf-8",
            )
            (root / "PICKS.json").write_text(
                json.dumps({"schema": "weft-picks-v1", "selections": {}, "auto_picked": [], "overridden": []}),
                encoding="utf-8",
            )
            with patch("weft.providers.registry.OpenAIImage", OddSizeProvider):
                summary = generate_images(root, n=1, recompile=False)

            self.assertEqual(1, summary["made"])
            self.assertEqual("16:9", summary["aspect"])
            blob = (root / "SHOTS" / "s01" / GEN_IMG_SUBDIR / "candidate_001.png").read_bytes()
            width, height = _png_size(blob)
            self.assertEqual(width * 9, height * 16)  # 저장 시점에 정확히 16:9

    def test_stub_provider_generates_target_aspect_directly(self) -> None:
        bundle = create_image_provider(provider_name="stub", aspect="16:9")
        blob = bundle.provider.generate("hello", n=1)[0]
        width, height = _png_size(blob)
        self.assertEqual(width * 9, height * 16)


class CharacterSheetTest(unittest.TestCase):
    def _project(self, root: Path, prompt: str) -> None:
        (root / "VISUALS.json").write_text(
            json.dumps({"schema": "weft-visual-v1", "shots": [{"id": "s01", "source_kind": "image", "prompt": prompt}]}),
            encoding="utf-8",
        )
        (root / "PICKS.json").write_text(
            json.dumps({"schema": "weft-picks-v1", "selections": {}, "auto_picked": [], "overridden": []}),
            encoding="utf-8",
        )

    def test_find_character_sheet_checks_project_then_parent_and_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"CHARACTER_SHEET": ""}, clear=False):
            parent = Path(tmp)
            project = parent / "generated_project"
            project.mkdir()
            self.assertIsNone(find_character_sheet(project))
            (parent / "CHARACTER.png").write_bytes(b"parent-sheet")
            self.assertEqual(parent / "CHARACTER.png", find_character_sheet(project))
            (project / "CHARACTER.png").write_bytes(b"project-sheet")
            self.assertEqual(project / "CHARACTER.png", find_character_sheet(project))

            custom = parent / "hero.png"
            custom.write_bytes(b"custom")
            with patch.dict(os.environ, {"CHARACTER_SHEET": str(custom)}, clear=False):
                self.assertEqual(custom, find_character_sheet(project))
            with patch.dict(os.environ, {"CHARACTER_SHEET": str(parent / "absent.png")}, clear=False):
                with self.assertRaises(RuntimeError) as ctx:
                    find_character_sheet(project)
            self.assertIn("CHARACTER_SHEET", str(ctx.exception))

    def test_char_marker_passes_sheet_to_supporting_provider(self) -> None:
        captured: dict = {}

        class RefProvider:
            supports_reference_images = True

            def __init__(self, **_kwargs) -> None:
                pass

            def cache_key(self, prompt: str) -> str:
                captured.setdefault("key_payloads", []).append(prompt)
                return "ref-key"

            def generate(self, prompt: str, n: int = 2, references=None) -> list[bytes]:
                captured["prompt"] = prompt
                captured["references"] = references
                return [b"img" for _ in range(n)]

        env = {"IMAGE_PROVIDER": "openai", "OPENAI_API_KEY": "t", "CHARACTER_SHEET": ""}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env, clear=False):
            root = Path(tmp)
            self._project(root, "wide shot of @char waving")
            (root / "CHARACTER.png").write_bytes(b"SHEETBYTES")
            with patch("weft.providers.registry.OpenAIImage", RefProvider):
                generate_images(root, n=1, recompile=False)

        self.assertIn("the recurring channel character exactly as shown in the reference sheet", captured["prompt"])
        self.assertNotIn("@char", captured["prompt"])
        self.assertEqual([b"SHEETBYTES"], captured["references"])
        # 시트 내용이 캐시 키에 들어가 시트가 바뀌면 재생성된다
        self.assertTrue(any("[charsheet:" in payload for payload in captured["key_payloads"]))

    def test_char_marker_stripped_with_warning_when_unsupported_or_missing(self) -> None:
        captured: dict = {}

        class PlainProvider:  # no supports_reference_images
            def __init__(self, **_kwargs) -> None:
                pass

            def cache_key(self, _prompt: str) -> str:
                return "plain-key"

            def generate(self, prompt: str, n: int = 2) -> list[bytes]:
                captured["prompt"] = prompt
                return [b"img" for _ in range(n)]

        env = {"IMAGE_PROVIDER": "openai", "OPENAI_API_KEY": "t", "CHARACTER_SHEET": ""}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env, clear=False):
            root = Path(tmp)
            self._project(root, "wide shot of @char waving")
            with patch("weft.providers.registry.OpenAIImage", PlainProvider):
                with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    summary = generate_images(root, n=1, recompile=False)

        self.assertEqual(1, summary["made"])  # 마커 제거 후 정상 생성
        self.assertNotIn("@char", captured["prompt"])
        self.assertNotIn("reference sheet", captured["prompt"])
        self.assertIn("@char", stdout.getvalue())  # 1회 경고


class PickerGenerationOptionsTest(unittest.TestCase):
    def test_state_includes_provider_and_model_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"IMAGE_PROVIDER": "stub", "OPENAI_IMAGE_MODEL": ""}, clear=False
        ):
            root = Path(tmp)
            (root / "VISUALS.json").write_text(
                json.dumps({"schema": "weft-visual-v1", "shots": []}), encoding="utf-8"
            )
            (root / "NARRATION.json").write_text(
                json.dumps({"schema": "weft-narration-v1", "beats": []}), encoding="utf-8"
            )
            (root / "PICKS.json").write_text(
                json.dumps({"schema": "weft-picks-v1", "selections": {}, "auto_picked": [], "overridden": []}),
                encoding="utf-8",
            )
            state = build_state(root)

        generation = state["generation"]
        self.assertEqual("stub", generation["provider"])
        names = {p["name"] for p in generation["providers"]}
        self.assertEqual({"openai", "gemini", "comfyui", "stub"}, names)

    def test_append_candidates_accepts_provider_and_model_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"IMAGE_PROVIDER": "openai", "IMAGE_ASPECT": "16:9", "IMAGE_SIZE": ""}, clear=False
        ):
            root = Path(tmp)
            (root / "VISUALS.json").write_text(
                json.dumps({"schema": "weft-visual-v1", "shots": [{"id": "s01", "source_kind": "image", "prompt": "p"}]}),
                encoding="utf-8",
            )
            (root / "SHOTS" / "s01").mkdir(parents=True)
            # IMAGE_PROVIDER=openai 인데 키 없이도 stub override 로 생성된다
            result = append_candidates(root, "s01", n=1, provider_name="stub")

            self.assertEqual("stub", result["provider"])
            self.assertEqual(["candidate_001.png"], result["new"])
            blob = (root / "SHOTS" / "s01" / GEN_IMG_SUBDIR / "candidate_001.png").read_bytes()
            width, height = _png_size(blob)
            self.assertEqual(width * 9, height * 16)


if __name__ == "__main__":
    unittest.main()
