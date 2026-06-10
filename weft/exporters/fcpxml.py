"""Export a Weft render plan as Final Cut Pro XML."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .capcut_draft import CANVAS_H, CANVAS_W, _make_card_png

DEFAULT_OUTPUT_NAME = "weft_timeline.fcpxml"

# FCP requires every <title> to ref an effect resource; this is the stock Basic Title.
BASIC_TITLE_UID = ".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"


def export_fcpxml(project_dir: str | Path, *, output: str | Path | None = None) -> dict[str, Any]:
    project_dir = Path(project_dir)
    exports_dir = project_dir / "EXPORTS"
    render_plan = _load(exports_dir / "render_plan.json")
    output_path = Path(output) if output else exports_dir / DEFAULT_OUTPUT_NAME
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fps = int(render_plan.get("fps", 30))
    sample_rate = int(render_plan.get("sample_rate", 48_000))
    total_samples = int(render_plan["total_samples"])
    cards = _load(project_dir / "CARDS.json") if (project_dir / "CARDS.json").is_file() else {}
    work_dir = exports_dir / "fcpxml"
    work_dir.mkdir(parents=True, exist_ok=True)

    root = ET.Element("fcpxml", {"version": "1.11"})
    resources = ET.SubElement(root, "resources")
    format_id = "r1"
    ET.SubElement(
        resources,
        "format",
        {
            "id": format_id,
            "name": f"FFVideoFormat1080p{fps}",
            "frameDuration": _duration_fraction(round(sample_rate / fps), sample_rate),
            "width": str(CANVAS_W),
            "height": str(CANVAS_H),
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )

    warnings: list[str] = []
    video_assets = _asset_ids(
        resources,
        project_dir,
        work_dir,
        render_plan.get("video", []),
        cards,
        format_id,
        sample_rate,
        warnings,
    )
    audio_assets = _audio_asset_ids(resources, project_dir, render_plan.get("audio", []), sample_rate, warnings)

    title_effect_id = ""
    if render_plan.get("subtitles"):
        title_effect_id = "r2"
        ET.SubElement(resources, "effect", {"id": title_effect_id, "name": "Basic Title", "uid": BASIC_TITLE_UID})

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": "Weft"})
    project = ET.SubElement(event, "project", {"name": _project_name(project_dir)})
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": format_id,
            "duration": _frame_fraction(total_samples, sample_rate, fps, minimum_frames=1),
            "tcStart": "0s",
            "tcFormat": "NDF",
        },
    )
    spine = ET.SubElement(sequence, "spine")
    gap = ET.SubElement(
        spine,
        "gap",
        {
            "name": "Weft Timeline",
            "offset": "0s",
            "start": "0s",
            "duration": _frame_fraction(total_samples, sample_rate, fps, minimum_frames=1),
        },
    )

    # Video and title timings are snapped onto the frame grid (numerators that are
    # multiples of sample_rate/fps) — FCP rejects/warns on non-frame-aligned video
    # edits. Audio lanes may keep sample precision.
    for event_item in render_plan.get("video", []):
        src = event_item.get("src")
        if not src:
            continue
        asset_id = video_assets[src]
        clip = ET.SubElement(
            gap,
            "asset-clip",
            {
                "name": event_item.get("shot_id", "shot"),
                "ref": asset_id,
                "lane": "1",
                "offset": _frame_fraction(int(event_item["start"]), sample_rate, fps),
                "start": "0s",
                "duration": _frame_fraction(
                    int(event_item["end"]) - int(event_item["start"]), sample_rate, fps, minimum_frames=1
                ),
            },
        )
        _append_motion_note(clip, event_item)

    for audio_event in render_plan.get("audio", []):
        src = audio_event.get("src")
        if not src:
            continue
        ET.SubElement(
            gap,
            "asset-clip",
            {
                "name": audio_event.get("beat_id", "audio"),
                "ref": audio_assets[src],
                "lane": "-1",
                "offset": _duration_fraction(int(audio_event["start"]), sample_rate),
                "start": "0s",
                "duration": _duration_fraction(int(audio_event["end"]) - int(audio_event["start"]), sample_rate),
            },
        )

    for index, subtitle in enumerate(render_plan.get("subtitles", []), start=1):
        title = ET.SubElement(
            gap,
            "title",
            {
                "name": f"subtitle_{index:04d}",
                "ref": title_effect_id,
                "lane": "2",
                "offset": _frame_fraction(int(subtitle["start"]), sample_rate, fps),
                "start": "0s",
                "duration": _frame_fraction(int(subtitle["end"]) - int(subtitle["start"]), sample_rate, fps, minimum_frames=1),
            },
        )
        text = ET.SubElement(title, "text")
        text_style = ET.SubElement(text, "text-style", {"ref": f"ts{index}"})
        text_style.text = str(subtitle.get("text", ""))
        style_def = ET.SubElement(title, "text-style-def", {"id": f"ts{index}"})
        ET.SubElement(
            style_def,
            "text-style",
            {
                "font": "Apple SD Gothic Neo",
                "fontSize": "54",
                "fontColor": "1 1 1 1",
                "alignment": "center",
            },
        )

    for warning in warnings:
        print(f"경고: {warning}", file=sys.stderr)

    _indent(root)
    xml = ET.tostring(root, encoding="unicode")
    output_path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + xml + "\n", encoding="utf-8")
    return {
        "kind": "fcpxml",
        "output": str(output_path),
        "video_events": len(render_plan.get("video", [])),
        "audio_events": len(render_plan.get("audio", [])),
        "subtitle_events": len(render_plan.get("subtitles", [])),
        "total_seconds": round(total_samples / sample_rate, 3),
        "warnings": warnings,
    }


def _asset_ids(
    resources: ET.Element,
    project_dir: Path,
    work_dir: Path,
    events: list[dict[str, Any]],
    cards: dict[str, str],
    format_id: str,
    sample_rate: int,
    warnings: list[str],
) -> dict[str, str]:
    asset_by_src: dict[str, str] = {}
    for event in events:
        src = str(event.get("src") or "")
        if not src or src in asset_by_src:
            continue
        asset_id = f"v{len(asset_by_src) + 1}"
        asset_path = _video_asset(project_dir, work_dir, event, cards)
        if not asset_path.is_file():
            warnings.append(f"비디오 에셋 파일 없음 (FCP에서 오프라인 미디어로 표시됨): {asset_path}")
        duration = _duration_fraction(int(event["end"]) - int(event["start"]), sample_rate)
        attrs = {
            "id": asset_id,
            "name": Path(src).name or event.get("shot_id", asset_id),
            "start": "0s",
            "duration": duration,
            "hasVideo": "1",
            "format": format_id,
        }
        if event.get("source_kind") in {"clip", "stock_clip", "remotion", "hyperframe"}:
            attrs["hasAudio"] = "0"
        asset = ET.SubElement(resources, "asset", attrs)
        # FCPXML 1.11: the media location lives in a media-rep child, not an asset attr.
        ET.SubElement(asset, "media-rep", {"kind": "original-media", "src": _file_uri(asset_path)})
        asset_by_src[src] = asset_id
    return asset_by_src


def _audio_asset_ids(
    resources: ET.Element,
    project_dir: Path,
    events: list[dict[str, Any]],
    sample_rate: int,
    warnings: list[str],
) -> dict[str, str]:
    asset_by_src: dict[str, str] = {}
    for event in events:
        src = str(event.get("src") or "")
        if not src or src in asset_by_src:
            continue
        path = (project_dir / src).resolve()
        if not path.is_file():
            warnings.append(f"오디오 에셋 파일 없음 (FCP에서 오프라인 미디어로 표시됨): {path}")
        asset_id = f"a{len(asset_by_src) + 1}"
        asset = ET.SubElement(
            resources,
            "asset",
            {
                "id": asset_id,
                "name": Path(src).name or event.get("beat_id", asset_id),
                "start": "0s",
                "duration": _duration_fraction(int(event["end"]) - int(event["start"]), sample_rate),
                "hasAudio": "1",
                "audioSources": "1",
                "audioChannels": "1",
                "audioRate": str(sample_rate),
            },
        )
        ET.SubElement(asset, "media-rep", {"kind": "original-media", "src": _file_uri(path)})
        asset_by_src[src] = asset_id
    return asset_by_src


def _video_asset(project_dir: Path, work_dir: Path, event: dict[str, Any], cards: dict[str, str]) -> Path:
    src = str(event.get("src") or "")
    path = (project_dir / src).resolve()
    if path.suffix.lower() == ".svg":
        return _make_card_png(
            str(event.get("shot_id") or "card"),
            str(event.get("source_kind") or "image"),
            work_dir / "cards",
            cards.get(str(event.get("shot_id") or "")),
        )
    return path


def _append_motion_note(parent: ET.Element, event: dict[str, Any]) -> None:
    motion = event.get("motion", {})
    motion_type = motion.get("type") or "static"
    if motion_type == "static":
        return
    note = ET.SubElement(parent, "note")
    note.text = f"Weft motion: {motion_type}; apply/rebuild in NLE if precise keyframes are required."


def _duration_fraction(samples: int, sample_rate: int) -> str:
    if samples <= 0:
        return "0s"
    return f"{samples}/{sample_rate}s"


def _frame_fraction(samples: int, sample_rate: int, fps: int, minimum_frames: int = 0) -> str:
    """Rational seconds snapped to the frame grid (numerator a multiple of
    sample_rate/fps, e.g. n*1600/48000s at 30fps/48kHz) so FCP sees frame-aligned edits."""
    frame_samples = max(1, round(sample_rate / fps))
    frames = max(minimum_frames, round(samples / frame_samples))
    return _duration_fraction(frames * frame_samples, sample_rate)


def _file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _project_name(project_dir: Path) -> str:
    return project_dir.parent.name if project_dir.name == "generated_project" else project_dir.name


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _indent(element: ET.Element, level: int = 0) -> None:
    pad = "\n" + level * "  "
    if len(element):
        if not element.text or not element.text.strip():
            element.text = pad + "  "
        for child in element:
            _indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = pad
    if level and (not element.tail or not element.tail.strip()):
        element.tail = pad
