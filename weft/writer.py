from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

from .compiler import compile_render_plan, write_exports
from .validate import validate_project

# Dryrun placeholder canvas per IMAGE_ASPECT — matches the ratio real
# candidates are normalized to, so dryrun layout previews are faithful.
_PLACEHOLDER_SIZES = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (1024, 1024),
    "3:2": (1230, 820),
}

# Shot image layout. New generations always land in the provider-neutral
# ``images/gen/`` so switching IMAGE_PROVIDER keeps caches and picks (the .key
# sidecar is a prompt hash, so providers never collide). Old projects from the
# pre-neutral layout used ``images/openai/``; readers fall back to it so
# existing candidates and PICKS keep working untouched.
GEN_IMG_SUBDIR = "images/gen"
LEGACY_IMG_SUBDIRS = ("images/openai",)
IMG_SUBDIRS = (GEN_IMG_SUBDIR, *LEGACY_IMG_SUBDIRS)  # read order: new first
DRYRUN_IMG_SUBDIR = "images/dryrun"  # deterministic SVG placeholders (no API)


def write_project(project: dict[str, Any], output_dir: str | Path, *, materialize_assets: bool = True) -> dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "project.json", project["project"])
    _write_json(output_dir / "NARRATION.json", project["narration"])
    _write_json(output_dir / "VISUALS.json", project["visuals"])
    _write_shots(project, output_dir, materialize_assets=materialize_assets)
    picks = _build_picks(project, output_dir, materialize_assets=materialize_assets)
    _write_json(output_dir / "PICKS.json", picks)
    render_plan = compile_render_plan(project, picks)
    write_exports(render_plan, output_dir)
    violations = validate_project(project, picks, output_dir)
    write_report(project, picks, render_plan, violations, output_dir)
    return {"picks": picks, "render_plan": render_plan, "violations": violations}


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_shots(project: dict[str, Any], output_dir: Path, *, materialize_assets: bool) -> None:
    size = _placeholder_size(output_dir)
    for shot in project["visuals"]["shots"]:
        shot_dir = output_dir / "SHOTS" / shot["id"]
        shot_dir.mkdir(parents=True, exist_ok=True)
        prompt = shot.get("prompt", "")
        shot_payload = {
            "schema": "weft-shot-v1",
            "id": shot["id"],
            "source_kind": shot.get("source_kind", "image"),
            "reuse_of": shot.get("reuse_of"),
            "prompt": prompt,
        }
        if shot.get("src"):
            shot_payload["src"] = shot["src"]
        _write_json(shot_dir / "SHOT.json", shot_payload)
        (shot_dir / "PROMPT.md").write_text(prompt + "\n", encoding="utf-8")
        if materialize_assets and shot.get("source_kind") not in {"reuse", "clip", "stock_clip", "remotion", "hyperframe"}:
            asset = shot_dir / DRYRUN_IMG_SUBDIR / "candidate_001.svg"
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_text(_placeholder_svg(shot, size), encoding="utf-8")


def _build_picks(project: dict[str, Any], output_dir: Path, *, materialize_assets: bool) -> dict[str, Any]:
    selections = {}
    auto_picked = []
    for shot in project["visuals"]["shots"]:
        if shot.get("source_kind") in {"reuse", "clip", "stock_clip", "remotion", "hyperframe"}:
            continue
        rel = f"{DRYRUN_IMG_SUBDIR}/candidate_001.svg"
        if materialize_assets:
            expected = output_dir / "SHOTS" / shot["id"] / rel
            if not expected.exists():
                raise FileNotFoundError(expected)
        selections[shot["id"]] = rel
        auto_picked.append(shot["id"])
    return {
        "schema": "weft-picks-v1",
        "selections": selections,
        "auto_picked": auto_picked,
        "overridden": [],
    }


def _placeholder_size(output_dir: Path | None = None) -> tuple[int, int]:
    """Placeholder canvas for the active IMAGE_ASPECT (env → WEFT_SETTINGS.txt; lenient)."""
    aspect = os.environ.get("IMAGE_ASPECT", "").strip()
    if not aspect and output_dir is not None:
        from .settings import load_project_settings  # local import: no cycle at module load

        aspect = load_project_settings(output_dir).get("IMAGE_ASPECT", "").strip()
    return _PLACEHOLDER_SIZES.get(aspect or "16:9", _PLACEHOLDER_SIZES["16:9"])


def _placeholder_svg(shot: dict[str, Any], size: tuple[int, int] | None = None) -> str:
    title = html.escape(shot["id"])
    kind = html.escape(shot.get("source_kind", "image"))
    prompt = html.escape((shot.get("prompt") or "").replace("\n", " ")[:180])
    color = {
        "image": "#f6ead2",
        "text_card": "#1e293b",
        "screen_element": "#263238",
        "clip": "#111827",
        "stock_clip": "#dbeafe",
        "remotion": "#0f172a",
        "hyperframe": "#111827",
    }.get(shot.get("source_kind", "image"), "#f6ead2")
    text_color = "#f8fafc" if shot.get("source_kind") in {"text_card", "screen_element"} else "#2f2a22"
    width, height = size or _placeholder_size()
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="{color}"/>
  <rect x="64" y="64" width="{width - 128}" height="{height - 128}" fill="none" stroke="{text_color}" stroke-width="4" opacity="0.35"/>
  <text x="96" y="142" fill="{text_color}" font-family="Arial, sans-serif" font-size="42" font-weight="700">{title}</text>
  <text x="96" y="202" fill="{text_color}" font-family="Arial, sans-serif" font-size="28">dryrun {kind}</text>
  <foreignObject x="96" y="250" width="{width - 192}" height="{height - 390}">
    <div xmlns="http://www.w3.org/1999/xhtml" style="font-family: Arial, sans-serif; font-size: 30px; line-height: 1.35; color: {text_color};">{prompt}</div>
  </foreignObject>
</svg>
"""


def write_report(
    project: dict[str, Any],
    picks: dict[str, Any],
    render_plan: dict[str, Any],
    violations: list[dict[str, str]],
    output_dir: Path,
) -> None:
    beats = project["narration"]["beats"]
    shots = project["visuals"]["shots"]
    voice_beats = [beat for beat in beats if beat.get("kind") == "narration" and beat.get("text")]
    pause_beats = [beat for beat in beats if beat.get("kind") == "pause"]
    reuse_shots = [shot for shot in shots if shot.get("source_kind") == "reuse"]
    image_assets = [shot for shot in shots if shot.get("source_kind") == "image"]
    animated_assets = [shot for shot in shots if shot.get("source_kind") in {"remotion", "hyperframe"}]
    selections = picks.get("selections", {})
    generated_prefixes = tuple(f"{subdir}/" for subdir in IMG_SUBDIRS)
    generated_picks = sum(1 for rel in selections.values() if str(rel).startswith(generated_prefixes))
    dryrun_picks = sum(1 for rel in selections.values() if str(rel).startswith(f"{DRYRUN_IMG_SUBDIR}/"))
    real_audio = sum(1 for beat in voice_beats if str(beat.get("audio", "")).endswith(".wav"))
    lines = [
        "# Weft Dry Run Report",
        "",
        f"- title: {project['project']['title']}",
        f"- beats_total: {len(beats)}",
        f"- voice_beats: {len(voice_beats)}",
        f"- pause_beats: {len(pause_beats)}",
        f"- shots_total: {len(shots)}",
        f"- reuse_shots: {len(reuse_shots)}",
        f"- generated_image_placeholders: {len(image_assets)}",
        f"- animation_clip_shots: {len(animated_assets)}",
        f"- picked_assets: {len(picks['selections'])}",
        f"- video_events: {len(render_plan['video'])}",
        f"- audio_events: {len(render_plan['audio'])}",
        f"- subtitle_events: {len(render_plan['subtitles'])}",
        f"- total_seconds: {render_plan['total_seconds']:.3f}",
        f"- validation_errors: {sum(1 for item in violations if item['severity'] == 'error')}",
        f"- generated_picks: {generated_picks}",
        f"- dryrun_picks: {dryrun_picks}",
        f"- wav_audio_beats: {real_audio}",
        "",
        "## Notes",
        "",
        "- Beat-level reuse tokens were normalized into explicit reuse shots so VISUALS.json stays the single source of truth.",
    ]
    if real_audio:
        lines.append("- TTS durations come from generated WAV sidecars where available; remaining beats keep their current duration values.")
    else:
        lines.append("- TTS is mocked. Durations come from the CONTI time column.")
    if generated_picks:
        lines.append("- Image picks include provider-generated PNG assets; ungenerated shots may still use deterministic SVG placeholders.")
    else:
        lines.append("- Image-provider calls are mocked. Assets are deterministic SVG placeholders.")
    if violations:
        lines.extend(["", "## Violations", ""])
        lines.extend(f"- {item['severity']} {item['invariant']} {item['where']}: {item['fix_hint']}" for item in violations)
    (output_dir / "DRYRUN_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
