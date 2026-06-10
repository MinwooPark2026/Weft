from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weft.cli import main as cli_main
from weft.providers.env import load_env
from weft.settings import (
    SETTINGS_FILE,
    apply_project_settings,
    find_settings_file,
    parse_settings,
    setting_bool,
)


ROOT = Path(__file__).resolve().parents[1]

MINIMAL_CONTI = """# CLI Test

| beat | 시각(shot) | 시간 | 나레이션 (TTS) | 자막 | 모션·메모 |
|---|---|---|---|---|---|
| b001 | ▶ s01_clip | 0:00~0:02 | clip narration | clip subtitle | static |

| shot id | source_kind | cover | 모션 | 프롬프트 / 문구 |
|---|---|---|---|---|
| s01_clip | clip | b001 | static | CLIPS/intro.mp4 |
"""


def _clean_env():
    """patch.dict 스냅샷 안에서 WEFT_* 오버라이드 변수를 제거한다."""
    ctx = patch.dict(os.environ, {}, clear=False)
    ctx.start()
    os.environ.pop("WEFT_SETTINGS", None)
    os.environ.pop("WEFT_ENV", None)
    return ctx


class CliDryrunAliasTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = _clean_env()
        self.addCleanup(self._env.stop)

    def test_dryrun_alias_builds_same_project_as_conti(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conti = Path(tmp) / "CONTI.md"
            conti.write_text(MINIMAL_CONTI, encoding="utf-8")
            out_conti = Path(tmp) / "out_conti"
            out_dryrun = Path(tmp) / "out_dryrun"

            with patch("sys.stdout", io.StringIO()):
                rc1 = cli_main(["conti", str(conti), "--out", str(out_conti)])
                rc2 = cli_main(["dryrun", str(conti), "--out", str(out_dryrun)])

            self.assertEqual(0, rc1)
            self.assertEqual(0, rc2)  # alias must not die in argparse dispatch
            plan1 = (out_conti / "EXPORTS" / "render_plan.json").read_text(encoding="utf-8")
            plan2 = (out_dryrun / "EXPORTS" / "render_plan.json").read_text(encoding="utf-8")
            self.assertEqual(plan1, plan2)

    def test_dryrun_alias_missing_conti_is_friendly_rc2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                rc = cli_main(["dryrun", str(Path(tmp) / "CONTI.md")])
            self.assertEqual(2, rc)
            self.assertIn("가 없습니다", stderr.getvalue())

    def test_conti_missing_conti_is_friendly_rc2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                rc = cli_main(["conti", str(Path(tmp) / "CONTI.md")])
            self.assertEqual(2, rc)
            self.assertIn("가 없습니다", stderr.getvalue())


class CliMissingProjectFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = _clean_env()
        self.addCleanup(self._env.stop)

    def test_tts_on_empty_folder_says_run_conti_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                rc = cli_main(["tts", tmp])
            self.assertEqual(2, rc)
            self.assertIn("NARRATION.json", stderr.getvalue())
            self.assertIn("weft conti", stderr.getvalue())

    def test_images_on_empty_folder_says_run_conti_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                rc = cli_main(["images", tmp])
            self.assertEqual(2, rc)
            self.assertIn("VISUALS.json", stderr.getvalue())

    def test_ffmpeg_on_empty_folder_says_run_conti_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                rc = cli_main(["ffmpeg", tmp])
            self.assertEqual(2, rc)
            self.assertIn("render_plan.json", stderr.getvalue())

    def test_missing_folder_still_rc2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                rc = cli_main(["tts", str(Path(tmp) / "nope")])
            self.assertEqual(2, rc)
            self.assertIn("폴더가 없습니다", stderr.getvalue())


class CliFriendlyErrorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = _clean_env()
        self.addCleanup(self._env.stop)

    def test_runtime_error_prints_message_instead_of_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "NARRATION.json").write_text(
                json.dumps({"schema": "weft-narration-v1", "beats": []}), encoding="utf-8"
            )
            os.environ["TTS_PROVIDER"] = "bogus-provider"
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                rc = cli_main(["tts", tmp, "--no-recompile"])
            self.assertEqual(1, rc)
            self.assertIn("TTS_PROVIDER", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())


class CliSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = _clean_env()
        self.addCleanup(self._env.stop)

    def test_settings_creates_missing_directory_instead_of_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "no" / "such" / "dir"
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                rc = cli_main(["settings", str(target)])
            self.assertEqual(0, rc)
            self.assertTrue((target / SETTINGS_FILE).is_file())
            self.assertIn("FFMPEG_BIN", (target / SETTINGS_FILE).read_text(encoding="utf-8"))

    def test_weft_settings_env_pointing_to_missing_file_is_explicit_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["WEFT_SETTINGS"] = str(Path(tmp) / "nope.txt")
            with self.assertRaises(RuntimeError):
                find_settings_file(tmp)
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                rc = cli_main(["settings", tmp])
            self.assertEqual(1, rc)
            self.assertIn("WEFT_SETTINGS", stderr.getvalue())

    def test_parse_settings_warns_on_line_without_equals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / SETTINGS_FILE
            path.write_text("# comment\nBADLINE\nGOOD=1\n", encoding="utf-8")
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                settings = parse_settings(path)
            self.assertEqual({"GOOD": "1"}, settings)
            self.assertIn("BADLINE", stderr.getvalue())

    def test_setting_bool_empty_value_returns_default(self) -> None:
        self.assertTrue(setting_bool({"K": ""}, "K", True))
        self.assertFalse(setting_bool({"K": ""}, "K", False))
        self.assertFalse(setting_bool({"K": "false"}, "K", True))
        self.assertTrue(setting_bool({"K": "true"}, "K", False))


class EnvPrecedenceTest(unittest.TestCase):
    """우선순위: 셸 환경변수 > WEFT_SETTINGS.txt > .env (양쪽 로드 순서 모두)."""

    def setUp(self) -> None:
        self._env = _clean_env()
        self.addCleanup(self._env.stop)

    def test_shell_beats_settings_beats_dotenv_when_dotenv_loads_first(self) -> None:
        # weft tts/images 경로: load_env() 다음 apply_project_settings()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "WEFT_TEST_A1=dotenv\nWEFT_TEST_A2=dotenv\nWEFT_TEST_A3=dotenv\n", encoding="utf-8"
            )
            (root / SETTINGS_FILE).write_text(
                "WEFT_TEST_A1=settings\nWEFT_TEST_A2=settings\n", encoding="utf-8"
            )
            for key in ("WEFT_TEST_A1", "WEFT_TEST_A2", "WEFT_TEST_A3"):
                os.environ.pop(key, None)
            os.environ["WEFT_TEST_A1"] = "shell"  # 셸 export 시뮬레이션

            load_env(root / ".env")
            apply_project_settings(root)

            self.assertEqual("shell", os.environ["WEFT_TEST_A1"])  # 셸 > settings
            self.assertEqual("settings", os.environ["WEFT_TEST_A2"])  # settings > .env
            self.assertEqual("dotenv", os.environ["WEFT_TEST_A3"])  # .env 폴백

    def test_shell_beats_settings_beats_dotenv_when_settings_load_first(self) -> None:
        # weft all 경로: apply_project_settings() 다음 load_env()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "WEFT_TEST_B1=dotenv\nWEFT_TEST_B2=dotenv\nWEFT_TEST_B3=dotenv\n", encoding="utf-8"
            )
            (root / SETTINGS_FILE).write_text(
                "WEFT_TEST_B1=settings\nWEFT_TEST_B2=settings\n", encoding="utf-8"
            )
            for key in ("WEFT_TEST_B1", "WEFT_TEST_B2", "WEFT_TEST_B3"):
                os.environ.pop(key, None)
            os.environ["WEFT_TEST_B1"] = "shell"

            apply_project_settings(root)
            load_env(root / ".env")

            self.assertEqual("shell", os.environ["WEFT_TEST_B1"])
            self.assertEqual("settings", os.environ["WEFT_TEST_B2"])
            self.assertEqual("dotenv", os.environ["WEFT_TEST_B3"])


class CliAllPartialFailureTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = _clean_env()
        self.addCleanup(self._env.stop)

    def test_all_stops_when_tts_partially_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conti = Path(tmp) / "CONTI.md"
            conti.write_text(MINIMAL_CONTI, encoding="utf-8")
            failing = {"made": 0, "cached": 0, "failed": [{"beat_id": "b001", "error": "boom"}]}
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("weft.assets.generate_tts", return_value=failing) as tts:
                with patch("weft.assets.generate_images") as images:
                    with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                        rc = cli_main(["all", str(conti), "--out", str(Path(tmp) / "out")])

            self.assertEqual(1, rc)
            tts.assert_called_once()
            images.assert_not_called()  # 부분 실패 시 다음 단계로 진행하지 않는다
            self.assertIn("--allow-partial", stderr.getvalue())


class CliWhereIsSkillTest(unittest.TestCase):
    def test_json_payload_lists_skills_with_existing_paths(self) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            rc = cli_main(["whereisskill", "--json"])
        self.assertEqual(0, rc)
        payload = json.loads(stdout.getvalue())
        self.assertIn("script-to-conti", payload["skills"])
        claude_path = payload["skills"]["script-to-conti"]["claude"]
        self.assertTrue(Path(claude_path).is_file())

    def test_no_skill_files_print_guidance(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("weft.cli._skill_paths", return_value={}):
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                rc = cli_main(["whereisskill"])
        self.assertEqual(1, rc)
        self.assertIn("SKILL.md", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
