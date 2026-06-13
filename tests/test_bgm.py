from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weft.cli import main as cli_main
from weft.exporters.capcut_draft import build_capcut_draft
from weft.exporters.ffmpeg_render import (
    load_bgm_config,
    render_ffmpeg,
    resolve_bgm_segments,
)


def _write_render_plan(root: Path, *, total_seconds: float = 3.0, with_audio: bool = True) -> None:
    image = root / "SHOTS" / "s01" / "images" / "gen" / "candidate_001.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"png")
    audio_events = []
    if with_audio:
        audio = root / "AUDIO" / "beats" / "b001.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"wav")
        audio_events = [{"beat_id": "b001", "src": "AUDIO/beats/b001.wav", "start": 0, "end": 48000}]
    exports = root / "EXPORTS"
    exports.mkdir(exist_ok=True)
    total_samples = round(total_seconds * 48000)
    (exports / "render_plan.json").write_text(
        json.dumps(
            {
                "fps": 30,
                "sample_rate": 48000,
                "total_samples": total_samples,
                "total_seconds": total_seconds,
                "video": [
                    {
                        "shot_id": "s01",
                        "source_kind": "image",
                        "src": "SHOTS/s01/images/gen/candidate_001.png",
                        "start": 0,
                        "end": total_samples,
                        "motion": {"type": "static"},
                    }
                ],
                "audio": audio_events,
                "subtitles": [],
            }
        ),
        encoding="utf-8",
    )


def _bgm_entry(path: Path, **overrides) -> dict:
    entry = {"path": str(path), "from": None, "to": None, "gain_db": -16.0}
    entry.update(overrides)
    return entry


class BgmFilterGraphTest(unittest.TestCase):
    """dry-run 명령에 BGM 입력/덕킹/페이드 체인이 정확히 들어가는지 검증."""

    def _graph(self, summary: dict) -> str:
        command = summary["command"]
        return command[command.index("-filter_complex") + 1]

    def test_dry_run_with_bgm_builds_sidechain_duck_after_single_loudnorm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_render_plan(root)
            bgm_file = root / "bgm.mp3"
            bgm_file.write_bytes(b"mp3")

            summary = render_ffmpeg(
                root, dry_run=True, encoder="libx264", bgm=[_bgm_entry(bgm_file)]
            )

            command = summary["command"]
            graph = self._graph(summary)
            # BGM 입력은 -stream_loop -1 로 (짧은 곡 자동 반복), 입력 1개 추가
            self.assertIn("-stream_loop", command)
            self.assertEqual(3, command.count("-i"))  # 이미지 + 나레이션 premix + BGM
            self.assertIn(str(bgm_file), command)
            # 기본 음량 + 페이드 인/아웃 (3초 영상, 기본 페이드 2.0 → dur/2=1.5초로 클램프)
            self.assertIn("volume=-16dB", graph)
            self.assertIn("afade=t=in:st=0:d=1.500", graph)
            self.assertIn("afade=t=out:st=1.500:d=1.500", graph)
            # 덕킹: 나레이션 loudnorm 은 1회, 그 결과가 사이드체인 키 — loudnorm 이 덕킹 앞
            self.assertEqual(1, graph.count("loudnorm"))
            self.assertIn("asplit=2[nar][narsc]", graph)
            self.assertIn("sidechaincompress=threshold=0.019953:ratio=2.5:attack=20:release=400", graph)
            self.assertLess(graph.index("loudnorm"), graph.index("sidechaincompress"))
            # 최종 합성: 나레이션 우선 길이 + 단순 합산 + 기존 리미터 유지
            self.assertIn("amix=inputs=2:duration=first:normalize=0", graph)
            self.assertIn("alimiter=limit=0.95", graph)
            # 기존 A/V 그리드/패딩 보존
            self.assertIn("apad,atrim=0:3.000", graph)
            self.assertIn("trim=end_frame=90", graph)
            self.assertEqual(1, summary["bgm_tracks"])

    def test_duck_db_maps_to_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_render_plan(root)
            bgm_file = root / "bgm.mp3"
            bgm_file.write_bytes(b"mp3")

            # GR ≈ headroom*(1-1/R), headroom=20dB → duck 10dB 는 ratio 2
            summary = render_ffmpeg(
                root, dry_run=True, encoder="libx264", bgm=[_bgm_entry(bgm_file)], bgm_duck_db=-10.0
            )
            self.assertIn("ratio=2:", self._graph(summary))

    def test_dry_run_without_bgm_keeps_legacy_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_render_plan(root)

            summary = render_ffmpeg(root, dry_run=True, encoder="libx264")

            command = summary["command"]
            graph = self._graph(summary)
            self.assertNotIn("-stream_loop", command)
            self.assertEqual(2, command.count("-i"))
            self.assertNotIn("sidechaincompress", graph)
            self.assertNotIn("afade", graph)
            self.assertNotIn("amix", graph)
            self.assertNotIn("bgm_tracks", summary)
            # 미설정 시 기존 오디오 체인이 바이트 단위로 동일
            self.assertIn(
                "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,"
                "apad,atrim=0:3.000,alimiter=limit=0.95,asetpts=PTS-STARTPTS[aout]",
                graph,
            )

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_render_plan(root)
            bgm_file = root / "bgm.mp3"
            bgm_file.write_bytes(b"mp3")

            render_ffmpeg(root, dry_run=True, encoder="libx264", bgm=[_bgm_entry(bgm_file)])

            self.assertFalse((root / "EXPORTS" / "ffmpeg" / "audio_mix.wav").exists())
            self.assertFalse((root / "EXPORTS" / "weft_render.mp4").exists())

    def test_bgm_json_two_tracks_build_segment_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_render_plan(root, total_seconds=10.0)
            a = root / "a.mp3"
            b = root / "b.mp3"
            a.write_bytes(b"mp3")
            b.write_bytes(b"mp3")

            summary = render_ffmpeg(
                root,
                dry_run=True,
                encoder="libx264",
                bgm=[
                    _bgm_entry(a, **{"from": "0:00", "to": "0:06", "gain_db": -16.0}),
                    _bgm_entry(b, **{"from": "0:06", "to": "", "gain_db": -22.0}),
                ],
            )

            command = summary["command"]
            graph = self._graph(summary)
            self.assertEqual(2, command.count("-stream_loop"))
            # 곡별 구간: 6초 + 4초(to 빈값 = 영상 끝까지), 두 번째 곡은 6초 지점에서 시작
            self.assertIn("atrim=0:6.000", graph)
            self.assertIn("atrim=0:4.000", graph)
            self.assertIn("adelay=6000:all=1", graph)
            # 곡별 gain_db
            self.assertIn("volume=-16dB", graph)
            self.assertIn("volume=-22dB", graph)
            # 두 구간을 합쳐 한 BGM 트랙으로 만든 뒤 덕킹
            self.assertIn("amix=inputs=2:duration=longest:normalize=0[bgmall]", graph)
            self.assertIn("[bgmall][narsc]sidechaincompress", graph)
            # 마지막 곡은 영상 끝(구간 끝)에서 페이드아웃: dur 4초, fade 2초 → st=2
            self.assertIn("afade=t=out:st=2.000:d=2.000", graph)
            self.assertEqual(
                [
                    {"file": "a.mp3", "start": 0.0, "end": 6.0, "gain_db": -16.0},
                    {"file": "b.mp3", "start": 6.0, "end": 10.0, "gain_db": -22.0},
                ],
                summary["bgm"],
            )

    def test_bgm_without_narration_skips_ducking_but_keeps_bgm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_render_plan(root, with_audio=False)
            bgm_file = root / "bgm.mp3"
            bgm_file.write_bytes(b"mp3")

            summary = render_ffmpeg(
                root, dry_run=True, encoder="libx264", bgm=[_bgm_entry(bgm_file)]
            )

            graph = self._graph(summary)
            self.assertNotIn("sidechaincompress", graph)
            self.assertIn("anullsrc", graph)
            self.assertIn("volume=-16dB", graph)
            self.assertIn("amix=inputs=2:duration=first:normalize=0", graph)


class BgmConfigTest(unittest.TestCase):
    def test_bgm_json_overrides_bgm_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "json_track.mp3").write_bytes(b"mp3")
            (root / "settings_track.mp3").write_bytes(b"mp3")
            (root / "BGM.json").write_text(
                json.dumps([{"file": "json_track.mp3", "from": "0:10", "to": "1:00", "gain_db": -20}]),
                encoding="utf-8",
            )

            config = load_bgm_config(root, bgm_file="settings_track.mp3", default_gain_db=-16.0)

            self.assertEqual(1, len(config))
            self.assertEqual(str((root / "json_track.mp3").resolve()), config[0]["path"])
            self.assertEqual("0:10", config[0]["from"])
            self.assertEqual("1:00", config[0]["to"])
            self.assertEqual(-20.0, config[0]["gain_db"])

    def test_bgm_file_only_uses_default_gain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "track.m4a").write_bytes(b"m4a")

            config = load_bgm_config(root, bgm_file="track.m4a", default_gain_db=-18.0)

            self.assertEqual(
                [{"path": str((root / "track.m4a").resolve()), "from": None, "to": None, "gain_db": -18.0}],
                config,
            )

    def test_relative_path_resolves_against_base_dir_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "conti_home"
            project = Path(tmp) / "conti_home" / "generated_project"
            (base / "music").mkdir(parents=True)
            project.mkdir(parents=True)
            (base / "music" / "bgm.wav").write_bytes(b"wav")

            config = load_bgm_config(project, bgm_file="music/bgm.wav", base_dir=base)

            self.assertEqual(str((base / "music" / "bgm.wav").resolve()), config[0]["path"])

    def test_missing_file_raises_korean_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as ctx:
                load_bgm_config(Path(tmp), bgm_file="없는파일.mp3")
            self.assertIn("BGM 파일을 찾지 못했습니다", str(ctx.exception))

    def test_unsupported_extension_raises_korean_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "cover.png").write_bytes(b"png")
            with self.assertRaises(RuntimeError) as ctx:
                load_bgm_config(Path(tmp), bgm_file="cover.png")
            self.assertIn("지원하지 않는 BGM 파일 형식", str(ctx.exception))

    def test_unset_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual([], load_bgm_config(Path(tmp)))
            self.assertEqual([], load_bgm_config(Path(tmp), bgm_file=""))

    def test_bgm_json_requires_file_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "BGM.json").write_text(json.dumps([{"from": "0:00"}]), encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                load_bgm_config(root)
            self.assertIn('"file"', str(ctx.exception))

    def test_resolve_segments_clamps_and_validates(self) -> None:
        entries = [
            {"path": "/x/a.mp3", "from": None, "to": None, "gain_db": -16.0},
            {"path": "/x/b.mp3", "from": "0:30", "to": "99:00", "gain_db": -10.0},
        ]
        segments = resolve_bgm_segments(entries, 60.0)
        self.assertEqual((0.0, 60.0), (segments[0]["start"], segments[0]["end"]))
        self.assertEqual((30.0, 60.0), (segments[1]["start"], segments[1]["end"]))  # 영상 길이로 클램프

        with self.assertRaises(RuntimeError) as ctx:
            resolve_bgm_segments([{"path": "/x/a.mp3", "from": "2:00", "to": "", "gain_db": -16.0}], 60.0)
        self.assertIn("구간이 비어 있습니다", str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx:
            resolve_bgm_segments([{"path": "/x/a.mp3", "from": "abc", "to": "", "gain_db": -16.0}], 60.0)
        self.assertIn("분:초", str(ctx.exception))


class BgmCliTest(unittest.TestCase):
    def _project_with_settings(self, tmp: str, settings_lines: str) -> tuple[Path, Path]:
        parent = Path(tmp) / "episode"
        project_dir = parent / "generated_project"
        (project_dir / "EXPORTS").mkdir(parents=True)
        (project_dir / "EXPORTS" / "render_plan.json").write_text("{}", encoding="utf-8")
        (parent / "WEFT_SETTINGS.txt").write_text(settings_lines, encoding="utf-8")
        return parent, project_dir

    def test_cli_ffmpeg_passes_bgm_from_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent, project_dir = self._project_with_settings(
                tmp, "BGM_FILE=bgm.mp3\nBGM_GAIN_DB=-18\nBGM_DUCK_DB=-10\nBGM_FADE_SECONDS=1.5\n"
            )
            (parent / "bgm.mp3").write_bytes(b"mp3")
            fake_summary = {"kind": "ffmpeg", "output": "out.mp4"}
            with patch("weft.exporters.ffmpeg_render.render_ffmpeg", return_value=fake_summary) as render:
                with patch("sys.stdout", io.StringIO()):
                    rc = cli_main(["ffmpeg", str(project_dir), "--dry-run"])

            self.assertEqual(0, rc)
            kwargs = render.call_args.kwargs
            self.assertEqual(
                [{"path": str((parent / "bgm.mp3").resolve()), "from": None, "to": None, "gain_db": -18.0}],
                kwargs["bgm"],
            )
            self.assertEqual(1.5, kwargs["bgm_fade_seconds"])
            self.assertEqual(-10.0, kwargs["bgm_duck_db"])

    def test_cli_ffmpeg_no_bgm_flag_disables_configured_bgm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent, project_dir = self._project_with_settings(tmp, "BGM_FILE=bgm.mp3\n")
            (parent / "bgm.mp3").write_bytes(b"mp3")
            fake_summary = {"kind": "ffmpeg", "output": "out.mp4"}
            with patch("weft.exporters.ffmpeg_render.render_ffmpeg", return_value=fake_summary) as render:
                with patch("sys.stdout", io.StringIO()):
                    rc = cli_main(["ffmpeg", str(project_dir), "--dry-run", "--no-bgm"])

            self.assertEqual(0, rc)
            self.assertIsNone(render.call_args.kwargs["bgm"])

    def test_cli_ffmpeg_without_config_passes_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _parent, project_dir = self._project_with_settings(tmp, "FFMPEG_ENCODER=libx264\n")
            fake_summary = {"kind": "ffmpeg", "output": "out.mp4"}
            with patch("weft.exporters.ffmpeg_render.render_ffmpeg", return_value=fake_summary) as render:
                with patch("sys.stdout", io.StringIO()):
                    rc = cli_main(["ffmpeg", str(project_dir), "--dry-run"])

            self.assertEqual(0, rc)
            self.assertIsNone(render.call_args.kwargs["bgm"])

    def test_cli_ffmpeg_missing_bgm_file_prints_korean_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _parent, project_dir = self._project_with_settings(tmp, "BGM_FILE=ghost.mp3\n")
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                rc = cli_main(["ffmpeg", str(project_dir), "--dry-run"])

            self.assertEqual(1, rc)
            self.assertIn("BGM 파일을 찾지 못했습니다", stderr.getvalue())

    def test_cli_conti_seeds_bgm_json_next_to_conti(self) -> None:
        conti = """# BGM Seed Test

| beat | 시각(shot) | 시간 | 나레이션 (TTS) | 자막 | 모션·메모 |
|---|---|---|---|---|---|
| b001 | ▶ s01 | 0:00~0:02 | narration | subtitle | static |

| shot id | source_kind | cover | 모션 | 프롬프트 / 문구 |
|---|---|---|---|---|
| s01 | image | b001 | static | a calm landscape |
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CONTI.md").write_text(conti, encoding="utf-8")
            payload = json.dumps([{"file": "bgm.mp3", "from": "0:00", "to": ""}], ensure_ascii=False)
            (root / "BGM.json").write_text(payload, encoding="utf-8")
            out_dir = root / "generated_project"
            with patch("sys.stdout", io.StringIO()) as stdout:
                rc = cli_main(["conti", str(root / "CONTI.md"), "--out", str(out_dir)])

            self.assertEqual(0, rc)
            self.assertEqual(payload, (out_dir / "BGM.json").read_text(encoding="utf-8"))
            self.assertIn("bgm=BGM.json", stdout.getvalue())


class BgmCapcutTest(unittest.TestCase):
    def test_capcut_draft_places_bgm_on_extra_audio_track(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            capcut_root = base / "capcut"
            skeleton = capcut_root / "empty"
            skeleton.mkdir(parents=True)
            (skeleton / "draft_info.json").write_text(json.dumps({"tracks": [], "materials": {}}), encoding="utf-8")
            (skeleton / "draft_meta_info.json").write_text("{}", encoding="utf-8")

            project_dir = base / "project"
            asset = project_dir / "SHOTS" / "s01" / "images" / "gen" / "candidate_001.png"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"not really a png")
            bgm_file = base / "bgm.mp3"
            bgm_file.write_bytes(b"mp3")
            exports = project_dir / "EXPORTS"
            exports.mkdir(parents=True)
            (exports / "render_plan.json").write_text(
                json.dumps(
                    {
                        "sample_rate": 48000,
                        "total_samples": 480000,  # 10초
                        "video": [
                            {
                                "shot_id": "s01",
                                "source_kind": "image",
                                "src": "SHOTS/s01/images/gen/candidate_001.png",
                                "start": 0,
                                "end": 480000,
                                "motion": {"type": "static"},
                            }
                        ],
                        "audio": [],
                        "subtitles": [],
                    }
                ),
                encoding="utf-8",
            )

            summary = build_capcut_draft(
                project_dir,
                folder_name="review",
                capcut_root=capcut_root,
                register=False,
                bgm=[{"path": str(bgm_file), "from": "0:02", "to": "", "gain_db": -16.0}],
            )

            self.assertEqual(1, summary["bgm_segments"])
            info = json.loads((capcut_root / "review" / "draft_info.json").read_text(encoding="utf-8"))
            audio_tracks = [t for t in info["tracks"] if t["type"] == "audio"]
            self.assertEqual(1, len(audio_tracks))  # 나레이션 없음 → BGM 트랙 하나
            segment = audio_tracks[0]["segments"][0]
            self.assertEqual(2_000_000, segment["target_timerange"]["start"])
            self.assertEqual(8_000_000, segment["target_timerange"]["duration"])
            self.assertAlmostEqual(0.1585, segment["volume"], places=4)  # -16dB → 선형
            self.assertEqual(1, len(info["materials"]["audios"]))
            # 음원은 드래프트 materials 폴더로 복사된다
            self.assertTrue((capcut_root / "review" / "materials" / "bgm_bgm.mp3").is_file())


if __name__ == "__main__":
    unittest.main()
