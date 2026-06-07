from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .timecode import format_clock, format_srt_time, samples_to_seconds, seconds_to_samples


def compile_render_plan(project: dict[str, Any], picks: dict[str, Any] | None = None) -> dict[str, Any]:
    fps = int(project["project"].get("fps", 30))
    sample_rate = int(project["project"].get("sample_rate", 48_000))
    beats = project["narration"]["beats"]
    shots = project["visuals"]["shots"]
    beat_times = _beat_times(beats, sample_rate)
    picks = picks or {"selections": {}}

    video = _compile_video(shots, beat_times, picks, sample_rate)
    audio = _compile_audio(beats, beat_times)
    subtitles = _compile_subtitles(beats, beat_times)
    total = beat_times[beats[-1]["id"]]["end"] if beats else 0

    return {
        "schema": "weft-render-plan-v1",
        "fps": fps,
        "sample_rate": sample_rate,
        "total_samples": total,
        "total_seconds": samples_to_seconds(total, sample_rate),
        "video": video,
        "audio": audio,
        "subtitles": subtitles,
    }


def write_exports(render_plan: dict[str, Any], output_dir: str | Path) -> None:
    output_dir = Path(output_dir)
    exports = output_dir / "EXPORTS"
    capcut = exports / "capcut"
    capcut.mkdir(parents=True, exist_ok=True)
    (exports / "render_plan.json").write_text(json.dumps(render_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (exports / "subtitles.srt").write_text(to_srt(render_plan), encoding="utf-8")
    _write_timeline_csv(render_plan, capcut / "timeline.csv")
    manifest = {
        "schema": "weft-export-manifest-v1",
        "total_seconds": render_plan["total_seconds"],
        "video_events": len(render_plan["video"]),
        "audio_events": len(render_plan["audio"]),
        "subtitle_events": len(render_plan["subtitles"]),
    }
    (exports / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def to_srt(render_plan: dict[str, Any]) -> str:
    sample_rate = int(render_plan["sample_rate"])
    blocks = []
    for index, sub in enumerate(render_plan["subtitles"], start=1):
        start = format_srt_time(int(sub["start"]), sample_rate)
        end = format_srt_time(int(sub["end"]), sample_rate)
        blocks.append(f"{index}\n{start} --> {end}\n{sub['text']}\n")
    return "\n".join(blocks)


def _beat_times(beats: list[dict[str, Any]], sample_rate: int) -> dict[str, dict[str, int]]:
    cursor = 0
    out: dict[str, dict[str, int]] = {}
    for beat in beats:
        duration = seconds_to_samples(beat.get("duration", 0), sample_rate)
        out[beat["id"]] = {"start": cursor, "end": cursor + duration}
        cursor += duration
    return out


def _compile_video(
    shots: list[dict[str, Any]],
    beat_times: dict[str, dict[str, int]],
    picks: dict[str, Any],
    sample_rate: int,
) -> list[dict[str, Any]]:
    raw_events: list[dict[str, Any]] = []
    for shot in shots:
        cover = shot["cover"]
        start = beat_times[cover["from"]]["start"]
        end = beat_times[cover["to"]]["end"]
        if shot.get("montage_slot"):
            slot = shot["montage_slot"]
            siblings = [s for s in shots if s.get("cover") == cover and s.get("montage_slot")]
            total_weight = sum(float(s["montage_slot"].get("weight", 1.0)) for s in siblings)
            offset = 0
            for sibling in sorted(siblings, key=lambda item: int(item["montage_slot"]["index"])):
                width = int((end - start) * float(sibling["montage_slot"].get("weight", 1.0)) / total_weight)
                if sibling["id"] == shot["id"]:
                    start = start + offset
                    end = start + width
                    break
                offset += width
            if int(slot["index"]) == int(slot["of"]) - 1:
                end = beat_times[cover["to"]]["end"]
        src = _source_for_shot(shot, picks)
        raw_events.append(
            {
                "shot_id": shot["id"],
                "source_kind": shot.get("source_kind", "image"),
                "src": src,
                "start": start,
                "end": end,
                "motion": shot.get("motion", {"type": "static"}),
                "reuse_of": shot.get("reuse_of"),
            }
        )

    raw_events.sort(key=lambda event: (event["start"], event["end"]))
    for index, event in enumerate(raw_events):
        if index + 1 < len(raw_events) and raw_events[index + 1]["start"] > event["end"]:
            event["end"] = raw_events[index + 1]["start"]
        event["start_seconds"] = samples_to_seconds(event["start"], sample_rate)
        event["end_seconds"] = samples_to_seconds(event["end"], sample_rate)
        event["start_clock"] = format_clock(event["start"], sample_rate)
        event["end_clock"] = format_clock(event["end"], sample_rate)
    return raw_events


def _source_for_shot(shot: dict[str, Any], picks: dict[str, Any]) -> str | None:
    if shot.get("source_kind") == "reuse":
        target = shot.get("reuse_of")
        rel = picks.get("selections", {}).get(target)
        return f"SHOTS/{target}/{rel}" if target and rel else None
    rel = picks.get("selections", {}).get(shot["id"])
    return f"SHOTS/{shot['id']}/{rel}" if rel else None


def _compile_audio(beats: list[dict[str, Any]], beat_times: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    audio = []
    for beat in beats:
        if beat.get("kind") != "narration" or not beat.get("text"):
            continue
        timing = beat_times[beat["id"]]
        audio.append(
            {
                "beat_id": beat["id"],
                "src": beat.get("audio") or f"AUDIO/beats/{beat['id']}.mp3",
                "start": timing["start"],
                "end": timing["end"],
            }
        )
    return audio


def _compile_subtitles(beats: list[dict[str, Any]], beat_times: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    subtitles = []
    for beat in beats:
        text = beat.get("subtitle") or beat.get("text")
        if not text or beat.get("kind") in {"pause", "screen"}:
            continue
        timing = beat_times[beat["id"]]
        subtitles.append({"beat_id": beat["id"], "start": timing["start"], "end": timing["end"], "text": text})
    return subtitles


def _write_timeline_csv(render_plan: dict[str, Any], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["shot_id", "source_kind", "src", "start", "end", "start_clock", "end_clock", "motion"],
        )
        writer.writeheader()
        for event in render_plan["video"]:
            writer.writerow(
                {
                    "shot_id": event["shot_id"],
                    "source_kind": event["source_kind"],
                    "src": event.get("src") or "",
                    "start": event["start_seconds"],
                    "end": event["end_seconds"],
                    "start_clock": event["start_clock"],
                    "end_clock": event["end_clock"],
                    "motion": event.get("motion", {}).get("type", "static"),
                }
            )
