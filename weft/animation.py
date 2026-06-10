"""Animation-shot preparation for AI-agent workflows.

Remotion/HyperFrame shots are authored by an AI agent or a human as external
mini projects. Weft treats their rendered MP4 output as normal timeline clips.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .assets import recompile_exports

ANIMATION_SOURCE_KINDS = {"remotion", "hyperframe"}
ANIMATION_OUTPUT = "rendered/clip.mp4"


def prepare_animation_shots(
    project_dir: str | Path,
    *,
    refresh_specs: bool = False,
    check: bool = False,
    recompile: bool = True,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    if recompile:
        render_plan = recompile_exports(project_dir)
    else:
        render_plan = json.loads((project_dir / "EXPORTS" / "render_plan.json").read_text(encoding="utf-8"))

    visuals = json.loads((project_dir / "VISUALS.json").read_text(encoding="utf-8"))
    video_by_shot = {event["shot_id"]: event for event in render_plan.get("video", [])}
    tasks = []
    for shot in visuals.get("shots", []):
        kind = str(shot.get("source_kind", "image"))
        if kind not in ANIMATION_SOURCE_KINDS:
            continue
        shot_id = str(shot["id"])
        shot_dir = project_dir / "SHOTS" / shot_id
        animation_dir = shot_dir / "animation"
        output_rel = f"SHOTS/{shot_id}/{ANIMATION_OUTPUT}"
        output_path = project_dir / output_rel
        spec_path = animation_dir / "SPEC.md"
        event = video_by_shot.get(shot_id, {})
        duration = _duration_seconds(event, render_plan)

        animation_dir.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if refresh_specs or not spec_path.exists():
            spec_path.write_text(_spec_text(shot, kind, duration, output_rel), encoding="utf-8")

        tasks.append(
            {
                "shot_id": shot_id,
                "source_kind": kind,
                "duration_seconds": duration,
                "spec": str(spec_path),
                "output": str(output_path),
                "output_rel": output_rel,
                "status": "done" if output_path.is_file() else "pending",
            }
        )

    pending = [task for task in tasks if task["status"] == "pending"]
    return {
        "kind": "animate",
        "project_dir": str(project_dir),
        "total": len(tasks),
        "done": len(tasks) - len(pending),
        "pending": pending,
        "tasks": tasks,
        "check": check,
    }


def _duration_seconds(event: dict[str, Any], render_plan: dict[str, Any]) -> float:
    sample_rate = int(render_plan.get("sample_rate", 48_000))
    if event:
        return round((int(event["end"]) - int(event["start"])) / sample_rate, 3)
    return 0.0


def _spec_text(shot: dict[str, Any], kind: str, duration: float, output_rel: str) -> str:
    shot_id = str(shot["id"])
    prompt = str(shot.get("prompt") or "").strip()
    cover = shot.get("cover", {})
    engine_note = (
        "Use Remotion React. Animate with useCurrentFrame()/interpolate(); do not use CSS transitions or CSS animations."
        if kind == "remotion"
        else "Use HyperFrame-style HTML/CSS animation tooling and render it to MP4."
    )
    return f"""# Weft Animation Shot: {shot_id}

- source_kind: {kind}
- cover: {cover.get("from", "")}~{cover.get("to", "")}
- duration_seconds: {duration:.3f}
- output: {output_rel}

## Prompt

{prompt}

## Contract

- Render one 16:9 MP4 clip with no narration audio.
- Match `duration_seconds` as closely as practical.
- Save the final rendered file exactly at `{output_rel}` relative to the generated project.
- Keep critical text large enough to read after export.
- After rendering, run `weft animate --check` and then `weft ffmpeg`.

## Engine Guidance

{engine_note}
"""
