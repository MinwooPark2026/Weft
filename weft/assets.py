"""Asset generation orchestration: real TTS and images via pluggable providers.

Providers (Typecast TTS; OpenAI / ComfyUI / stub images) are selected with
TTS_PROVIDER / IMAGE_PROVIDER and constructed in ``weft.providers.registry``.
Operates on a generated project directory (the JSON files are the source of
truth). Both generators are idempotent: a sidecar cache key per beat/shot means
re-running never re-bills unchanged inputs. After generation, exports are
recompiled so render_plan / SRT / CapCut reflect the real audio durations.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Callable

from .compiler import compile_render_plan, write_exports
from .providers.env import load_env
from .providers.registry import create_image_provider, create_tts_provider, image_provider_label
from .settings import apply_project_settings, setting_int
from .validate import validate_project
from .writer import GEN_IMG_SUBDIR, IMG_SUBDIRS, LEGACY_IMG_SUBDIRS, write_report

# GEN_IMG_SUBDIR / IMG_SUBDIRS / LEGACY_IMG_SUBDIRS are re-exported here for the
# picker and tests: new generations write to GEN_IMG_SUBDIR ("images/gen"),
# readers search IMG_SUBDIRS in order so pre-existing "images/openai" projects
# keep their candidates and picks without migration.

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
    generated default ``STYLE.txt``. Edit STYLE.txt to give a whole video your
    own consistent look without touching code (STYLE_GUIDE.md).
    """
    project_dir = Path(project_dir)
    for candidate in (project_dir / "STYLE.txt", project_dir.parent / "STYLE.txt"):
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
    default_path = project_dir.parent / "STYLE.txt" if project_dir.name == "generated_project" else project_dir / "STYLE.txt"
    try:
        default_path.write_text(DEFAULT_STYLE + "\n", encoding="utf-8")
    except OSError:
        pass
    return DEFAULT_STYLE


Progress = Callable[[int, int, str, str, float], None]


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text to ``path`` atomically (tmp file in the same dir + os.replace).

    Concurrent readers never observe a half-written file; concurrent writers
    last-write-win instead of corrupting JSON.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


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
    apply_project_settings(project_dir)
    provider_name = os.environ.get("TTS_PROVIDER", "typecast").strip().lower()
    # Provider-specific env (TYPECAST_MODEL/LANGUAGE/EMOTION …) is read inside
    # each provider's from_env(); only the provider choice lives here.
    bundle = create_tts_provider(provider_name=provider_name, voice_id=voice_id)
    provider = bundle.provider
    metadata = bundle.metadata
    voice_id = metadata.get("voice_id", "")

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
            try:  # a broken/stale sidecar must not kill the batch — just regenerate
                meta = json.loads(side_path.read_text(encoding="utf-8"))
                if meta.get("key") == key:
                    duration = float(meta["duration"])
                    status = "cache"
            except (OSError, ValueError, TypeError, KeyError):
                status = "new"
        if status == "new":
            try:
                audio = provider.synthesize(beat["text"])
            except Exception as exc:  # isolate one bad beat; keep the batch alive
                failed.append({"beat_id": beat["id"], "error": str(exc)[:200]})
                if progress:
                    progress(index, len(targets), beat["id"], "FAIL", float(beat.get("duration", 0.0)))
                continue
            try:
                wav_path.write_bytes(audio)
                duration = _wav_duration(wav_path)
            except Exception as exc:  # broken write/parse must not kill the batch
                failed.append({"beat_id": beat["id"], "error": str(exc)[:200]})
                try:  # drop the half-written/unparseable wav so nothing trusts it
                    wav_path.unlink()
                except OSError:
                    pass
                if progress:
                    progress(index, len(targets), beat["id"], "FAIL", float(beat.get("duration", 0.0)))
                continue
            _atomic_write_json(
                side_path,
                {
                    "key": key,
                    "duration": duration,
                    **metadata,
                    "chars": len(beat["text"]),
                },
            )
            made += 1
        else:
            cached += 1
        beat["duration"] = duration
        beat["audio"] = f"AUDIO/beats/{beat['id']}.wav"
        if progress:
            progress(index, len(targets), beat["id"], status, duration)

    _atomic_write_json(narration_path, narration)
    summary: dict[str, Any] = {
        "kind": "tts",
        "made": made,
        "cached": cached,
        "failed": failed,
        "total": len(targets),
        "voice_id": voice_id,
        "provider": metadata["provider"],
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
    apply_project_settings(project_dir)
    provider_name = os.environ.get("IMAGE_PROVIDER", "openai").strip().lower()
    n = n or setting_int(os.environ, "IMAGE_CANDIDATES_N", 2) or 2
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
            "model": image_provider_label(provider_name),
            "size": size,
            "quality": quality,
            "provider": provider_name,
            "n": n,
        }

    style = load_style(project_dir)
    bundle = create_image_provider(provider_name=provider_name, size=size, quality=quality)
    provider = bundle.provider
    metadata = bundle.metadata
    picks_path = project_dir / "PICKS.json"
    picks = json.loads(picks_path.read_text(encoding="utf-8"))
    picks.setdefault("selections", {})
    picks.setdefault("auto_picked", [])
    picks.setdefault("overridden", [])

    made = cached = 0
    failed: list[dict[str, str]] = []
    protected: list[str] = []
    for index, shot in enumerate(shots, start=1):
        sid = shot["id"]
        prompt = (shot.get("prompt") or "").strip()
        full_prompt = f"{prompt}\n\n{style}" if prompt else style
        gen_dir = project_dir / "SHOTS" / sid / GEN_IMG_SUBDIR
        key = provider.cache_key(full_prompt)
        # Cache check searches the new layout first, then the legacy one, so an
        # old "images/openai" project is recognized as cached without migration.
        status = "new"
        active_subdir = GEN_IMG_SUBDIR  # where candidate_001.png for this shot lives
        if not force:
            for subdir in IMG_SUBDIRS:
                candidate_dir = project_dir / "SHOTS" / sid / subdir
                key_path = candidate_dir / ".key"
                existing = sorted(candidate_dir.glob("candidate_*.png"))
                if (
                    len(existing) >= n
                    and key_path.exists()
                    and key_path.read_text(encoding="utf-8").strip() == key
                ):
                    status = "cache"
                    active_subdir = subdir
                    break
        # Would regeneration delete the file the human explicitly picked?
        selection = picks["selections"].get(sid, "")
        selection_name = selection.split("/")[-1] if selection else ""
        selection_at_risk = (
            any(selection.startswith(f"{subdir}/candidate_") for subdir in IMG_SUBDIRS)
            and (project_dir / "SHOTS" / sid / selection).is_file()
        )
        if status == "new" and not force and sid in picks["overridden"] and selection_at_risk:
            # The human picked this candidate in the picker; never silently delete
            # their choice — require --force for an explicit regeneration.
            print(f"[images] {sid}: picker에서 직접 선택한 후보가 있어 재생성 건너뜀 (--force 로 재생성)")
            protected.append(sid)
            status = "keep"
        if status == "new":
            try:
                blobs = _generate_with_retry(provider, full_prompt, n, sid)
            except Exception as exc:  # isolate one bad shot; keep the batch alive
                failed.append({"shot_id": sid, "error": str(exc)[:200]})
                if progress:
                    progress(index, len(shots), sid, "FAIL", 0.0)
                continue
            # Drop stale auto candidates in every layout dir (external_* survive),
            # then write the fresh batch into the provider-neutral gen dir.
            for subdir in IMG_SUBDIRS:
                _clear_auto_candidates(project_dir / "SHOTS" / sid / subdir)
            gen_dir.mkdir(parents=True, exist_ok=True)
            for slot, blob in enumerate(blobs, start=1):
                (gen_dir / f"candidate_{slot:03d}.png").write_bytes(blob)
            _atomic_write_text(gen_dir / ".key", key)
            active_subdir = GEN_IMG_SUBDIR
            made += 1
            if selection_at_risk and sid in picks["overridden"]:
                # The picked file is gone — demote the shot to auto-picked so the
                # selection resets to the fresh candidate_001 below.
                picks["overridden"] = [s for s in picks["overridden"] if s != sid]
                print(
                    f"[images] {sid}: 선택했던 {selection_name} 이(가) 재생성으로 삭제됨 "
                    "→ 선택을 candidate_001 로 초기화 (picker에서 다시 고를 수 있음)"
                )
        else:
            cached += 1
        if sid not in picks["overridden"]:
            picks["selections"][sid] = f"{active_subdir}/candidate_001.png"
            if sid not in picks["auto_picked"]:
                picks["auto_picked"].append(sid)
        else:
            picks["auto_picked"] = [s for s in picks["auto_picked"] if s != sid]
        if progress:
            progress(index, len(shots), sid, status, 0.0)

    _atomic_write_json(picks_path, picks)
    summary: dict[str, Any] = {
        "kind": "images",
        "made": made,
        "cached": cached,
        "failed": failed,
        "protected": protected,
        "total": len(shots),
        "provider": metadata["provider"],
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
    apply_project_settings(project_dir)
    provider_name = os.environ.get("IMAGE_PROVIDER", "openai").strip().lower()
    quality = quality or os.environ.get("IMAGE_QUALITY", "medium")
    size = size or os.environ.get("IMAGE_SIZE", "1536x1024")

    visuals_path = project_dir / "VISUALS.json"
    visuals = json.loads(visuals_path.read_text(encoding="utf-8"))
    shot = next((s for s in visuals["shots"] if s["id"] == shot_id), None)
    if shot is None:
        raise ValueError(f"shot 없음: {shot_id}")

    if prompt is not None and prompt.strip():
        shot["prompt"] = prompt.strip()
        _atomic_write_json(visuals_path, visuals)
        _sync_shot_prompt(project_dir, shot_id, prompt.strip())
    base_prompt = (shot.get("prompt") or "").strip()
    style = load_style(project_dir)
    full_prompt = f"{base_prompt}\n\n{style}" if base_prompt else style

    bundle = create_image_provider(provider_name=provider_name, size=size, quality=quality)
    provider = bundle.provider
    img_dir = project_dir / "SHOTS" / shot_id / GEN_IMG_SUBDIR  # appends always land in the neutral dir
    img_dir.mkdir(parents=True, exist_ok=True)
    # Number after the highest candidate in *any* layout dir so a legacy
    # images/openai project never gets a colliding candidate name.
    existing = [
        p
        for subdir in IMG_SUBDIRS
        for p in (project_dir / "SHOTS" / shot_id / subdir).glob("candidate_*.png")
    ]
    next_idx = 1 + max([_candidate_index(p) for p in existing], default=0)

    blobs = provider.generate(full_prompt, n=n)
    new_names = []
    for offset, blob in enumerate(blobs):
        name = f"candidate_{next_idx + offset:03d}.png"
        (img_dir / name).write_bytes(blob)
        new_names.append(name)
    # Refresh the cache-key sidecar so the next `weft images` run sees these
    # candidates as up to date for the (possibly edited) prompt instead of
    # wiping the human's picks and re-billing a full regeneration.
    _atomic_write_text(img_dir / ".key", provider.cache_key(full_prompt))
    return {"shot_id": shot_id, "new": new_names, "prompt": base_prompt}


def _candidate_index(path: Path) -> int:
    try:
        return int(path.stem.split("_")[-1])
    except ValueError:
        return 0


def _is_rate_limit_error(exc: Exception) -> bool:
    try:
        import openai  # optional dependency (absent for stub provider)
    except ImportError:
        return False
    return isinstance(exc, openai.RateLimitError)


def _generate_with_retry(provider: Any, full_prompt: str, n: int, shot_id: str) -> list[bytes]:
    """One generate call; on a provider rate limit, back off and retry once."""
    try:
        return provider.generate(full_prompt, n=n)
    except Exception as exc:
        if not _is_rate_limit_error(exc):
            raise
        wait = 15.0
        print(f"[images] {shot_id}: rate limit — {wait:.0f}s 대기 후 1회 재시도")
        time.sleep(wait)
        return provider.generate(full_prompt, n=n)


def _clear_auto_candidates(img_dir: Path) -> None:
    for path in img_dir.glob("candidate_*.png"):
        path.unlink()


def _sync_shot_prompt(project_dir: Path, shot_id: str, prompt: str) -> None:
    shot_dir = project_dir / "SHOTS" / shot_id
    if not shot_dir.is_dir():  # never create ghost SHOTS dirs for unknown ids
        return
    shot_json = shot_dir / "SHOT.json"
    if shot_json.is_file():
        data = json.loads(shot_json.read_text(encoding="utf-8"))
        data["prompt"] = prompt
        _atomic_write_json(shot_json, data)
    _atomic_write_text(shot_dir / "PROMPT.md", prompt + "\n")


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
