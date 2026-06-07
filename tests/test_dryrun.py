from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weft.cli import _default_capcut_folder, main as cli_main
from weft.assets import DEFAULT_STYLE, generate_images, load_style
from weft.compiler import compile_render_plan
from weft.exporters.capcut_draft import build_capcut_draft
from weft.parser import parse_conti
from weft.picker.server import _save_pick
from weft.validate import validate_project
from weft.writer import write_project


ROOT = Path(__file__).resolve().parents[1]


class DryRunTest(unittest.TestCase):
    def test_parse_validate_and_compile_existing_conti(self) -> None:
        project = parse_conti(ROOT / "example" / "CONTI.md")
        violations = validate_project(project)
        self.assertEqual([], violations)

        shots = {shot["id"]: shot for shot in project["visuals"]["shots"]}
        self.assertIn("s_reuse_b091_s44_see_and_read", shots)
        self.assertEqual("s44_see_and_read", shots["s_reuse_b091_s44_see_and_read"]["reuse_of"])
        self.assertEqual({"from": "b008", "to": "b009"}, shots["s09_gtx580"]["cover"])
        self.assertEqual({"from": "b013", "to": "b014"}, shots["s_title"]["cover"])

        plan = compile_render_plan(project)
        self.assertEqual(743.0, plan["total_seconds"])
        self.assertGreater(len(plan["video"]), 50)

    def test_write_project_materializes_assets_and_exports(self) -> None:
        project = parse_conti(ROOT / "example" / "CONTI.md")
        with tempfile.TemporaryDirectory() as tmp:
            result = write_project(project, tmp)
            self.assertEqual([], result["violations"])
            self.assertTrue((Path(tmp) / "NARRATION.json").exists())
            self.assertTrue((Path(tmp) / "VISUALS.json").exists())
            self.assertTrue((Path(tmp) / "PICKS.json").exists())
            self.assertTrue((Path(tmp) / "EXPORTS" / "render_plan.json").exists())
            self.assertTrue((Path(tmp) / "EXPORTS" / "subtitles.srt").exists())
            self.assertTrue((Path(tmp) / "SHOTS" / "s01_rank_card" / "images" / "dryrun" / "candidate_001.svg").exists())

    def test_picker_override_removes_auto_picked_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "PICKS.json").write_text(
                json.dumps(
                    {
                        "schema": "weft-picks-v1",
                        "selections": {"s01": "images/openai/candidate_001.png"},
                        "auto_picked": ["s01", "s02"],
                        "overridden": [],
                    }
                ),
                encoding="utf-8",
            )
            _save_pick(root, "s01", "candidate_002.png")
            picks = json.loads((root / "PICKS.json").read_text(encoding="utf-8"))
            self.assertEqual("images/openai/candidate_002.png", picks["selections"]["s01"])
            self.assertEqual(["s02"], picks["auto_picked"])
            self.assertEqual(["s01"], picks["overridden"])

    def test_image_cache_regenerates_when_candidate_count_is_short(self) -> None:
        class FakeImageProvider:
            def __init__(self, **_kwargs) -> None:
                pass

            def cache_key(self, _prompt: str) -> str:
                return "same-key"

            def generate(self, _prompt: str, n: int = 2) -> list[bytes]:
                return [f"png-{idx}".encode("ascii") for idx in range(n)]

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=False):
            root = Path(tmp)
            (root / "VISUALS.json").write_text(
                json.dumps({"schema": "weft-visual-v1", "shots": [{"id": "s01", "source_kind": "image", "prompt": "p"}]}),
                encoding="utf-8",
            )
            (root / "PICKS.json").write_text(
                json.dumps({"schema": "weft-picks-v1", "selections": {}, "auto_picked": [], "overridden": []}),
                encoding="utf-8",
            )
            image_dir = root / "SHOTS" / "s01" / "images" / "openai"
            image_dir.mkdir(parents=True)
            (image_dir / ".key").write_text("same-key", encoding="utf-8")
            (image_dir / "candidate_001.png").write_bytes(b"old")

            with patch("weft.assets.OpenAIImage", FakeImageProvider):
                summary = generate_images(root, n=2, recompile=False)

            self.assertEqual(1, summary["made"])
            self.assertEqual(0, summary["cached"])
            self.assertTrue((image_dir / "candidate_002.png").exists())

    def test_generate_images_preserves_overridden_pick_provenance(self) -> None:
        class FakeImageProvider:
            def __init__(self, **_kwargs) -> None:
                pass

            def cache_key(self, _prompt: str) -> str:
                return "new-key"

            def generate(self, _prompt: str, n: int = 2) -> list[bytes]:
                return [f"png-{idx}".encode("ascii") for idx in range(n)]

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=False):
            root = Path(tmp)
            (root / "VISUALS.json").write_text(
                json.dumps({"schema": "weft-visual-v1", "shots": [{"id": "s01", "source_kind": "image", "prompt": "p"}]}),
                encoding="utf-8",
            )
            (root / "PICKS.json").write_text(
                json.dumps(
                    {
                        "schema": "weft-picks-v1",
                        "selections": {"s01": "images/openai/external_001.png"},
                        "auto_picked": [],
                        "overridden": ["s01"],
                    }
                ),
                encoding="utf-8",
            )
            image_dir = root / "SHOTS" / "s01" / "images" / "openai"
            image_dir.mkdir(parents=True)
            (image_dir / "external_001.png").write_bytes(b"external")

            with patch("weft.assets.OpenAIImage", FakeImageProvider):
                generate_images(root, n=2, recompile=False)

            picks = json.loads((root / "PICKS.json").read_text(encoding="utf-8"))
            self.assertEqual("images/openai/external_001.png", picks["selections"]["s01"])
            self.assertEqual([], picks["auto_picked"])
            self.assertEqual(["s01"], picks["overridden"])
            self.assertTrue((image_dir / "candidate_001.png").exists())

    def test_load_style_prefers_project_style_then_parent_style(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "project"
            generated = parent / "generated_project"
            generated.mkdir(parents=True)
            (parent / "STYLE.txt").write_text("Style: parent", encoding="utf-8")
            self.assertEqual("Style: parent", load_style(generated))
            (generated / "STYLE.txt").write_text("Style: generated", encoding="utf-8")
            self.assertEqual("Style: generated", load_style(generated))

    def test_load_style_materializes_default_style_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "project"
            generated = parent / "generated_project"
            generated.mkdir(parents=True)
            self.assertEqual(DEFAULT_STYLE, load_style(generated))
            self.assertEqual(DEFAULT_STYLE, (parent / "STYLE.txt").read_text(encoding="utf-8").strip())

    def test_load_style_materializes_default_inside_custom_project_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "custom-out"
            project_dir.mkdir()
            self.assertEqual(DEFAULT_STYLE, load_style(project_dir))
            self.assertEqual(DEFAULT_STYLE, (project_dir / "STYLE.txt").read_text(encoding="utf-8").strip())
            self.assertFalse((Path(tmp) / "STYLE.txt").exists())

    def test_capcut_existing_folder_is_archived_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            capcut_root = base / "capcut"
            skeleton = capcut_root / "empty"
            skeleton.mkdir(parents=True)
            (skeleton / "draft_info.json").write_text(json.dumps({"tracks": [], "materials": {}}), encoding="utf-8")
            (skeleton / "draft_meta_info.json").write_text("{}", encoding="utf-8")

            existing = capcut_root / "review"
            existing.mkdir()
            (existing / "marker.txt").write_text("keep me", encoding="utf-8")

            project_dir = base / "project"
            asset = project_dir / "SHOTS" / "s01" / "images" / "openai" / "candidate_001.png"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"not really a png")
            exports = project_dir / "EXPORTS"
            exports.mkdir(parents=True)
            (exports / "render_plan.json").write_text(
                json.dumps(
                    {
                        "sample_rate": 48000,
                        "total_samples": 48000,
                        "video": [
                            {
                                "shot_id": "s01",
                                "source_kind": "image",
                                "src": "SHOTS/s01/images/openai/candidate_001.png",
                                "start": 0,
                                "end": 48000,
                                "motion": {"type": "static"},
                            }
                        ],
                        "audio": [],
                        "subtitles": [],
                    }
                ),
                encoding="utf-8",
            )

            summary = build_capcut_draft(project_dir, folder_name="review", capcut_root=capcut_root, register=False)

            backup = Path(summary["backup"])
            self.assertTrue(backup.exists())
            self.assertEqual("keep me", (backup / "marker.txt").read_text(encoding="utf-8"))
            self.assertTrue((capcut_root / "review" / "draft_info.json").exists())

    def test_cli_default_capcut_folder_uses_project_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "episode-3" / "generated_project"
            project_dir.mkdir(parents=True)
            self.assertEqual("weft_episode-3", _default_capcut_folder(project_dir))

    def test_cli_capcut_skips_registration_when_capcut_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "episode-3" / "generated_project"
            project_dir.mkdir(parents=True)
            fake_summary = {"folder": "/fake/capcut/weft_episode-3", "registered": False}
            stdout = io.StringIO()
            with patch("weft.exporters.capcut_draft.capcut_running", return_value=True):
                with patch("weft.exporters.capcut_draft.build_capcut_draft", return_value=fake_summary) as build:
                    with patch("sys.stdout", stdout):
                        rc = cli_main(["capcut", str(project_dir)])

            self.assertEqual(0, rc)
            build.assert_called_once()
            self.assertEqual("weft_episode-3", build.call_args.kwargs["folder_name"])
            self.assertFalse(build.call_args.kwargs["register"])
            out = json.loads(stdout.getvalue())
            self.assertTrue(out["capcut_running"])
            self.assertEqual("weft_episode-3", out["folder_name"])

    def test_agents_skill_mirrors_claude_skill(self) -> None:
        claude = ROOT / ".claude" / "skills" / "script-to-conti" / "SKILL.md"
        agents = ROOT / ".agents" / "skills" / "script-to-conti" / "SKILL.md"
        self.assertTrue(claude.is_file() and agents.is_file())
        self.assertEqual(
            claude.read_text(encoding="utf-8"),
            agents.read_text(encoding="utf-8"),
            ".agents mirror drifted from .claude — keep the two SKILL.md identical",
        )

    def test_cli_whereisskill_prints_skill_paths(self) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            rc = cli_main(["whereisskill"])

        self.assertEqual(0, rc)
        out = stdout.getvalue()
        self.assertIn("script-to-conti", out)
        self.assertIn(str(ROOT / ".agents" / "skills" / "script-to-conti" / "SKILL.md"), out)
        self.assertIn(str(ROOT / ".claude" / "skills" / "script-to-conti" / "SKILL.md"), out)

    def test_cli_whereisskill_json_is_machine_readable(self) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            rc = cli_main(["whereisskill", "--json"])

        self.assertEqual(0, rc)
        payload = json.loads(stdout.getvalue())
        self.assertEqual("script-to-conti", payload["skill"])
        self.assertEqual(
            str(ROOT / ".agents" / "skills" / "script-to-conti" / "SKILL.md"),
            payload["paths"]["codex"],
        )

    def test_typecast_payload_attaches_emotion_only_for_known_non_normal_preset(self) -> None:
        from weft.providers.typecast_tts import TypecastTTS

        neutral = TypecastTTS(api_key="k", voice_id="v")._payload("hi")
        self.assertNotIn("prompt", neutral)  # default 'normal' keeps the validated payload
        self.assertEqual("kor", neutral["language"])

        happy = TypecastTTS(api_key="k", voice_id="v", emotion="happy")._payload("hi")
        self.assertEqual(
            {"emotion_type": "preset", "emotion_preset": "happy", "emotion_intensity": 1.0},
            happy["prompt"],
        )

        bad = TypecastTTS(api_key="k", voice_id="v", emotion="excited")._payload("hi")
        self.assertNotIn("prompt", bad)  # unknown preset → neutral, never a mid-run 400


if __name__ == "__main__":
    unittest.main()
