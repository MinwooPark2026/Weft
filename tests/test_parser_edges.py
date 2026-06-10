from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weft.compiler import compile_render_plan
from weft.markdown import parse_markdown_tables, split_markdown_row
from weft.parser import _extract_style_bible, _infer_anchor_texts, parse_conti
from weft.timecode import parse_time_range, parse_timecode
from weft.validate import validate_project


ROOT = Path(__file__).resolve().parents[1]

BEAT_HEADER = "| beat | 시각(shot) | 시간 | 나레이션 (TTS) | 자막 | 모션·메모 |\n|---|---|---|---|---|---|\n"
SHOT_HEADER = "| shot id | source_kind | cover | 모션 | 프롬프트 / 문구 |\n|---|---|---|---|---|\n"


def _parse_inline(beat_rows: str, shot_rows: str = "", preamble: str = "") -> dict:
    conti = f"# Edge Test\n\n{preamble}{BEAT_HEADER}{beat_rows}\n{SHOT_HEADER}{shot_rows}"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "CONTI.md"
        path.write_text(conti, encoding="utf-8")
        return parse_conti(path)


class MarkdownTableEdgeTest(unittest.TestCase):
    def test_split_markdown_row_supports_escaped_pipe(self) -> None:
        self.assertEqual(["a | b", "c"], split_markdown_row(r"| a \| b | c |"))

    def test_escaped_pipe_survives_into_beat_text(self) -> None:
        project = _parse_inline(
            "| b001 | ▶ s01 | 0:00~0:05 | 좌 \\| 우 비교 문장 | 자막 | static |\n",
            "| s01 | image | b001 | static | prompt |\n",
        )
        self.assertEqual("좌 | 우 비교 문장", project["narration"]["beats"][0]["text"])

    def test_mismatched_row_in_selected_table_raises_with_line(self) -> None:
        conti = (
            "# Edge Test\n\n"
            + BEAT_HEADER
            + "| b001 | ▶ s01 | 0:00~0:05 | 문장 | 자막 | static |\n"
            + "| b002 | ▶ s02 | 0:05~0:08 | 문장 | 깨진 | 행 | static |\n"
            + "\n"
            + SHOT_HEADER
            + "| s01 | image | b001 | static | prompt |\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CONTI.md"
            path.write_text(conti, encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                parse_conti(path)
        message = str(ctx.exception)
        self.assertIn("b002", message)
        self.assertIn("6행", message)  # 1-based line of the broken row
        self.assertIn("칸", message)

    def test_mismatch_in_unselected_table_does_not_raise(self) -> None:
        preamble = "| note | value |\n|---|---|\n| a | b | c |\n\n"
        project = _parse_inline(
            "| b001 | ▶ s01 | 0:00~0:05 | 문장 | 자막 | static |\n",
            "| s01 | image | b001 | static | prompt |\n",
            preamble=preamble,
        )
        self.assertEqual(1, len(project["narration"]["beats"]))

    def test_parse_markdown_tables_keeps_rows_after_mismatch(self) -> None:
        text = "| a | b |\n|---|---|\n| 1 | 2 | 3 |\n| 4 | 5 |\n"
        tables = parse_markdown_tables(text)
        self.assertEqual(1, len(tables))
        self.assertEqual([{"a": "4", "b": "5"}], tables[0]["rows"])
        self.assertEqual(1, len(tables[0]["mismatches"]))
        self.assertEqual(3, tables[0]["mismatches"][0]["line"])


class HeaderNormalizationTest(unittest.TestCase):
    def test_capitalized_headers_parse_without_keyerror(self) -> None:
        conti = (
            "# Edge Test\n\n"
            "| Beat | 시각(shot) | 시간 | 나레이션 (TTS) | 자막 | 모션·메모 |\n"
            "|---|---|---|---|---|---|\n"
            "| b001 | ▶ s01 | 0:00~0:05 | 문장 | 자막 | static |\n"
            "\n"
            "| Shot ID | source_kind | cover | 모션 | 프롬프트 / 문구 |\n"
            "|---|---|---|---|---|\n"
            "| s01 | image | b001 | static | prompt |\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CONTI.md"
            path.write_text(conti, encoding="utf-8")
            project = parse_conti(path)
        self.assertEqual("b001", project["narration"]["beats"][0]["id"])
        self.assertEqual("prompt", project["visuals"]["shots"][0]["prompt"])


class DeriveShotsEdgeTest(unittest.TestCase):
    def test_montage_referencing_existing_shot_synthesizes_reuse(self) -> None:
        project = _parse_inline(
            "| b001 | ▶ s01 | 0:00~0:05 | 첫 문장 | 자막1 | static |\n"
            "| b002 | ▦ s01 / s02 | 0:05~0:09 | 둘째 문장 | 하나 · 둘 | fast |\n",
            "| s01 | image | b001 | static | prompt one |\n"
            "| s02 | image | b002 | static | prompt two |\n",
        )
        shots = {shot["id"]: shot for shot in project["visuals"]["shots"]}
        self.assertEqual({"from": "b001", "to": "b001"}, shots["s01"]["cover"])
        synth = shots["s_mont_b002_0_s01"]
        self.assertEqual("reuse", synth["source_kind"])
        self.assertEqual("s01", synth["reuse_of"])
        self.assertEqual(0, synth["montage_slot"]["index"])
        self.assertEqual(1, shots["s02"]["montage_slot"]["index"])
        self.assertEqual([], validate_project(project))

    def test_hold_after_montage_raises_specific_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _parse_inline(
                "| b001 | ▦ s01 / s02 | 0:00~0:04 | 첫 문장 | 하나 · 둘 | fast |\n"
                "| b002 | ↓ | 0:04~0:08 | 둘째 문장 | 자막 | static |\n",
                "| s01 | image | b001 | static | p1 |\n"
                "| s02 | image | b001 | static | p2 |\n",
            )
        self.assertIn("b002", str(ctx.exception))

    def test_duplicate_beat_id_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _parse_inline(
                "| b001 | ▶ s01 | 0:00~0:05 | 첫 문장 | 자막 | static |\n"
                "| b001 | ▶ s02 | 0:05~0:08 | 둘째 문장 | 자막 | static |\n",
                "| s01 | image | b001 | static | p1 |\n"
                "| s02 | image | b001 | static | p2 |\n",
            )
        self.assertIn("b001", str(ctx.exception))

    def test_duplicate_shot_id_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _parse_inline(
                "| b001 | ▶ s01 | 0:00~0:05 | 첫 문장 | 자막 | static |\n",
                "| s01 | image | b001 | static | p1 |\n"
                "| s01 | image | b001 | static | p2 |\n",
            )
        self.assertIn("s01", str(ctx.exception))

    def test_variation_selector_does_not_pollute_shot_id(self) -> None:
        project = _parse_inline(
            "| b001 | ▶️ s01 | 0:00~0:05 | 문장 | 자막 | static |\n",
            "| s01 | image | b001 | static | prompt |\n",
        )
        self.assertEqual(["s01"], [shot["id"] for shot in project["visuals"]["shots"]])


class DurationAndTimecodeTest(unittest.TestCase):
    def test_duration_estimated_from_clean_text_not_raw(self) -> None:
        project = _parse_inline(
            "| b001 | ▶ s01 |  | *[차분하게]* 안녕하세요 다섯글자 | 자막 | static |\n",
            "| s01 | image | b001 | static | prompt |\n",
        )
        beat = project["narration"]["beats"][0]
        # clean text "안녕하세요다섯글자" = 9 chars / 4.8 cps
        self.assertAlmostEqual(9 / 4.8, beat["duration"], places=6)
        self.assertEqual("차분하게", beat["tone"])

    def test_pause_without_time_falls_back_to_fixed_duration(self) -> None:
        project = _parse_inline(
            "| b001 | ▶ s01 | 0:00~0:05 | 문장 | 자막 | static |\n"
            "| p001 | ⏸ |  | *(정적)* |  |  |\n",
            "| s01 | image | b001 | static | prompt |\n",
        )
        pause = project["narration"]["beats"][1]
        self.assertEqual("pause", pause["kind"])
        self.assertEqual(1.0, pause["duration"])

    def test_fullwidth_tilde_time_ranges_are_recognized(self) -> None:
        project = _parse_inline(
            "| b001 | ▶ s01 | 0:00～0:05 | 첫 문장 | 자막 | static |\n"
            "| b002 | ↓ | 0:05〜0:08 | 둘째 문장 | 자막 | static |\n",
            "| s01 | image | b001 | static | prompt |\n",
        )
        beats = project["narration"]["beats"]
        self.assertEqual(0.0, beats[0]["source_start"])
        self.assertEqual(5.0, beats[0]["duration"])
        self.assertEqual(5.0, beats[1]["source_start"])
        self.assertEqual(3.0, beats[1]["duration"])

    def test_invalid_timecode_raises_value_error_with_beat_id(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _parse_inline(
                "| b001 | ▶ s01 | 0:xx~0:05 | 문장 | 자막 | static |\n",
                "| s01 | image | b001 | static | prompt |\n",
            )
        message = str(ctx.exception)
        self.assertIn("b001", message)
        self.assertIn("invalid timecode", message)

    def test_parse_timecode_invalid_is_value_error(self) -> None:
        with self.assertRaises(ValueError):
            parse_timecode("0:xx")
        with self.assertRaises(ValueError):
            parse_time_range("0:00~0:zz")


class AnchorAndStyleBibleTest(unittest.TestCase):
    def test_anchor_texts_split_on_double_spaces(self) -> None:
        self.assertEqual(["고양이", "개", "배"], _infer_anchor_texts("고양이  개  배", 3))

    def test_style_bible_requires_label_line(self) -> None:
        self.assertEqual("", _extract_style_bible("이번 편의 스타일 바이블 언급은 생략: 아무거나"))
        self.assertEqual("warm palette", _extract_style_bible("> **스타일 바이블:** warm palette"))
        self.assertEqual("simple", _extract_style_bible("스타일 바이블: simple"))


class CompilerGapFillTest(unittest.TestCase):
    def test_trailing_pause_extends_last_video_event_to_total(self) -> None:
        project = _parse_inline(
            "| b001 | ▶ s01 | 0:00~0:05 | 문장 | 자막 | static |\n"
            "| p001 | ⏸ 2초 | 0:05~0:07 | *(정적)* |  |  |\n",
            "| s01 | image | b001 | static | prompt |\n",
        )
        plan = compile_render_plan(project)
        self.assertEqual(7.0, plan["total_seconds"])
        self.assertEqual(plan["total_samples"], plan["video"][-1]["end"])

    def test_leading_pause_pulls_first_video_event_to_zero(self) -> None:
        project = _parse_inline(
            "| p001 | ⏸ 2초 | 0:00~0:02 | *(정적)* |  |  |\n"
            "| b001 | ▶ s01 | 0:02~0:07 | 문장 | 자막 | static |\n",
            "| s01 | image | b001 | static | prompt |\n",
        )
        plan = compile_render_plan(project)
        self.assertEqual(0, plan["video"][0]["start"])
        self.assertEqual(plan["total_samples"], plan["video"][-1]["end"])


class ValidateEdgeTest(unittest.TestCase):
    def test_source_time_gap_emits_warning_not_error(self) -> None:
        project = _parse_inline(
            "| b001 | ▶ s01 | 0:00~0:05 | 첫 문장 | 자막 | static |\n"
            "| b002 | ↓ | 0:06~0:09 | 둘째 문장 | 자막 | static |\n",
            "| s01 | image | b001 | static | prompt |\n",
        )
        violations = validate_project(project)
        warnings = [item for item in violations if item["invariant"] == "I11"]
        self.assertEqual(1, len(warnings))
        self.assertEqual("warning", warnings[0]["severity"])
        self.assertEqual("b002", warnings[0]["where"])
        self.assertIn("b001", warnings[0]["fix_hint"])
        self.assertEqual([], [item for item in violations if item["severity"] == "error"])

    def test_continuous_source_times_emit_no_warning(self) -> None:
        project = _parse_inline(
            "| b001 | ▶ s01 | 0:00~0:05 | 첫 문장 | 자막 | static |\n"
            "| b002 | ↓ | 0:05~0:09 | 둘째 문장 | 자막 | static |\n",
            "| s01 | image | b001 | static | prompt |\n",
        )
        self.assertEqual([], validate_project(project))

    def test_i7_message_names_offending_shots(self) -> None:
        project = {
            "narration": {"beats": [{"id": "b001", "kind": "narration", "text": "t", "duration": 1.0}]},
            "visuals": {
                "shots": [
                    {
                        "id": "s01",
                        "cover": {"from": "b001", "to": "b001"},
                        "source_kind": "image",
                    }
                ]
            },
        }
        picks = {
            "selections": {"s01": "x.png", "s_ghost": "y.png"},
            "auto_picked": ["s01", "s_ghost"],
            "overridden": [],
        }
        violations = validate_project(project, picks)
        i7 = [item for item in violations if item["invariant"] == "I7"]
        self.assertEqual(1, len(i7))
        self.assertIn("s_ghost", i7[0]["fix_hint"])


class ExampleContiRegressionTest(unittest.TestCase):
    def test_example_conti_parses_identically(self) -> None:
        project = parse_conti(ROOT / "example" / "CONTI.md")
        self.assertEqual([], validate_project(project))
        plan = compile_render_plan(project)
        self.assertEqual(743.0, plan["total_seconds"])
        self.assertEqual(plan["total_samples"], plan["video"][-1]["end"])


if __name__ == "__main__":
    unittest.main()
