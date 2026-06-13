from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from weft.animation import prepare_animation_shots
from weft.cli import _default_capcut_folder, main as cli_main
from weft.assets import DEFAULT_STYLE, generate_images, generate_tts, load_style
from weft.compiler import compile_render_plan
from weft.exporters.capcut_draft import build_capcut_draft
from weft.exporters.ffmpeg_render import render_ffmpeg
from weft.exporters.fcpxml import export_fcpxml
from weft.parser import parse_conti
from weft.picker.server import _generate_and_pick, _save_pick
from weft.validate import validate_project
from weft.writer import write_project


ROOT = Path(__file__).resolve().parents[1]


class DryRunTest(unittest.TestCase):
    def test_parse_validate_and_compile_existing_conti(self) -> None:
        project = parse_conti(ROOT / "example" / "CONTI.md")
        violations = validate_project(project)
        self.assertEqual([], [item for item in violations if item["severity"] == "error"])
        # 페이스 경고(I12)는 0~2건까지 허용, 그 외 warning은 없어야 한다.
        self.assertLessEqual(len([item for item in violations if item["invariant"] == "I12"]), 2)
        self.assertEqual([], [item for item in violations if item["invariant"] != "I12"])

        shots = {shot["id"]: shot for shot in project["visuals"]["shots"]}
        self.assertIn("s_reuse_b091_s44_see_and_read", shots)
        self.assertEqual("s44_see_and_read", shots["s_reuse_b091_s44_see_and_read"]["reuse_of"])
        self.assertEqual({"from": "b008", "to": "b008"}, shots["s09_gtx580"]["cover"])
        self.assertEqual({"from": "b013", "to": "b014"}, shots["s_title"]["cover"])

        plan = compile_render_plan(project)
        self.assertEqual(743.0, plan["total_seconds"])
        # 페이싱 재컷: 평균 장면 길이 5~7초 → 743초 기준 이벤트 100개 이상
        self.assertGreater(len(plan["video"]), 100)
        self.assertLessEqual(plan["total_seconds"] / len(plan["video"]), 7.0)

    def test_write_project_materializes_assets_and_exports(self) -> None:
        project = parse_conti(ROOT / "example" / "CONTI.md")
        with tempfile.TemporaryDirectory() as tmp:
            result = write_project(project, tmp)
            self.assertEqual([], [item for item in result["violations"] if item["severity"] == "error"])
            self.assertTrue((Path(tmp) / "NARRATION.json").exists())
            self.assertTrue((Path(tmp) / "VISUALS.json").exists())
            self.assertTrue((Path(tmp) / "PICKS.json").exists())
            self.assertTrue((Path(tmp) / "EXPORTS" / "render_plan.json").exists())
            self.assertTrue((Path(tmp) / "EXPORTS" / "subtitles.srt").exists())
            self.assertTrue((Path(tmp) / "SHOTS" / "s01_rank_board" / "images" / "dryrun" / "candidate_001.svg").exists())

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
            self.assertEqual("images/gen/candidate_002.png", picks["selections"]["s01"])
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
            # legacy layout fixture: regeneration must read it, then write to images/gen
            image_dir = root / "SHOTS" / "s01" / "images" / "openai"
            image_dir.mkdir(parents=True)
            (image_dir / ".key").write_text("same-key", encoding="utf-8")
            (image_dir / "candidate_001.png").write_bytes(b"old")

            with patch("weft.providers.registry.OpenAIImage", FakeImageProvider):
                summary = generate_images(root, n=2, recompile=False)

            self.assertEqual(1, summary["made"])
            self.assertEqual(0, summary["cached"])
            gen_dir = root / "SHOTS" / "s01" / "images" / "gen"
            self.assertTrue((gen_dir / "candidate_002.png").exists())
            self.assertFalse((image_dir / "candidate_001.png").exists())  # stale legacy auto candidate cleared

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
            # legacy-layout fallback: the externally added pick in images/openai
            # must survive a regeneration that writes new candidates to images/gen.
            image_dir = root / "SHOTS" / "s01" / "images" / "openai"
            image_dir.mkdir(parents=True)
            (image_dir / "external_001.png").write_bytes(b"external")

            with patch("weft.providers.registry.OpenAIImage", FakeImageProvider):
                generate_images(root, n=2, recompile=False)

            picks = json.loads((root / "PICKS.json").read_text(encoding="utf-8"))
            self.assertEqual("images/openai/external_001.png", picks["selections"]["s01"])
            self.assertEqual([], picks["auto_picked"])
            self.assertEqual(["s01"], picks["overridden"])
            self.assertTrue((image_dir / "external_001.png").exists())
            self.assertTrue((root / "SHOTS" / "s01" / "images" / "gen" / "candidate_001.png").exists())

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

    def test_cli_settings_materializes_default_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "video"
            project_dir.mkdir()
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                rc = cli_main(["settings", str(project_dir)])

            settings_path = project_dir / "WEFT_SETTINGS.txt"
            self.assertEqual(0, rc)
            self.assertTrue(settings_path.exists())
            self.assertIn("FFMPEG_CRF=20", settings_path.read_text(encoding="utf-8"))
            self.assertIn(f"settings={settings_path}", stdout.getvalue())

    def test_project_settings_drive_image_estimate_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=False):
            root = Path(tmp)
            (root / "WEFT_SETTINGS.txt").write_text(
                "IMAGE_PROVIDER=stub\nIMAGE_CANDIDATES_N=3\nIMAGE_QUALITY=low\nIMAGE_SIZE=1024x1024\n",
                encoding="utf-8",
            )
            (root / "VISUALS.json").write_text(
                json.dumps({"schema": "weft-visual-v1", "shots": [{"id": "s01", "source_kind": "image", "prompt": "p"}]}),
                encoding="utf-8",
            )

            summary = generate_images(root, estimate=True)

            self.assertEqual("stub", summary["provider"])
            self.assertEqual(3, summary["n"])
            self.assertEqual(3, summary["candidates"])
            self.assertEqual("low", summary["quality"])
            self.assertEqual("1024x1024", summary["size"])

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
            asset = project_dir / "SHOTS" / "s01" / "images" / "gen" / "candidate_001.png"
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
                                "src": "SHOTS/s01/images/gen/candidate_001.png",
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
            (project_dir / "EXPORTS").mkdir(parents=True)
            (project_dir / "EXPORTS" / "render_plan.json").write_text("{}", encoding="utf-8")
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

    def test_ffmpeg_dry_run_builds_motion_audio_and_subtitle_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "SHOTS" / "s01" / "images" / "gen" / "candidate_001.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            audio = root / "AUDIO" / "beats" / "b002.wav"
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b"wav")
            exports = root / "EXPORTS"
            exports.mkdir()
            (exports / "subtitles.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
                encoding="utf-8",
            )
            (exports / "render_plan.json").write_text(
                json.dumps(
                    {
                        "fps": 30,
                        "sample_rate": 48000,
                        "total_samples": 144000,
                        "total_seconds": 3.0,
                        "video": [
                            {
                                "shot_id": "s01",
                                "source_kind": "image",
                                "src": "SHOTS/s01/images/gen/candidate_001.png",
                                "start": 0,
                                "end": 144000,
                                "motion": {"type": "zoom_in"},
                            }
                        ],
                        "audio": [{"beat_id": "b002", "src": "AUDIO/beats/b002.wav", "start": 48000, "end": 96000}],
                        "subtitles": [{"beat_id": "b001", "start": 0, "end": 48000, "text": "hello"}],
                    }
                ),
                encoding="utf-8",
            )

            summary = render_ffmpeg(root, output=exports / "preview.mp4", encoder="libx264", width=1280, height=720, dry_run=True)

            command = summary["command"]
            filter_graph = command[command.index("-filter_complex") + 1]
            self.assertIn("scale=1280:720", filter_graph)
            self.assertIn("pad=1280:720", filter_graph)
            self.assertNotIn("force_original_aspect_ratio=increase", filter_graph)
            self.assertNotIn("crop=1280:720", filter_graph)
            self.assertIn("s=1280x720", filter_graph)
            self.assertIn("zoompan", filter_graph)
            # frame-grid snapping: 3.0s @ 30fps → exactly 90 frames, trimmed by frame
            # count (not seconds) so per-shot rounding cannot accumulate as A/V drift
            self.assertIn("d=90", filter_graph)
            self.assertIn("trim=end_frame=90", filter_graph)
            self.assertNotIn("trim=duration=", filter_graph)
            # subtitles: ONE libass burn-in instead of a per-subtitle overlay chain
            self.assertIn("subtitles=filename=", filter_graph)
            self.assertIn("subtitles.ass", filter_graph)
            self.assertNotIn("overlay=", filter_graph)
            # audio: ONE premixed wav input instead of per-beat -i + adelay + amix
            self.assertEqual(2, command.count("-i"))  # 1 video still + 1 audio mix
            self.assertIn(str(exports / "ffmpeg" / "audio_mix.wav"), command)
            self.assertNotIn("adelay", filter_graph)
            self.assertNotIn("amix", filter_graph)
            # loudnorm runs exactly once, over the whole mixed track
            self.assertEqual(1, filter_graph.count("loudnorm"))
            self.assertIn("loudnorm=I=-14:TP=-1.5:LRA=11", filter_graph)
            self.assertIn("apad,atrim=0:3.000", filter_graph)
            self.assertIn("libx264", command)
            self.assertEqual(str(exports / "preview.mp4"), summary["output"])
            self.assertEqual(1, summary["subtitle_events"])
            # dry_run must not materialize the .ass file nor the premixed wav
            self.assertFalse((exports / "ffmpeg" / "subtitles.ass").exists())
            self.assertFalse((exports / "ffmpeg" / "audio_mix.wav").exists())

    def test_fcpxml_export_includes_video_audio_and_subtitle_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "SHOTS" / "s01" / "images" / "gen" / "candidate_001.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            audio = root / "AUDIO" / "beats" / "b001.wav"
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b"wav")
            exports = root / "EXPORTS"
            exports.mkdir()
            (exports / "render_plan.json").write_text(
                json.dumps(
                    {
                        "fps": 30,
                        "sample_rate": 48000,
                        "total_samples": 48000,
                        "video": [
                            {
                                "shot_id": "s01",
                                "source_kind": "image",
                                "src": "SHOTS/s01/images/gen/candidate_001.png",
                                "start": 0,
                                "end": 48000,
                                "motion": {"type": "zoom_in"},
                            }
                        ],
                        "audio": [{"beat_id": "b001", "src": "AUDIO/beats/b001.wav", "start": 0, "end": 48000}],
                        "subtitles": [{"beat_id": "b001", "start": 0, "end": 48000, "text": "hello"}],
                    }
                ),
                encoding="utf-8",
            )

            summary = export_fcpxml(root)
            xml = Path(summary["output"]).read_text(encoding="utf-8")

            self.assertIn("<!DOCTYPE fcpxml>", xml)
            self.assertIn('lane="1"', xml)
            self.assertIn('lane="-1"', xml)
            self.assertIn("<title", xml)
            self.assertIn("Weft motion: zoom_in", xml)

            tree = ElementTree.fromstring(xml)
            # FCPXML 1.11: media location is a media-rep child, never an asset src attr
            assets = tree.findall("resources/asset")
            self.assertTrue(assets)
            for asset in assets:
                self.assertIsNone(asset.get("src"))
                rep = asset.find("media-rep")
                self.assertIsNotNone(rep)
                self.assertEqual("original-media", rep.get("kind"))
                self.assertTrue(rep.get("src", "").startswith("file://"))
            # every <title> must ref a declared effect resource
            effect = tree.find("resources/effect")
            self.assertIsNotNone(effect)
            self.assertEqual("Basic Title", effect.get("name"))
            title = tree.find(".//title")
            self.assertEqual(effect.get("id"), title.get("ref"))
            # text-style-def carries a nested text-style, not direct font attrs
            style_def = title.find("text-style-def")
            self.assertIsNotNone(style_def)
            self.assertIsNone(style_def.get("font"))
            nested = style_def.find("text-style")
            self.assertIsNotNone(nested)
            self.assertEqual("Apple SD Gothic Neo", nested.get("font"))
            # video/title clip times are frame-aligned rationals (numerator % 1600 == 0)
            clip = tree.find(".//asset-clip[@lane='1']")
            for value in (clip.get("offset"), clip.get("duration"), title.get("offset"), title.get("duration")):
                if value.endswith("s") and "/" in value:
                    numerator = int(value[:-1].split("/")[0])
                    self.assertEqual(0, numerator % 1600, f"frame-misaligned time: {value}")

    def test_clip_source_kind_compiles_without_pick(self) -> None:
        conti = """# Clip Test

| beat | 시각(shot) | 시간 | 나레이션 (TTS) | 자막 | 모션·메모 |
|---|---|---|---|---|---|
| b001 | ▶ s01_clip | 0:00~0:02 | clip narration | clip subtitle | static |

| shot id | source_kind | cover | 모션 | 프롬프트 / 문구 |
|---|---|---|---|---|
| s01_clip | clip | b001 | static | CLIPS/intro.mp4 |
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CONTI.md"
            path.write_text(conti, encoding="utf-8")
            project = parse_conti(path)
            result = write_project(project, Path(tmp) / "generated_project")

            self.assertEqual([], result["violations"])
            self.assertEqual({}, result["picks"]["selections"])
            video = result["render_plan"]["video"][0]
            self.assertEqual("clip", video["source_kind"])
            self.assertEqual("CLIPS/intro.mp4", video["src"])

    def test_remotion_source_kind_prepares_animation_clip(self) -> None:
        conti = """# Remotion Test

| beat | 시각(shot) | 시간 | 나레이션 (TTS) | 자막 | 모션·메모 |
|---|---|---|---|---|---|
| b001 | ▶ s01_chart | 0:00~0:02 | chart narration | chart subtitle | static |

| shot id | source_kind | cover | 모션 | 프롬프트 / 문구 |
|---|---|---|---|---|
| s01_chart | remotion | b001 | static | Animated bar chart from 1 to 3 |
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CONTI.md"
            path.write_text(conti, encoding="utf-8")
            project_dir = Path(tmp) / "generated_project"
            project = parse_conti(path)
            result = write_project(project, project_dir)

            self.assertEqual([], result["violations"])
            self.assertEqual({}, result["picks"]["selections"])
            video = result["render_plan"]["video"][0]
            self.assertEqual("remotion", video["source_kind"])
            self.assertEqual("SHOTS/s01_chart/rendered/clip.mp4", video["src"])

            summary = prepare_animation_shots(project_dir, check=True)
            self.assertEqual(1, summary["total"])
            self.assertEqual(1, len(summary["pending"]))
            self.assertTrue((project_dir / "SHOTS" / "s01_chart" / "animation" / "SPEC.md").exists())

            output = project_dir / "SHOTS" / "s01_chart" / "rendered" / "clip.mp4"
            output.write_bytes(b"mp4")
            summary = prepare_animation_shots(project_dir, check=True, recompile=False)
            self.assertEqual(0, len(summary["pending"]))

    def test_ffmpeg_clip_event_uses_video_filter_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = root / "CLIPS" / "intro.mp4"
            clip.parent.mkdir()
            clip.write_bytes(b"mp4")
            exports = root / "EXPORTS"
            exports.mkdir()
            (exports / "render_plan.json").write_text(
                json.dumps(
                    {
                        "fps": 30,
                        "sample_rate": 48000,
                        "total_samples": 48000,
                        "total_seconds": 1.0,
                        "video": [
                            {
                                "shot_id": "s01",
                                "source_kind": "clip",
                                "src": "CLIPS/intro.mp4",
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

            summary = render_ffmpeg(root, dry_run=True, encoder="libx264")
            filter_graph = summary["command"][summary["command"].index("-filter_complex") + 1]
            self.assertIn("tpad=stop_mode=clone", filter_graph)
            self.assertNotIn("zoompan", filter_graph)

    def test_ffmpeg_remotion_event_uses_video_filter_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = root / "SHOTS" / "s01" / "rendered" / "clip.mp4"
            clip.parent.mkdir(parents=True)
            clip.write_bytes(b"mp4")
            exports = root / "EXPORTS"
            exports.mkdir()
            (exports / "render_plan.json").write_text(
                json.dumps(
                    {
                        "fps": 30,
                        "sample_rate": 48000,
                        "total_samples": 48000,
                        "total_seconds": 1.0,
                        "video": [
                            {
                                "shot_id": "s01",
                                "source_kind": "remotion",
                                "src": "SHOTS/s01/rendered/clip.mp4",
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

            summary = render_ffmpeg(root, dry_run=True, encoder="libx264")
            filter_graph = summary["command"][summary["command"].index("-filter_complex") + 1]
            self.assertIn("tpad=stop_mode=clone", filter_graph)
            self.assertNotIn("zoompan", filter_graph)

    def test_ffmpeg_auto_encoder_falls_back_when_videotoolbox_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "SHOTS" / "s01" / "images" / "gen" / "candidate_001.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            exports = root / "EXPORTS"
            exports.mkdir()
            (exports / "render_plan.json").write_text(
                json.dumps(
                    {
                        "fps": 30,
                        "sample_rate": 48000,
                        "total_samples": 48000,
                        "total_seconds": 1.0,
                        "video": [
                            {
                                "shot_id": "s01",
                                "source_kind": "image",
                                "src": "SHOTS/s01/images/gen/candidate_001.png",
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

            def fake_run(args: list[str], **_kwargs):
                if "-encoders" in args:
                    return subprocess.CompletedProcess(args, 0, stdout="h264_videotoolbox", stderr="")
                if "h264_videotoolbox" in args:
                    raise subprocess.CalledProcessError(187, args, stderr="videotoolbox failed")
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            with patch("weft.exporters.ffmpeg_render.subprocess.run", side_effect=fake_run):
                summary = render_ffmpeg(root)

            self.assertEqual("libx264", summary["encoder"])
            self.assertEqual("h264_videotoolbox", summary["encoder_fallback_from"])

    def test_stub_providers_work_without_api_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"IMAGE_PROVIDER": "stub", "TTS_PROVIDER": "stub", "OPENAI_API_KEY": "", "TYPECAST_API_KEY": "", "TYPECAST_VOICE": ""},
            clear=False,
        ):
            root = Path(tmp)
            (root / "NARRATION.json").write_text(
                json.dumps({"schema": "weft-narration-v1", "beats": [{"id": "b001", "kind": "narration", "text": "hello", "duration": 1.0}]}),
                encoding="utf-8",
            )
            (root / "VISUALS.json").write_text(
                json.dumps({"schema": "weft-visual-v1", "shots": [{"id": "s01", "source_kind": "image", "prompt": "p"}]}),
                encoding="utf-8",
            )
            (root / "PICKS.json").write_text(
                json.dumps({"schema": "weft-picks-v1", "selections": {}, "auto_picked": [], "overridden": []}),
                encoding="utf-8",
            )
            (root / "project.json").write_text(json.dumps({"schema": "weft-project-v1", "fps": 30, "sample_rate": 48000}), encoding="utf-8")

            tts_summary = generate_tts(root, recompile=False)
            image_summary = generate_images(root, n=1, recompile=False)

            self.assertEqual("stub", tts_summary["provider"])
            self.assertEqual("stub", image_summary["provider"])
            self.assertTrue((root / "AUDIO" / "beats" / "b001.wav").exists())
            self.assertTrue((root / "SHOTS" / "s01" / "images" / "gen" / "candidate_001.png").exists())

    def test_cli_ffmpeg_invokes_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "generated_project"
            (project_dir / "EXPORTS").mkdir(parents=True)
            (project_dir / "EXPORTS" / "render_plan.json").write_text("{}", encoding="utf-8")
            stdout = io.StringIO()
            fake_summary = {"kind": "ffmpeg", "output": "out.mp4"}
            with patch("weft.exporters.ffmpeg_render.render_ffmpeg", return_value=fake_summary) as render:
                with patch("sys.stdout", stdout):
                    rc = cli_main(["ffmpeg", str(project_dir), "--output", "out.mp4", "--no-subtitles", "--dry-run"])

            self.assertEqual(0, rc)
            render.assert_called_once()
            self.assertEqual(str(project_dir), render.call_args.args[0])
            self.assertEqual("out.mp4", render.call_args.kwargs["output"])
            self.assertFalse(render.call_args.kwargs["with_subtitles"])
            self.assertTrue(render.call_args.kwargs["dry_run"])
            self.assertEqual(fake_summary, json.loads(stdout.getvalue()))

    def test_cli_ffmpeg_reads_project_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "episode"
            project_dir = parent / "generated_project"
            (project_dir / "EXPORTS").mkdir(parents=True)
            (project_dir / "EXPORTS" / "render_plan.json").write_text("{}", encoding="utf-8")
            (parent / "WEFT_SETTINGS.txt").write_text(
                "FFMPEG_ENCODER=libx264\nFFMPEG_WIDTH=1280\nFFMPEG_HEIGHT=720\nFFMPEG_CRF=34\nFFMPEG_BITRATE=1M\nFFMPEG_NO_SUBTITLES=true\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()
            fake_summary = {"kind": "ffmpeg", "output": "out.mp4"}
            with patch("weft.exporters.ffmpeg_render.render_ffmpeg", return_value=fake_summary) as render:
                with patch("sys.stdout", stdout):
                    rc = cli_main(["ffmpeg", str(project_dir)])

            self.assertEqual(0, rc)
            self.assertEqual("libx264", render.call_args.kwargs["encoder"])
            self.assertEqual(1280, render.call_args.kwargs["width"])
            self.assertEqual(720, render.call_args.kwargs["height"])
            self.assertEqual(34, render.call_args.kwargs["crf"])
            self.assertEqual("1M", render.call_args.kwargs["bitrate"])
            self.assertFalse(render.call_args.kwargs["with_subtitles"])

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
        self.assertIn("script-to-conti", payload["skills"])
        self.assertEqual(
            str(ROOT / ".agents" / "skills" / "script-to-conti" / "SKILL.md"),
            payload["skills"]["script-to-conti"]["codex"],
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

    def test_append_candidates_uses_and_persists_edited_prompt(self) -> None:
        from weft.assets import append_candidates

        captured: dict[str, str] = {}

        class FakeImageProvider:
            def __init__(self, **_kwargs) -> None:
                pass

            def cache_key(self, _prompt: str) -> str:
                return "k"

            def generate(self, prompt: str, n: int = 2) -> list[bytes]:
                captured["prompt"] = prompt
                return [b"png" for _ in range(n)]

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=False):
            root = Path(tmp)
            (root / "VISUALS.json").write_text(
                json.dumps({"schema": "weft-visual-v1",
                            "shots": [{"id": "s01", "source_kind": "image", "prompt": "OLD subject"}]}),
                encoding="utf-8",
            )
            with patch("weft.providers.registry.OpenAIImage", FakeImageProvider):
                r = append_candidates(root, "s01", n=1, prompt="NEW subject")

            self.assertIn("NEW subject", captured["prompt"])   # generation used the edited prompt
            self.assertNotIn("OLD subject", captured["prompt"])
            self.assertEqual(["candidate_001.png"], r["new"])
            visuals = json.loads((root / "VISUALS.json").read_text(encoding="utf-8"))
            self.assertEqual("NEW subject", visuals["shots"][0]["prompt"])  # and persisted it

    def test_picker_generate_auto_picks_new_candidate(self) -> None:
        captured: dict[str, str] = {}

        class FakeImageProvider:
            def __init__(self, **_kwargs) -> None:
                pass

            def cache_key(self, _prompt: str) -> str:
                return "k"

            def generate(self, prompt: str, n: int = 2) -> list[bytes]:
                captured["prompt"] = prompt
                return [b"png" for _ in range(n)]

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=False):
            root = Path(tmp)
            (root / "VISUALS.json").write_text(
                json.dumps({"schema": "weft-visual-v1",
                            "shots": [{"id": "s01", "source_kind": "image", "prompt": "OLD subject"}]}),
                encoding="utf-8",
            )
            (root / "PICKS.json").write_text(
                json.dumps({"schema": "weft-picks-v1",
                            "selections": {"s01": "images/openai/candidate_000.png"},
                            "auto_picked": [], "overridden": ["s01"]}),
                encoding="utf-8",
            )

            with patch("weft.providers.registry.OpenAIImage", FakeImageProvider):
                result = _generate_and_pick(root, "s01", n=1, prompt="NEW subject")

            self.assertIn("NEW subject", captured["prompt"])
            self.assertEqual("candidate_001.png", result["pick"])
            picks = json.loads((root / "PICKS.json").read_text(encoding="utf-8"))
            self.assertEqual("images/gen/candidate_001.png", picks["selections"]["s01"])


if __name__ == "__main__":
    unittest.main()
