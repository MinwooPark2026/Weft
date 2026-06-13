"""Build a CapCut 8.6.0 (macOS) draft from a Weft render_plan.json.

Strategy:
- Clone an existing *empty* CapCut draft folder as the skeleton (gives us all the
  opaque config/platform/function_assistant_info scaffolding at the exact app
  version), then inject our materials + tracks into the root draft_info.json and
  its Timelines mirror.
- Time unit is microseconds. render_plan start/end are 48kHz sample integers.
- Photo (still image) -> materials.videos[] with type "photo". Audio WAV ->
  materials.audios[]. Ken Burns motion -> segment.common_keyframes.
- Subtitles stay as SRT import (texts[] schema is unverified on this version).

UNVERIFIED on 8.6.0 (flagged by the spec, confirm via round-trip): photo
type/duration sentinel, audio material type, keyframe key names. Build, open in
CapCut, re-save, re-read to confirm.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

CANVAS_W = 1920
CANVAS_H = 1080
DEFAULT_ROOT = Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"

ZOOM_RATIO = 1.12  # zoom_in/out scale change over the clip
PAN_SHIFT = 0.10   # pan position offset in half-canvas units
FADE_US = 500_000  # fade-in length


def capcut_running() -> bool:
    try:
        return subprocess.run(["pgrep", "-x", "CapCut"], capture_output=True).returncode == 0
    except Exception:
        return False


def us(samples: int, sample_rate: int = 48_000) -> int:
    return round(samples / sample_rate * 1_000_000)


def _uuid_u() -> str:
    return str(uuid.uuid4()).upper()


def _uuid_l() -> str:
    return str(uuid.uuid4())


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _find_skeleton(root: Path, exclude_name: str | None = None) -> Path:
    """Pick an empty draft (tracks==[]) to clone; fall back to the smallest draft.

    Weft's own outputs (the destination folder, prior weft_* drafts, *.weft_bak_*
    archives) are never candidates — choosing the destination itself as skeleton
    would archive it away and then fail the copy with FileNotFoundError.
    """
    candidates = []
    for child in sorted(root.iterdir()):
        name = child.name
        if name == exclude_name or name.startswith("weft_") or ".weft_bak" in name:
            continue
        info = child / "draft_info.json"
        if not info.is_file():
            continue
        try:
            data = _load(info)
        except Exception:
            continue
        candidates.append((len(data.get("tracks", [])), info.stat().st_size, child))
    if not candidates:
        raise RuntimeError(f"복제할 CapCut draft 스켈레톤을 찾지 못함: {root}")
    candidates.sort()  # fewest tracks, then smallest
    return candidates[0][2]


# ---------------------------------------------------------------- materials ---

def _singletons() -> tuple[dict[str, dict], list[str]]:
    speed = {"id": _uuid_l(), "curve_speed": None, "mode": 0, "speed": 1.0, "type": "speed"}
    ph = {"id": _uuid_l(), "error_path": "", "error_text": "", "meta_type": "none",
          "res_path": "", "res_text": "", "type": "placeholder_info"}
    canvas = {"id": _uuid_l(), "album_image": "", "blur": 0.0, "color": "", "image": "",
              "image_id": "", "image_name": "", "source_platform": 0, "team_id": "", "type": "canvas_color"}
    scm = {"id": _uuid_l(), "audio_channel_mapping": 0, "is_config_open": False, "type": ""}
    mc = {"id": _uuid_l(), "gradient_angle": 90.0, "gradient_colors": [], "gradient_percents": [],
          "height": 0.0, "is_color_clip": False, "is_gradient": False, "solid_color": "", "width": 0.0}
    vs = {"id": _uuid_l(), "choice": 0, "enter_from": "", "final_algorithm": "", "production_path": "",
          "removed_sounds": [], "time_range": None, "type": "vocal_separation"}
    refs = [speed["id"], ph["id"], canvas["id"], scm["id"], mc["id"], vs["id"]]
    return {"speeds": speed, "placeholder_infos": ph, "canvases": canvas,
            "sound_channel_mappings": scm, "material_colors": mc, "vocal_separations": vs}, refs


def _photo_material(path: str, width: int, height: int) -> dict:
    return {
        "id": _uuid_l(), "type": "photo", "has_audio": False, "duration": 10_800_000_000,
        "width": width, "height": height, "path": path,
        "material_name": os.path.basename(path), "local_material_id": _uuid_l(),
        "category_name": "local", "category_id": "", "check_flag": 62978047,
        "crop": {"lower_left_x": 0.0, "lower_left_y": 1.0, "lower_right_x": 1.0, "lower_right_y": 1.0,
                 "upper_left_x": 0.0, "upper_left_y": 0.0, "upper_right_x": 1.0, "upper_right_y": 0.0},
        "crop_ratio": "free", "crop_scale": 1.0,
        "matting": {"cloud_product_fps": 0.0, "custom_matting_id": "", "enable_matting_stroke": False,
                    "expansion": 0, "feather": 0, "flag": 0, "has_use_quick_brush": False,
                    "has_use_quick_eraser": False, "interactiveTime": [], "is_clould": False,
                    "mask_video_path": "", "path": "", "reverse": False, "strokes": []},
        "video_algorithm": {"ai_background_configs": [], "ai_expression_driven": None,
                            "ai_in_painting_config": [], "ai_motion_driven": None, "aigc_generate": None,
                            "aigc_generate_list": [], "algorithms": [], "complement_frame_config": None,
                            "deflicker": None, "gameplay_configs": [], "image_interpretation": None,
                            "motion_blur_config": None, "mouth_shape_driver": None, "noise_reduction": None,
                            "path": "", "quality_enhance": None, "skip_algorithm_index": [],
                            "smart_complement_frame": None,
                            "story_video_modify_video_config": {"is_overwrite_last_video": False,
                                                                "task_id": "", "tracker_task_id": ""},
                            "super_resolution": None, "time_range": None},
        "video_mask_shadow": {"alpha": 0.0, "angle": 0.0, "blur": 0.0, "color": "", "distance": 0.0,
                              "path": "", "resource_id": ""},
        "video_mask_stroke": {"alpha": 0.0, "color": "", "distance": 0.0, "horizontal_shift": 0.0,
                              "path": "", "resource_id": "", "size": 0.0, "texture": 0.0, "type": "",
                              "vertical_shift": 0.0},
        "stable": {"matrix_path": "", "stable_level": 0, "time_range": {"duration": 0, "start": 0}},
        "beauty_face_auto_preset": {"name": "", "preset_id": "", "rate_map": "", "scene": ""},
        "beauty_face_auto_preset_infos": [], "beauty_face_preset_infos": [],
        "beauty_body_auto_preset": None, "beauty_body_preset_id": "",
        "aigc_history_id": "", "aigc_item_id": "", "aigc_type": "none", "audio_fade": None,
        "cartoon_path": "", "content_feature_info": None, "corner_pin": None, "extra_type_option": 0,
        "formula_id": "", "freeze": None, "has_sound_separated": False, "intensifies_audio_path": "",
        "intensifies_path": "", "is_ai_generate_content": False, "is_copyright": False,
        "is_set_beauty_mode": False, "is_text_edit_overdub": False, "is_unified_beauty_mode": False,
        "live_photo_cover_path": "", "live_photo_timestamp": -1, "local_id": "", "local_material_from": "",
        "material_id": "", "material_url": "", "media_path": "", "multi_camera_info": None,
        "object_locked": None, "origin_material_id": "", "picture_from": "none",
        "picture_set_category_id": "", "picture_set_category_name": "", "request_id": "",
        "reverse_intensifies_path": "", "reverse_path": "", "smart_match_info": None, "smart_motion": None,
        "source": 0, "source_platform": 0, "surface_trackings": [], "team_id": "", "unique_id": "",
    }


def _video_material(path: str, width: int, height: int, duration_us: int) -> dict:
    material = _photo_material(path, width, height)
    material["type"] = "video"
    material["duration"] = duration_us
    material["has_audio"] = False
    material["material_name"] = os.path.basename(path)
    return material


def _audio_material(path: str, duration_us: int) -> dict:
    mid = _uuid_l()
    return {
        "id": mid, "type": "extract_music", "name": os.path.basename(path), "path": path,
        "duration": duration_us, "music_id": mid, "local_material_id": _uuid_l(),
        "category_id": "", "category_name": "local", "check_flag": 1, "app_id": 0,
        "copyright_limit_type": "none", "effect_id": "", "formula_id": "", "intensifies_path": "",
        "request_id": "", "resource_id": "", "source_platform": 0, "team_id": "",
        "is_ai_clone_tone": False, "is_text_edit_overdub": False, "is_ugc": False, "wave_points": [],
    }


# ---------------------------------------------------------------- keyframes ---

def _kf(time_offset: int, value: float) -> dict:
    return {"curveType": "Line", "graphID": "", "left_control": {"x": 0.0, "y": 0.0},
            "right_control": {"x": 0.0, "y": 0.0}, "id": _uuid_l(),
            "time_offset": time_offset, "values": [value]}


def _kflist(prop: str, frames: list[tuple[int, float]]) -> dict:
    return {"id": _uuid_l(), "keyframe_list": [_kf(t, v) for t, v in frames],
            "material_id": "", "property_type": prop}


def _motion(motion_type: str, dur_us: int, base: float) -> tuple[list[dict], float, float, bool]:
    """Return (common_keyframes, clip_scale, clip_alpha, uniform_scale_on)."""
    if motion_type == "zoom_in":
        return ([_kflist("KFTypeScaleX", [(0, base), (dur_us, base * ZOOM_RATIO)]),
                 _kflist("KFTypeScaleY", [(0, base), (dur_us, base * ZOOM_RATIO)])], base, 1.0, False)
    if motion_type == "zoom_out":
        hi = base * ZOOM_RATIO
        return ([_kflist("KFTypeScaleX", [(0, hi), (dur_us, base)]),
                 _kflist("KFTypeScaleY", [(0, hi), (dur_us, base)])], hi, 1.0, False)
    if motion_type in ("pan_lr", "pan_rl"):
        a, b = (-PAN_SHIFT, PAN_SHIFT) if motion_type == "pan_lr" else (PAN_SHIFT, -PAN_SHIFT)
        # cover scale leaves zero margin, so a bare PositionX shift exposes the
        # background; zoom in slightly (like the ffmpeg path) to buy pan headroom.
        return ([_kflist("KFTypePositionX", [(0, a), (dur_us, b)])], base * ZOOM_RATIO, 1.0, False)
    if motion_type == "fade":
        end = min(FADE_US, dur_us)
        return ([_kflist("KFTypeAlpha", [(0, 0.0), (end, 1.0)])], base, 1.0, True)
    return ([], base, 1.0, True)  # static


def _image_segment(material_id: str, refs: list[str], start_us: int, dur_us: int,
                   common_keyframes: list[dict], scale: float, alpha: float, uniform_on: bool) -> dict:
    return {
        "id": _uuid_l(), "material_id": material_id, "extra_material_refs": refs,
        "target_timerange": {"start": start_us, "duration": dur_us},
        "source_timerange": {"start": 0, "duration": dur_us},
        "render_timerange": {"start": 0, "duration": 0},
        "clip": {"alpha": alpha, "flip": {"horizontal": False, "vertical": False}, "rotation": 0.0,
                 "scale": {"x": scale, "y": scale}, "transform": {"x": 0.0, "y": 0.0}},
        "common_keyframes": common_keyframes, "keyframe_refs": [], "caption_info": None, "cartoon": False,
        "color_correct_alg_result": "", "desc": "", "digital_human_template_group_id": "",
        "enable_adjust": True, "enable_adjust_mask": False, "enable_color_adjust_pro": False,
        "enable_color_correct_adjust": False, "enable_color_curves": True, "enable_color_match_adjust": False,
        "enable_color_wheels": True, "enable_hsl": False, "enable_hsl_curves": True, "enable_lut": True,
        "enable_mask_shadow": False, "enable_mask_stroke": False, "enable_smart_color_adjust": False,
        "enable_video_mask": True, "group_id": "",
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
        "intensifies_audio": False, "is_loop": False, "is_placeholder": False, "is_tone_modify": False,
        "last_nonzero_volume": 1.0, "lyric_keyframes": None, "raw_segment_id": "", "render_index": 0,
        "track_attribute": 0, "track_render_index": 0,
        "responsive_layout": {"enable": False, "horizontal_pos_layout": 0, "size_layout": 0,
                              "target_follow": "", "vertical_pos_layout": 0},
        "reverse": False, "source": "segmentsourcenormal", "speed": 1.0, "state": 0,
        "template_id": "", "template_scene": "default",
        "uniform_scale": {"on": uniform_on, "value": 1.0}, "visible": True, "volume": 1.0,
    }


def _audio_segment(material_id: str, refs: list[str], start_us: int, dur_us: int) -> dict:
    return {
        "id": _uuid_l(), "material_id": material_id, "extra_material_refs": refs,
        "target_timerange": {"start": start_us, "duration": dur_us},
        "source_timerange": {"start": 0, "duration": dur_us},
        "render_timerange": {"start": 0, "duration": 0},
        "speed": 1.0, "volume": 1.0, "last_nonzero_volume": 1.0,
        "clip": None, "hdr_settings": None, "uniform_scale": None,
        "common_keyframes": [], "keyframe_refs": [], "caption_info": None, "cartoon": False,
        "enable_adjust": False, "enable_color_curves": True, "enable_color_match_adjust": False,
        "enable_color_wheels": True, "enable_lut": False, "enable_smart_color_adjust": False,
        "group_id": "", "intensifies_audio": False, "is_loop": False, "is_placeholder": False,
        "is_tone_modify": False, "render_index": 0, "track_attribute": 0, "track_render_index": 0,
        "responsive_layout": {"enable": False, "horizontal_pos_layout": 0, "size_layout": 0,
                              "target_follow": "", "vertical_pos_layout": 0},
        "reverse": False, "source": "segmentsourcenormal", "state": 0, "template_id": "",
        "template_scene": "default", "visible": True,
    }


# --------------------------------------------------------------- card pngs ---

def _localize(src_abs: Path, materials_dir: Path, cache: dict[str, str], rel_name: str) -> str:
    """Copy an asset into the draft's materials/ folder (inside ~/Movies, where the
    sandboxed CapCut can read it) and return the copied absolute path. Cached by source."""
    key = str(src_abs)
    if key in cache:
        return cache[key]
    dst = materials_dir / rel_name
    shutil.copy2(src_abs, dst)
    cache[key] = str(dst)
    return str(dst)


_KOREAN_FONTS = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/NanumGothic.ttf",
]


def _kfont(size: int):
    from PIL import ImageFont
    for path in _KOREAN_FONTS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _fit_font(draw, text: str, max_w: int, max_h: int, spacing: int):
    for size in range(116, 32, -6):
        font = _kfont(size)
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align="center")
        if (bbox[2] - bbox[0]) <= max_w and (bbox[3] - bbox[1]) <= max_h:
            return font
    return _kfont(34)


def _make_card_png(shot_id: str, kind: str, out_dir: Path, text: str | None = None) -> Path:
    """Typographic card PNG (CapCut can't read SVG). Warm-dark bg + cream text + amber accent."""
    from PIL import Image, ImageDraw

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{shot_id}.png"
    bg = (26, 32, 46)
    cream = (244, 237, 219)
    amber = (228, 156, 78)
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), bg)
    draw = ImageDraw.Draw(img)
    content = text or shot_id
    spacing = 28
    font = _fit_font(draw, content, max_w=1520, max_h=720, spacing=spacing)
    draw.multiline_text((CANVAS_W / 2, CANVAS_H / 2 - 30), content, font=font, fill=cream,
                        anchor="mm", align="center", spacing=spacing)
    draw.rectangle([CANVAS_W / 2 - 80, CANVAS_H / 2 + 230, CANVAS_W / 2 + 80, CANVAS_H / 2 + 238], fill=amber)
    img.save(out)
    return out


_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
_FALLBACK_DIMS = (1536, 1024)


def _img_dims(path: str) -> tuple[int, int]:
    """Pixel dimensions of an image or video asset.

    PIL cannot open videos; a silent fallback there miscomputes the cover scale
    (e.g. a 320x180 clip got scale 1.25 instead of 6.0). Probe videos with ffprobe.
    """
    if Path(path).suffix.lower() in _VIDEO_EXTS:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, check=True,
            )
            parts = [p for p in result.stdout.strip().splitlines()[0].split(",") if p]
            w, h = int(parts[0]), int(parts[1])
            if w > 0 and h > 0:
                return (w, h)
        except Exception:
            pass
        return _FALLBACK_DIMS
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return _FALLBACK_DIMS


def _audio_duration_us(path: str) -> int | None:
    """Audio file duration in microseconds via ffprobe; None when unknown."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        )
        seconds = float(result.stdout.strip().splitlines()[0])
        if seconds > 0:
            return round(seconds * 1_000_000)
    except Exception:
        pass
    return None


# ------------------------------------------------------------------- build ---

def build_capcut_draft(
    project_dir: str | Path,
    *,
    folder_name: str = "weft_draft",
    capcut_root: str | Path | None = None,
    with_motion: bool = True,
    with_audio: bool = True,
    images_only: bool = False,
    limit: int | None = None,
    register: bool = True,
    bgm: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    base = project_dir  # asset paths in render_plan are relative to project_dir
    root = Path(capcut_root) if capcut_root else DEFAULT_ROOT
    if not root.is_dir():
        raise RuntimeError(f"CapCut 프로젝트 폴더 없음: {root} (CapCut 설치/경로 확인)")

    render_plan = _load(project_dir / "EXPORTS" / "render_plan.json")
    video_events = render_plan["video"]
    audio_events = render_plan["audio"] if with_audio else []
    if images_only:
        video_events = [v for v in video_events if v.get("source_kind") == "image"]
    if limit:
        video_events = video_events[:limit]
        if audio_events:
            cutoff = max((us(v["end"]) for v in video_events), default=0)
            audio_events = [a for a in audio_events if us(a["start"]) < cutoff]

    # Fail early (before touching the CapCut root) when a shot has no picked asset:
    # an empty src would resolve to the project directory itself and crash later
    # with an opaque IsADirectoryError while copying.
    unpicked = [str(v.get("shot_id") or f"video[{i}]") for i, v in enumerate(video_events) if not (v.get("src") or "")]
    if unpicked:
        raise RuntimeError(
            "선택된 이미지(src)가 없는 shot이 있습니다: " + ", ".join(unpicked)
            + ". `weft pick`으로 후보를 선택하거나 PICKS.json을 채운 뒤 다시 실행하세요."
        )

    skeleton = _find_skeleton(root, exclude_name=folder_name)
    dest = root / folder_name
    backup: Path | None = None
    if dest.exists():
        backup = _archive_existing_draft(dest)
    shutil.copytree(skeleton, dest)

    # fresh ids so the new draft never collides with the skeleton's
    timeline_id = _uuid_u()
    project_id = _uuid_u()
    draft_id = _uuid_u()

    info = _load(dest / "draft_info.json")
    info["id"] = timeline_id
    info["color_space"] = 0  # match a known-good rendered draft (0522), not -1 skeleton
    info["draft_type"] = ""
    mats = info["materials"]
    for key in ("videos", "audios", "speeds", "placeholder_infos", "canvases",
                "sound_channel_mappings", "material_colors", "vocal_separations"):
        mats.setdefault(key, [])

    cards_path = project_dir / "CARDS.json"
    card_text = _load(cards_path) if cards_path.is_file() else {}

    materials_dir = dest / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)
    copy_cache: dict[str, str] = {}
    material_by_path: dict[str, str] = {}
    dims_by_path: dict[str, tuple[int, int]] = {}
    video_segments: list[dict] = []

    for ev in video_events:
        src = ev.get("src") or ""
        kind = ev.get("source_kind", "image")
        if src.endswith(".svg"):
            asset = str(_make_card_png(ev["shot_id"], kind, materials_dir, card_text.get(ev["shot_id"])))
        else:
            asset = _localize((base / src).resolve(), materials_dir, copy_cache, src.replace("/", "_"))
        if asset not in dims_by_path:
            dims_by_path[asset] = _img_dims(asset)
        w, h = dims_by_path[asset]
        if asset not in material_by_path:
            start_us = us(ev["start"])
            dur_us = us(ev["end"]) - start_us
            if kind in {"clip", "stock_clip", "remotion", "hyperframe"}:
                pm = _video_material(asset, w, h, dur_us)
            else:
                pm = _photo_material(asset, w, h)
            mats["videos"].append(pm)
            material_by_path[asset] = pm["id"]
        material_id = material_by_path[asset]
        cover = max(CANVAS_W / w, CANVAS_H / h)
        start_us = us(ev["start"])
        dur_us = us(ev["end"]) - start_us
        motion = ev.get("motion", {}).get("type", "static") if with_motion else "static"
        ckf, scale, alpha, uniform_on = _motion(motion, dur_us, cover)
        singles, refs = _singletons()
        for cat, obj in singles.items():
            mats[cat].append(obj)
        video_segments.append(
            _image_segment(material_id, refs, start_us, dur_us, ckf, scale, alpha, uniform_on)
        )

    audio_segments: list[dict] = []
    for ev in audio_events:
        asset = _localize((base / ev["src"]).resolve(), materials_dir, copy_cache, ev["src"].replace("/", "_"))
        start_us = us(ev["start"])
        dur_us = us(ev["end"]) - start_us
        am = _audio_material(asset, dur_us)
        mats["audios"].append(am)
        speed = {"id": _uuid_l(), "curve_speed": None, "mode": 0, "speed": 1.0, "type": "speed"}
        scm = {"id": _uuid_l(), "audio_channel_mapping": 0, "is_config_open": False, "type": ""}
        vs = {"id": _uuid_l(), "choice": 0, "enter_from": "", "final_algorithm": "",
              "production_path": "", "removed_sounds": [], "time_range": None, "type": "vocal_separation"}
        mats["speeds"].append(speed)
        mats["sound_channel_mappings"].append(scm)
        mats["vocal_separations"].append(vs)
        audio_segments.append(_audio_segment(am["id"], [speed["id"], scm["id"], vs["id"]], start_us, dur_us))

    # BGM: simple placement on its own audio track — one segment per BGM.json
    # entry (or the single BGM_FILE), base volume from gain_db. Ducking and exact
    # fades are left to the user inside CapCut; the ffmpeg render does them
    # automatically. CapCut cannot loop a clip, so a song shorter than its span
    # is placed once at the span start (capped to the song length).
    bgm_segments: list[dict] = []
    if bgm and not images_only:
        from .ffmpeg_render import resolve_bgm_segments  # lazy: avoids a circular import

        plan_total_seconds = render_plan["total_samples"] / int(render_plan.get("sample_rate", 48_000))
        for seg in resolve_bgm_segments(bgm, plan_total_seconds):
            asset = _localize(
                Path(seg["path"]).resolve(), materials_dir, copy_cache, "bgm_" + os.path.basename(seg["path"])
            )
            start_us = round(seg["start"] * 1_000_000)
            dur_us = round((seg["end"] - seg["start"]) * 1_000_000)
            source_us = _audio_duration_us(asset)
            if source_us:
                dur_us = min(dur_us, source_us)
            am = _audio_material(asset, dur_us)  # same verified material shape as narration audio
            mats["audios"].append(am)
            speed = {"id": _uuid_l(), "curve_speed": None, "mode": 0, "speed": 1.0, "type": "speed"}
            scm = {"id": _uuid_l(), "audio_channel_mapping": 0, "is_config_open": False, "type": ""}
            vs = {"id": _uuid_l(), "choice": 0, "enter_from": "", "final_algorithm": "",
                  "production_path": "", "removed_sounds": [], "time_range": None, "type": "vocal_separation"}
            mats["speeds"].append(speed)
            mats["sound_channel_mappings"].append(scm)
            mats["vocal_separations"].append(vs)
            segment = _audio_segment(am["id"], [speed["id"], scm["id"], vs["id"]], start_us, dur_us)
            volume = round(10 ** (float(seg["gain_db"]) / 20), 4)  # dB → CapCut linear volume
            segment["volume"] = volume
            segment["last_nonzero_volume"] = volume
            bgm_segments.append(segment)

    total_us = us(render_plan["total_samples"]) if not limit else max(
        [s["target_timerange"]["start"] + s["target_timerange"]["duration"] for s in video_segments] or [0]
    )
    info["duration"] = total_us
    tracks = [{"attribute": 0, "flag": 0, "id": _uuid_u(), "is_default_name": True, "name": "",
               "type": "video", "segments": video_segments}]
    if audio_segments:
        tracks.append({"attribute": 0, "flag": 0, "id": _uuid_u(), "is_default_name": True, "name": "",
                       "type": "audio", "segments": audio_segments})
    if bgm_segments:
        tracks.append({"attribute": 0, "flag": 0, "id": _uuid_u(), "is_default_name": True, "name": "",
                       "type": "audio", "segments": bgm_segments})
    info["tracks"] = tracks

    _dump(dest / "draft_info.json", info)
    _wire_skeleton(dest, info, timeline_id, project_id)
    _write_draft_meta(dest, folder_name, draft_id, total_us)  # always — part of the draft folder
    if register:
        append_root_meta(root, dest, folder_name, draft_id, total_us)  # needs CapCut closed

    return {
        "folder": str(dest),
        "video_segments": len(video_segments),
        "audio_segments": len(audio_segments),
        "bgm_segments": len(bgm_segments),
        "photo_materials": len(mats["videos"]),
        "total_seconds": round(total_us / 1_000_000, 2),
        "registered": register,
        "backup": str(backup) if backup else None,
    }


def _archive_existing_draft(dest: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = dest.with_name(f"{dest.name}.weft_bak_{stamp}")
    suffix = 1
    while backup.exists():
        backup = dest.with_name(f"{dest.name}.weft_bak_{stamp}_{suffix}")
        suffix += 1
    shutil.move(str(dest), str(backup))
    return backup


def _wire_skeleton(dest: Path, info: dict, timeline_id: str, project_id: str) -> None:
    """Mirror draft_info into Timelines/<id>/, fix all the linked ids."""
    timelines_dir = dest / "Timelines"
    if timelines_dir.is_dir():
        # rename the cloned child folder to our timeline_id and mirror content
        children = [c for c in timelines_dir.iterdir() if c.is_dir()]
        mirror = timelines_dir / timeline_id
        if children:
            children[0].rename(mirror)
        else:
            mirror.mkdir(parents=True, exist_ok=True)
        _dump(mirror / "draft_info.json", info)
        proj_path = timelines_dir / "project.json"
        if proj_path.is_file():
            proj = _load(proj_path)
            proj["id"] = project_id
            proj["main_timeline_id"] = timeline_id
            if proj.get("timelines"):
                proj["timelines"][0]["id"] = timeline_id
            _dump(proj_path, proj)
    layout_path = dest / "timeline_layout.json"
    if layout_path.is_file():
        layout = _load(layout_path)
        if layout.get("dockItems"):
            layout["dockItems"][0]["timelineIds"] = [timeline_id]
        _dump(layout_path, layout)


def _write_draft_meta(dest: Path, folder_name: str, draft_id: str, total_us: int) -> None:
    now_us = int(time.time()) * 1_000_000
    meta_path = dest / "draft_meta_info.json"
    if not meta_path.is_file():
        raise RuntimeError(
            f"스켈레톤 draft에 draft_meta_info.json이 없습니다: {meta_path}. "
            "CapCut에서 빈 프로젝트를 새로 하나 만들어 정상 스켈레톤을 확보한 뒤 다시 실행하세요."
        )
    meta = _load(meta_path)
    meta["draft_fold_path"] = str(dest)
    meta["draft_name"] = folder_name
    meta["draft_id"] = draft_id
    meta["tm_draft_create"] = now_us
    meta["tm_draft_modified"] = now_us
    meta["tm_duration"] = total_us
    meta["draft_timeline_materials_size_"] = 0  # NOT the byte size (verified)
    _dump(meta_path, meta)


def append_root_meta(root: Path, dest: Path, folder_name: str, draft_id: str, total_us: int) -> None:
    """Append this draft to CapCut's project catalog. Run while CapCut is CLOSED —
    CapCut rewrites root_meta_info.json from memory on quit and would drop the entry."""
    now_us = int(time.time()) * 1_000_000
    info_bytes = (dest / "draft_info.json").stat().st_size
    root_meta_path = root / "root_meta_info.json"
    if not root_meta_path.is_file():
        return
    shutil.copy2(root_meta_path, root_meta_path.with_suffix(".json.weft_bak"))
    root_meta = _load(root_meta_path)
    store = root_meta.setdefault("all_draft_store", [])
    store = [d for d in store if d.get("draft_fold_path") != str(dest)]  # replace if rerun
    entry = {
        "cloud_draft_cover": False, "cloud_draft_sync": False,
        "draft_cloud_last_action_download": False, "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "", "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": str(dest / "draft_cover.jpg"), "draft_fold_path": str(dest),
        "draft_id": draft_id, "draft_is_ai_shorts": False, "draft_is_cloud_temp_draft": False,
        "draft_is_invisible": False, "draft_is_web_article_video": False,
        "draft_json_file": str(dest / "draft_info.json"), "draft_name": folder_name,
        "draft_new_version": "", "draft_root_path": str(root),
        "draft_timeline_materials_size": info_bytes, "draft_type": "",
        "draft_web_article_video_enter_from": "", "streaming_edit_draft_ready": True,
        "tm_draft_cloud_completed": "", "tm_draft_cloud_entry_id": -1, "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1, "tm_draft_cloud_space_id": -1, "tm_draft_cloud_user_id": -1,
        "tm_draft_create": now_us, "tm_draft_modified": now_us, "tm_draft_removed": 0,
        "tm_duration": total_us,
    }
    store.append(entry)
    root_meta["all_draft_store"] = store
    root_meta["draft_ids"] = len(store)
    _dump(root_meta_path, root_meta)
