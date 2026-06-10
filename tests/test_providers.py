"""Provider-layer tests: ComfyUI (mocked urllib — never a real server),
the registry dictionary, and the legacy ``images/openai`` layout fallback.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError

from weft.assets import GEN_IMG_SUBDIR, IMG_SUBDIRS, LEGACY_IMG_SUBDIRS, generate_images
from weft.picker.server import _save_pick, build_state
from weft.providers.comfyui_image import ComfyUIImage
from weft.providers.registry import (
    create_image_provider,
    create_tts_provider,
    image_provider_label,
)

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
        for name in ("openai", "comfyui", "stub"):
            self.assertIn(name, message)

    def test_image_provider_label_resolves_without_api_keys(self) -> None:
        with patch.dict(os.environ, {"OPENAI_IMAGE_MODEL": "", "COMFYUI_WORKFLOW": "/x/wf_api.json"}, clear=False):
            self.assertEqual("gpt-image-1", image_provider_label("openai"))
            self.assertEqual("wf_api.json", image_provider_label("comfyui"))
            self.assertEqual("stub-image", image_provider_label("stub"))
        with self.assertRaises(RuntimeError):
            image_provider_label("dalle")

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


if __name__ == "__main__":
    unittest.main()
