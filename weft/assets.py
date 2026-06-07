"""Asset generation orchestration: real TTS (Typecast) and images (OpenAI).

Operates on a generated project directory (the JSON files are the source of
truth). Both generators are idempotent: a sidecar cache key per beat/shot means
re-running never re-bills unchanged inputs. After generation, exports are
recompiled so render_plan / SRT / CapCut reflect the real audio durations.
"""

from __future__ import annotations

import json
import os
import wave
from pathlib import Path
from typing import Any, Callable

from .compiler import compile_render_plan, write_exports
from .providers.env import load_env, require
from .providers.openai_image import OpenAIImage
from .providers.typecast_tts import TypecastTTS
from .validate import validate_project
from .writer import write_report

# Default style suffix. Inherited by every generated image to keep the whole video
# consistent. Override per-project by writing your own to <project>/STYLE.txt
# (see STYLE_GUIDE.md). This default = clean diagrammatic explainer + warm light.
DEFAULT_STYLE = (
    "Style: clean diagrammatic explainer illustration with precise educational-diagram "
    "clarity (3Blue1Brown-like structure); warm light palette — soft cream / off-white "
    "background with warm amber, terracotta, and muted teal accents; smooth vector shapes, "
    "consistent thin-to-medium line weight, generous negative space; balanced mix of accurate "
    "schematic diagrams and conceptual metaphor imagery; no human figures (concepts, objects, "
    "and metaphors only); soft ambient shading, gentle depth; friendly yet intellectually "
    "credible mood; 16:9; no text inside generated images."
)

# Backwards-compat alias.
STYLE_SUFFIX = DEFAULT_STYLE


def load_style(project_dir: str | Path) -> str:
    """Active style suffix for image generation.

    Precedence: ``<project_dir>/STYLE.txt`` → ``<project_dir>/../STYLE.txt`` →
    ``DEFAULT_STYLE``. Edit STYLE.txt to give a whole video your own consistent
    look without touching code (STYLE_GUIDE.md).
    """
    project_dir = Path(project_dir)
    for candidate in (project_dir / "STYLE.txt", project_dir.parent / "STYLE.txt"):
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
    return DEFAULT_STYLE


Progress = Callable[[int, int, str, str, float], None]


def generate_tts(
    project_dir: str | Path,
    *,
    voice_id: str | None = None,
    limit: int | None = None,
    beat_ids: list[str] | None = None,
    force: bool = False,
    recompile: bool = True,
    progress: Progress | None = None,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    load_env()
    api_key = require("TYPECAST_API_KEY")
    voice_id = voice_id or require("TYPECAST_VOICE")
    model = os.environ.get("TYPECAST_MODEL", "ssfm-v30")
    language = os.environ.get("TYPECAST_LANGUAGE", "kor")
    emotion = os.environ.get("TYPECAST_EMOTION", "normal")
    provider = TypecastTTS(api_key=api_key, voice_id=voice_id, model=model, language=language, emotion=emotion)

    narration_path = project_dir / "NARRATION.json"
    narration = json.loads(narration_path.read_text(encoding="utf-8"))
    beats = narration["beats"]
    targets = [b for b in beats if b.get("kind") == "narration" and b.get("text")]
    if beat_ids:
        wanted = set(beat_ids)
        targets = [b for b in targets if b["id"] in wanted]
    if limit:
        targets = targets[:limit]

    audio_dir = project_dir / "AUDIO" / "beats"
    audio_dir.mkdir(parents=True, exist_ok=True)
    made = cached = 0
    failed: list[dict[str, str]] = []
    for index, beat in enumerate(targets, start=1):
        wav_path = audio_dir / f"{beat['id']}.wav"
        side_path = audio_dir / f"{beat['id']}.json"
        key = provider.cache_key(beat["text"])
        status = "new"
        if not force and wav_path.exists() and side_path.exists():
            meta = json.loads(side_path.read_text(encoding="utf-8"))
            if meta.get("key") == key:
                duration = float(meta["duration"])
                status = "cache"
        if status == "new":
            try:
                audio = provider.synthesize(beat["text"])
            except Exception as exc:  # isolate one bad beat; keep the batch alive
                failed.append({"beat_id": beat["id"], "error": str(exc)[:200]})
                if progress:
                    progress(index, len(targets), beat["id"], "FAIL", float(beat.get("duration", 0.0)))
                continue
            wav_path.write_bytes(audio)
            duration = _wav_duration(wav_path)
            side_path.write_text(
                json.dumps(
                    {
                        "key": key,
                        "duration": duration,
                        "voice_id": voice_id,
                        "model": model,
                        "language": language,
                        "emotion": emotion,
                        "chars": len(beat["text"]),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            made += 1
        else:
            cached += 1
        beat["duration"] = duration
        beat["audio"] = f"AUDIO/beats/{beat['id']}.wav"
        if progress:
            progress(index, len(targets), beat["id"], status, duration)

    narration_path.write_text(json.dumps(narration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary: dict[str, Any] = {
        "kind": "tts",
        "made": made,
        "cached": cached,
        "failed": failed,
        "total": len(targets),
        "voice_id": voice_id,
    }
    if recompile:
        summary["total_seconds"] = recompile_exports(project_dir)["total_seconds"]
    return summary


def generate_images(
    project_dir: str | Path,
    *,
    limit: int | None = None,
    shot_ids: list[str] | None = None,
    n: int | None = None,
    quality: str | None = None,
    size: str | None = None,
    force: bool = False,
    recompile: bool = True,
    estimate: bool = False,
    progress: Progress | None = None,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    load_env()
    model = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
    n = n or int(os.environ.get("IMAGE_CANDIDATES_N", "2"))
    quality = quality or os.environ.get("IMAGE_QUALITY", "medium")
    size = size or os.environ.get("IMAGE_SIZE", "1536x1024")

    visuals = json.loads((project_dir / "VISUALS.json").read_text(encoding="utf-8"))
    shots = [s for s in visuals["shots"] if s.get("source_kind") == "image"]
    if shot_ids:
        wanted = set(shot_ids)
        shots = [s for s in shots if s["id"] in wanted]
    if limit:
        shots = shots[:limit]

    if estimate:
        return {
            "kind": "images-estimate",
            "shots": len(shots),
            "candidates": len(shots) * n,
            "model": model,
            "size": size,
            "quality": quality,
            "n": n,
        }

    api_key = require("OPENAI_API_KEY")
    style = load_style(project_dir)
    provider = OpenAIImage(api_key=api_key, model=model, size=size, quality=quality)
    picks_path = project_dir / "PICKS.json"
    picks = json.loads(picks_path.read_text(encoding="utf-8"))
    picks.setdefault("selections", {})
    picks.setdefault("auto_picked", [])
    picks.setdefault("overridden", [])

    made = cached = 0
    failed: list[dict[str, str]] = []
    for index, shot in enumerate(shots, start=1):
        prompt = (shot.get("prompt") or "").strip()
        full_prompt = f"{prompt}\n\n{style}" if prompt else style
        img_dir = project_dir / "SHOTS" / shot["id"] / "images" / "openai"
        img_dir.mkdir(parents=True, exist_ok=True)
        key_path = img_dir / ".key"
        key = provider.cache_key(full_prompt)
        existing = sorted(img_dir.glob("candidate_*.png"))
        status = "new"
        if (
            not force
            and len(existing) >= n
            and key_path.exists()
            and key_path.read_text(encoding="utf-8").strip() == key
        ):
            status = "cache"
        if status == "new":
            try:
                blobs = provider.generate(full_prompt, n=n)
            except Exception as exc:  # isolate one bad shot; keep the batch alive
                failed.append({"shot_id": shot["id"], "error": str(exc)[:200]})
                if progress:
                    progress(index, len(shots), shot["id"], "FAIL", 0.0)
                continue
            _clear_auto_candidates(img_dir)
            for slot, blob in enumerate(blobs, start=1):
                (img_dir / f"candidate_{slot:03d}.png").write_bytes(blob)
            key_path.write_text(key, encoding="utf-8")
            made += 1
        else:
            cached += 1
        if shot["id"] not in picks["overridden"]:
            picks["selections"][shot["id"]] = "images/openai/candidate_001.png"
            if shot["id"] not in picks["auto_picked"]:
                picks["auto_picked"].append(shot["id"])
        else:
            picks["auto_picked"] = [sid for sid in picks["auto_picked"] if sid != shot["id"]]
        if progress:
            progress(index, len(shots), shot["id"], status, 0.0)

    picks_path.write_text(json.dumps(picks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary: dict[str, Any] = {
        "kind": "images",
        "made": made,
        "cached": cached,
        "failed": failed,
        "total": len(shots),
        "n": n,
        "quality": quality,
        "size": size,
    }
    if recompile:
        summary["total_seconds"] = recompile_exports(project_dir)["total_seconds"]
    return summary


def append_candidates(
    project_dir: str | Path,
    shot_id: str,
    *,
    n: int = 1,
    prompt: str | None = None,
    quality: str | None = None,
    size: str | None = None,
) -> dict[str, Any]:
    """Generate N *additional* image candidates for one shot (append, never overwrite).

    If ``prompt`` is given it replaces the shot's prompt in VISUALS.json (+ sidecars)
    and is used for generation; otherwise the stored prompt is used. Returns the new
    candidate filenames. Used by the picker's "+ generate" and prompt-edit actions.
    """
    project_dir = Path(project_dir)
    load_env()
    model = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
    quality = quality or os.environ.get("IMAGE_QUALITY", "medium")
    size = size or os.environ.get("IMAGE_SIZE", "1536x1024")

    visuals_path = project_dir / "VISUALS.json"
    visuals = json.loads(visuals_path.read_text(encoding="utf-8"))
    shot = next((s for s in visuals["shots"] if s["id"] == shot_id), None)
    if shot is None:
        raise ValueError(f"shot 없음: {shot_id}")

    if prompt is not None and prompt.strip():
        shot["prompt"] = prompt.strip()
        visuals_path.write_text(json.dumps(visuals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _sync_shot_prompt(project_dir, shot_id, prompt.strip())
    base_prompt = (shot.get("prompt") or "").strip()
    style = load_style(project_dir)
    full_prompt = f"{base_prompt}\n\n{style}" if base_prompt else style

    api_key = require("OPENAI_API_KEY")
    provider = OpenAIImage(api_key=api_key, model=model, size=size, quality=quality)
    img_dir = project_dir / "SHOTS" / shot_id / "images" / "openai"
    img_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(img_dir.glob("candidate_*.png"))
    next_idx = 1 + max([_candidate_index(p) for p in existing], default=0)

    blobs = provider.generate(full_prompt, n=n)
    new_names = []
    for offset, blob in enumerate(blobs):
        name = f"candidate_{next_idx + offset:03d}.png"
        (img_dir / name).write_bytes(blob)
        new_names.append(name)
    return {"shot_id": shot_id, "new": new_names, "prompt": base_prompt}


def _candidate_index(path: Path) -> int:
    try:
        return int(path.stem.split("_")[-1])
    except ValueError:
        return 0


def _clear_auto_candidates(img_dir: Path) -> None:
    for path in img_dir.glob("candidate_*.png"):
        path.unlink()


def _sync_shot_prompt(project_dir: Path, shot_id: str, prompt: str) -> None:
    shot_dir = project_dir / "SHOTS" / shot_id
    shot_json = shot_dir / "SHOT.json"
    if shot_json.is_file():
        data = json.loads(shot_json.read_text(encoding="utf-8"))
        data["prompt"] = prompt
        shot_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (shot_dir / "PROMPT.md").write_text(prompt + "\n", encoding="utf-8")


def recompile_exports(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    project = {
        "project": json.loads((project_dir / "project.json").read_text(encoding="utf-8")),
        "narration": json.loads((project_dir / "NARRATION.json").read_text(encoding="utf-8")),
        "visuals": json.loads((project_dir / "VISUALS.json").read_text(encoding="utf-8")),
    }
    picks = json.loads((project_dir / "PICKS.json").read_text(encoding="utf-8"))
    render_plan = compile_render_plan(project, picks)
    write_exports(render_plan, project_dir)
    violations = validate_project(project, picks, project_dir)
    write_report(project, picks, render_plan, violations, project_dir)
    return render_plan


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
    return frames / float(rate) if rate else 0.0
