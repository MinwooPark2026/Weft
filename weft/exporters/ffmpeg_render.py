"""Render a Weft project directly to MP4 with ffmpeg.

This exporter is for quick final previews and shareable files. CapCut remains the
editable export path; ffmpeg turns the compiled render_plan into a rendered video.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import wave
from array import array
from pathlib import Path
from typing import Any

from ..timecode import parse_timecode
from .capcut_draft import CANVAS_H, CANVAS_W, FADE_US, ZOOM_RATIO, _make_card_png

DEFAULT_OUTPUT_NAME = "weft_render.mp4"

# ----------------------------------------------------------------------- BGM ---
# Background music is mixed INSIDE the ffmpeg render (no CapCut round-trip):
# the narration premix gets its single loudnorm pass first, then that normalized
# track keys a sidechain compressor that ducks the BGM while narration plays and
# lets it rise back during ⏸ pauses and gaps.
BGM_JSON_NAME = "BGM.json"
BGM_DEFAULT_GAIN_DB = -16.0
BGM_DEFAULT_DUCK_DB = -12.0
BGM_DEFAULT_FADE_SECONDS = 2.0
# 문서상 지원 포맷은 mp3/wav/m4a — 그 외도 ffmpeg 가 디코드 가능한 일반 오디오는 허용.
_BGM_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}

# Ducking → sidechaincompress mapping rationale:
# the narration premix is loudnorm'ed to I=-14 LUFS BEFORE the sidechain tap, so
# active speech averages ~-14 dBFS. With the compressor threshold at -34 dB
# (linear 0.02), speech rides ~20 dB above threshold; a downward compressor with
# ratio R then attenuates the BGM by about 20*(1-1/R) dB while speech plays.
# BGM_DUCK_DB=-12 → R = 20/(20-12) = 2.5. Attack 20 ms dips the BGM as soon as a
# sentence starts; release 400 ms brings it back smoothly in pauses (no pumping).
_DUCK_THRESHOLD_DB = -34.0
_DUCK_HEADROOM_DB = 20.0  # expected speech level above threshold: -14 - (-34)
_DUCK_ATTACK_MS = 20
_DUCK_RELEASE_MS = 400

# Long scripts (200+ beats/subtitles) must not scale ffmpeg inputs/filters with N:
# subtitles are burned from ONE .ass file (libass) and beats are premixed into ONE
# wav, so the ffmpeg invocation stays at (shots + 1) inputs regardless of script size.
ASS_FILE_NAME = "subtitles.ass"
AUDIO_MIX_NAME = "audio_mix.wav"

# Korean-capable font for libass; libass does per-glyph fallback if it is missing,
# and the render is pixel-verified in tests/docs, so a plain family name is enough.
SUBTITLE_FONT = "Apple SD Gothic Neo"

# Homebrew's default `ffmpeg` bottle dropped libass; `ffmpeg-full` keeps it. When the
# configured binary cannot burn subtitles, fall back to a known libass-capable build.
_LIBASS_FALLBACK_BINS = (
    "ffmpeg-full",
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
    "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
)


def render_ffmpeg(
    project_dir: str | Path,
    *,
    output: str | Path | None = None,
    ffmpeg_bin: str = "ffmpeg",
    with_motion: bool = True,
    with_audio: bool = True,
    with_subtitles: bool = True,
    encoder: str = "auto",
    preset: str = "veryfast",
    crf: int = 20,
    bitrate: str = "8M",
    width: int = CANVAS_W,
    height: int = CANVAS_H,
    bgm: list[dict[str, Any]] | None = None,
    bgm_fade_seconds: float = BGM_DEFAULT_FADE_SECONDS,
    bgm_duck_db: float = BGM_DEFAULT_DUCK_DB,
    dry_run: bool = False,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    exports_dir = project_dir / "EXPORTS"
    render_plan = _load(exports_dir / "render_plan.json")
    output_path = Path(output) if output else exports_dir / DEFAULT_OUTPUT_NAME
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fps = int(render_plan.get("fps", 30))
    sample_rate = int(render_plan.get("sample_rate", 48_000))
    total_seconds = float(render_plan.get("total_seconds") or render_plan["total_samples"] / sample_rate)
    video_events = [event for event in render_plan.get("video", []) if event.get("src")]
    if not video_events:
        raise RuntimeError("렌더링할 video 이벤트가 없습니다. 먼저 weft conti/images를 확인하세요.")

    work_dir = exports_dir / "ffmpeg"
    work_dir.mkdir(parents=True, exist_ok=True)
    card_text = _load(project_dir / "CARDS.json") if (project_dir / "CARDS.json").is_file() else {}

    video_assets = [_video_asset(project_dir, work_dir, event, card_text, dry_run=dry_run) for event in video_events]
    subtitle_events = _subtitle_events(render_plan) if with_subtitles else []
    ass_path: Path | None = None
    if subtitle_events:
        ass_path = work_dir / ASS_FILE_NAME
        if not dry_run:  # dry_run only reports the would-be path
            _write_ass(ass_path, subtitle_events, width, height, sample_rate)
    audio_events = _audio_events(project_dir, render_plan) if with_audio else []
    audio_mix = _premix_audio(work_dir, audio_events, sample_rate, total_seconds, dry_run=dry_run)
    bgm_segments = resolve_bgm_segments(bgm, total_seconds) if bgm else []
    # dry_run stays side-effect free: no `ffmpeg -encoders`/filter probes. "auto" maps
    # to the deterministic software encoder so the reported command is still runnable.
    if dry_run:
        render_bin = ffmpeg_bin
        resolved_encoder = encoder if encoder != "auto" else "libx264"
    else:
        render_bin = _resolve_subtitles_bin(ffmpeg_bin) if ass_path is not None else ffmpeg_bin
        resolved_encoder = _resolve_encoder(render_bin, encoder)

    command = _build_command(
        ffmpeg_bin=render_bin,
        output_path=output_path,
        video_events=video_events,
        video_assets=video_assets,
        ass_path=ass_path,
        audio_mix=audio_mix,
        fps=fps,
        sample_rate=sample_rate,
        total_seconds=total_seconds,
        with_motion=with_motion,
        encoder=resolved_encoder,
        preset=preset,
        crf=crf,
        bitrate=bitrate,
        width=width,
        height=height,
        bgm_segments=bgm_segments,
        bgm_fade_seconds=bgm_fade_seconds,
        bgm_duck_db=bgm_duck_db,
    )

    summary: dict[str, Any] = {
        "kind": "ffmpeg",
        "output": str(output_path),
        "video_events": len(video_events),
        "audio_events": len(audio_events),
        "subtitle_events": len(subtitle_events),
        "total_seconds": round(total_seconds, 3),
        "encoder": resolved_encoder,
        "preset": preset,
        "crf": crf,
        "bitrate": bitrate,
        "width": width,
        "height": height,
    }
    if bgm_segments:  # 미설정(기본) 출력과 summary 를 동일하게 유지하려고 BGM 키는 조건부
        summary["bgm_tracks"] = len(bgm_segments)
        summary["bgm"] = [
            {
                "file": Path(segment["path"]).name,
                "start": round(segment["start"], 3),
                "end": round(segment["end"], 3),
                "gain_db": segment["gain_db"],
            }
            for segment in bgm_segments
        ]
    if render_bin != ffmpeg_bin:
        summary["ffmpeg_bin"] = render_bin
    if dry_run:
        summary["command"] = command
        return summary

    started = time.monotonic()
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        if encoder == "auto" and resolved_encoder == "h264_videotoolbox":
            command = _build_command(
                ffmpeg_bin=render_bin,
                output_path=output_path,
                video_events=video_events,
                video_assets=video_assets,
                ass_path=ass_path,
                audio_mix=audio_mix,
                fps=fps,
                sample_rate=sample_rate,
                total_seconds=total_seconds,
                with_motion=with_motion,
                encoder="libx264",
                preset=preset,
                crf=crf,
                bitrate=bitrate,
                width=width,
                height=height,
                bgm_segments=bgm_segments,
                bgm_fade_seconds=bgm_fade_seconds,
                bgm_duck_db=bgm_duck_db,
            )
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as fallback_exc:
                raise _ffmpeg_error(fallback_exc) from fallback_exc
            summary["encoder_fallback_from"] = resolved_encoder
            summary["encoder"] = "libx264"
        else:
            raise _ffmpeg_error(exc) from exc
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg 실행 파일을 찾지 못했습니다. Homebrew 등으로 ffmpeg를 설치하세요.") from exc
    summary["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return summary


def _ffmpeg_error(exc: subprocess.CalledProcessError) -> RuntimeError:
    stderr = (exc.stderr or "").strip()
    tail = "\n".join(stderr.splitlines()[-20:])
    return RuntimeError(f"ffmpeg 렌더링 실패:\n{tail}")


def _build_command(
    *,
    ffmpeg_bin: str,
    output_path: Path,
    video_events: list[dict[str, Any]],
    video_assets: list[Path],
    ass_path: Path | None,
    audio_mix: Path | None,
    fps: int,
    sample_rate: int,
    total_seconds: float,
    with_motion: bool,
    encoder: str,
    preset: str,
    crf: int,
    bitrate: str,
    width: int,
    height: int,
    bgm_segments: list[dict[str, Any]] | None = None,
    bgm_fade_seconds: float = BGM_DEFAULT_FADE_SECONDS,
    bgm_duck_db: float = BGM_DEFAULT_DUCK_DB,
) -> list[str]:
    bgm_segments = bgm_segments or []
    command = [ffmpeg_bin, "-y", "-hide_banner"]
    for asset in video_assets:
        command.extend(["-i", str(asset)])
    if audio_mix is not None:
        command.extend(["-i", str(audio_mix)])
    for segment in bgm_segments:
        # -stream_loop -1: 곡이 구간보다 짧으면 자동 반복 — 구간 길이는 필터의 atrim 이 정확히 끊는다
        command.extend(["-stream_loop", "-1", "-i", str(segment["path"])])

    filters = _video_filters(video_events, video_assets, fps, sample_rate, with_motion, width, height)
    filters.append(_subtitle_filter(ass_path))
    filters.extend(
        _audio_filters(
            len(video_assets),
            audio_mix is not None,
            sample_rate,
            total_seconds,
            bgm_segments=bgm_segments,
            bgm_fade_seconds=bgm_fade_seconds,
            bgm_duck_db=bgm_duck_db,
        )
    )

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-t",
            _fmt_seconds(total_seconds),
            "-r",
            str(fps),
            "-c:v",
            encoder,
            *_encoder_options(encoder, preset, crf, bitrate),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return command


def _encoder_options(encoder: str, preset: str, crf: int, bitrate: str) -> list[str]:
    if encoder == "h264_videotoolbox":
        return ["-b:v", bitrate, "-allow_sw", "1"]
    return ["-preset", preset, "-crf", str(crf)]


def _resolve_encoder(ffmpeg_bin: str, encoder: str) -> str:
    if encoder != "auto":
        return encoder
    try:
        result = subprocess.run([ffmpeg_bin, "-hide_banner", "-encoders"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return "libx264"
    return "h264_videotoolbox" if "h264_videotoolbox" in result.stdout else "libx264"


def _has_subtitles_filter(ffmpeg_bin: str) -> bool:
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-h", "filter=subtitles"], capture_output=True, text=True, check=False
        )
    except (FileNotFoundError, OSError):
        return False
    return "Filter subtitles" in result.stdout


def _resolve_subtitles_bin(ffmpeg_bin: str) -> str:
    """The configured binary if it can burn ASS subtitles, else a libass-capable fallback."""
    for candidate in (ffmpeg_bin, *_LIBASS_FALLBACK_BINS):
        if _has_subtitles_filter(candidate):
            return candidate
    raise RuntimeError(
        "ffmpeg에 subtitles(libass) 필터가 없어 자막을 구울 수 없습니다. "
        "`brew install ffmpeg-full`로 libass 포함 빌드를 설치하거나 --no-subtitles로 렌더하세요."
    )


def _video_filters(
    video_events: list[dict[str, Any]],
    video_assets: list[Path],
    fps: int,
    sample_rate: int,
    with_motion: bool,
    width: int,
    height: int,
) -> list[str]:
    labels = []
    filters = []
    total_frames = 0
    for index, event in enumerate(video_events):
        # Snap each segment boundary onto the GLOBAL frame grid: per-shot second-based
        # trims quantize up to the next frame and the error accumulates across concat
        # (audio adelay / subtitle enable use absolute times, so video drifts vs them).
        start_seconds = int(event["start"]) / sample_rate
        end_seconds = int(event["end"]) / sample_rate
        frames = max(1, round(end_seconds * fps) - round(start_seconds * fps))
        total_frames += frames
        duration = max(1 / fps, end_seconds - start_seconds)
        motion = event.get("motion", {}).get("type", "static") if with_motion else "static"
        zoom, x_expr, y_expr = _zoompan_expr(motion, frames)
        fade = ""
        if motion == "fade" and with_motion:
            fade_seconds = min(FADE_US / 1_000_000, duration)
            fade = f",fade=t=in:st=0:d={_fmt_seconds(fade_seconds)}"
        label = f"v{index}"
        if event.get("source_kind") in {"clip", "stock_clip", "remotion", "hyperframe"}:
            filters.append(
                f"[{index}:v]"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},"
                f"tpad=stop_mode=clone:stop_duration={_fmt_seconds((frames + 1) / fps)},"
                f"trim=end_frame={frames},setpts=PTS-STARTPTS"
                f"{fade},format=yuv420p[{label}]"
            )
        else:
            # Letterbox a still that does not match the frame aspect: zoom/pan must
            # happen INSIDE the fitted image, then pad LAST so the black bars stay a
            # constant width for the whole shot (otherwise the zoom eats the bars).
            fit_w, fit_h = _fit_dims(video_assets[index], width, height)
            filters.append(
                f"[{index}:v]"
                f"scale={fit_w}:{fit_h},setsar=1,"
                f"zoompan=fps={fps}:s={fit_w}x{fit_h}:d={frames}:"
                f"z='{zoom}':x='{x_expr}':y='{y_expr}',"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
                f"trim=end_frame={frames},setpts=PTS-STARTPTS"
                f"{fade},format=yuv420p[{label}]"
            )
        labels.append(f"[{label}]")
    filters.append(
        f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0,"
        f"trim=end_frame={total_frames},"
        "setpts=PTS-STARTPTS[vcat]"
    )
    return filters


def _audio_filter(mix_input_index: int, has_mix: bool, sample_rate: int, total_seconds: float) -> str:
    if not has_mix:
        return (
            f"anullsrc=channel_layout=stereo:sample_rate={sample_rate},"
            f"atrim=0:{_fmt_seconds(total_seconds)},asetpts=PTS-STARTPTS[aout]"
        )
    # The beats are already premixed at their timeline offsets into one wav, so the
    # whole track gets ONE loudnorm pass (per-beat loudnorm was unstable on short
    # inputs and needed one ffmpeg input per beat — fd limit on 200+ beat scripts).
    # loudnorm internally upsamples, so aresample back; apad + atrim then pin the
    # audio to exactly the video length, with a true-peak limiter as a safety net.
    return (
        f"[{mix_input_index}:a]loudnorm=I=-14:TP=-1.5:LRA=11,"
        f"aresample={sample_rate},"
        f"apad,atrim=0:{_fmt_seconds(total_seconds)},"
        f"alimiter=limit=0.95,asetpts=PTS-STARTPTS[aout]"
    )


def _audio_filters(
    mix_input_index: int,
    has_mix: bool,
    sample_rate: int,
    total_seconds: float,
    *,
    bgm_segments: list[dict[str, Any]],
    bgm_fade_seconds: float,
    bgm_duck_db: float,
) -> list[str]:
    """Audio filter chains; without BGM this is exactly the legacy single chain."""
    if not bgm_segments:
        return [_audio_filter(mix_input_index, has_mix, sample_rate, total_seconds)]

    filters: list[str] = []
    labels: list[str] = []
    first_bgm_input = mix_input_index + (1 if has_mix else 0)
    for index, segment in enumerate(bgm_segments):
        duration = segment["end"] - segment["start"]
        fade = max(0.0, min(float(bgm_fade_seconds), duration / 2))
        chain = (
            f"[{first_bgm_input + index}:a]"
            f"aresample={sample_rate},"  # 샘플레이트가 달라도 ffmpeg 가 리샘플
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"atrim=0:{_fmt_seconds(duration)},asetpts=PTS-STARTPTS,"
            f"volume={segment['gain_db']:g}dB"
        )
        if fade > 0:
            # 세그먼트 시작 fade-in / 끝 fade-out — 마지막 세그먼트의 끝은 영상 끝이므로
            # (to 빈값 = total_seconds 로 클램프) 영상 끝에서도 페이드아웃된다.
            chain += (
                f",afade=t=in:st=0:d={_fmt_seconds(fade)}"
                f",afade=t=out:st={_fmt_seconds(max(0.0, duration - fade))}:d={_fmt_seconds(fade)}"
            )
        if segment["start"] > 0:
            chain += f",adelay={round(segment['start'] * 1000)}:all=1"
        label = f"bgm{index}"
        filters.append(f"{chain}[{label}]")
        labels.append(f"[{label}]")
    if len(labels) == 1:
        bgm_all = labels[0]
    else:
        # 막별 세그먼트는 서로 겹치지 않으므로 normalize=0 단순 합산으로 이어붙인다.
        filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0[bgmall]")
        bgm_all = "[bgmall]"

    if has_mix:
        # Chain order matters: loudnorm runs ONCE on the narration premix, and that
        # normalized track feeds BOTH the main mix and the sidechain key — running
        # loudnorm after the duck would re-level the track and undo the ducking.
        filters.append(
            f"[{mix_input_index}:a]loudnorm=I=-14:TP=-1.5:LRA=11,"
            f"aresample={sample_rate},"
            f"apad,atrim=0:{_fmt_seconds(total_seconds)},asetpts=PTS-STARTPTS,"
            f"asplit=2[nar][narsc]"
        )
        threshold = 10 ** (_DUCK_THRESHOLD_DB / 20)
        filters.append(
            f"{bgm_all}[narsc]sidechaincompress="
            f"threshold={threshold:.6f}:ratio={_duck_ratio(bgm_duck_db):g}:"
            f"attack={_DUCK_ATTACK_MS}:release={_DUCK_RELEASE_MS}[bgmduck]"
        )
        narration = "[nar][bgmduck]"
    else:
        # 나레이션이 없으면(--no-audio 등) 덕킹 없이 BGM 만 깐다.
        filters.append(
            f"anullsrc=channel_layout=stereo:sample_rate={sample_rate},"
            f"atrim=0:{_fmt_seconds(total_seconds)},asetpts=PTS-STARTPTS[base]"
        )
        narration = f"[base]{bgm_all}"
    # duration=first: 첫 입력(나레이션/무음 베이스)이 apad+atrim 으로 정확히 영상 길이다.
    # normalize=0 — amix 의 기본 스케일링이 나레이션을 6dB 깎는 것을 막고 단순 합산,
    # 클리핑은 기존과 같은 true-peak limiter 가 잡는다.
    filters.append(
        f"{narration}amix=inputs=2:duration=first:normalize=0,"
        f"alimiter=limit=0.95,asetpts=PTS-STARTPTS[aout]"
    )
    return filters


def _duck_ratio(duck_db: float) -> float:
    """BGM_DUCK_DB(대략 원하는 감쇠량) → sidechaincompress ratio.

    감쇠량 GR ≈ headroom*(1-1/R) 이므로 R = headroom/(headroom-GR).
    headroom(스피치가 threshold 위로 올라오는 양)은 약 20 dB — 위 상수 주석 참고.
    """
    depth = min(abs(float(duck_db)), _DUCK_HEADROOM_DB - 1.0)
    if depth <= 0:
        return 1.0
    return min(round(_DUCK_HEADROOM_DB / (_DUCK_HEADROOM_DB - depth), 3), 20.0)


def _subtitle_filter(ass_path: Path | None) -> str:
    if ass_path is None:
        return "[vcat]null[vout]"
    # ONE libass burn-in instead of one overlay input+filter per subtitle: every
    # frame used to traverse N overlays (O(N^2) work across the video) and each
    # subtitle PNG consumed a file descriptor.
    return f"[vcat]subtitles=filename={_filter_escape(str(ass_path))}[vout]"


def _filter_escape(value: str) -> str:
    """Escape a filter option value for use inside -filter_complex.

    Two parser levels see the string (ffmpeg "Notes on filtergraph escaping"):
    first the per-filter option parser (\\ : '), then the filtergraph parser
    (\\ ' [ ] , ;). Each level strips one layer of backslashes.
    """
    for ch in ("\\", ":", "'"):  # level 1: option value
        value = value.replace(ch, "\\" + ch)
    out = []
    for ch in value:  # level 2: filtergraph
        if ch in "\\'[],;":
            out.append("\\")
        out.append(ch)
    return "".join(out)


def _fit_dims(asset: Path, width: int, height: int) -> tuple[int, int]:
    """Fitted (letterbox) size of a still inside width x height, rounded to even.

    Mirrors ffmpeg's force_original_aspect_ratio=decrease. Falls back to the full
    frame (no bars) if the asset cannot be read or already matches the frame.
    """
    try:
        from PIL import Image

        with Image.open(asset) as im:
            iw, ih = im.size
    except Exception:
        return width, height
    if iw <= 0 or ih <= 0:
        return width, height
    scale = min(width / iw, height / ih)
    fit_w = max(2, (int(round(iw * scale)) // 2) * 2)
    fit_h = max(2, (int(round(ih * scale)) // 2) * 2)
    return min(fit_w, width), min(fit_h, height)


def _zoompan_expr(motion: str, frames: int) -> tuple[str, str, str]:
    denominator = max(frames - 1, 1)
    progress = f"on/{denominator}"
    center_x = "(iw-iw/zoom)/2"
    center_y = "(ih-ih/zoom)/2"
    delta = f"{ZOOM_RATIO - 1:.6f}"
    if motion == "zoom_in":
        return f"1+{delta}*{progress}", center_x, center_y
    if motion == "zoom_out":
        return f"{ZOOM_RATIO:.6f}-{delta}*{progress}", center_x, center_y
    if motion == "pan_lr":
        return f"{ZOOM_RATIO:.6f}", f"(iw-iw/zoom)*{progress}", center_y
    if motion == "pan_rl":
        return f"{ZOOM_RATIO:.6f}", f"(iw-iw/zoom)*(1-{progress})", center_y
    return "1", "0", "0"


def _video_asset(
    project_dir: Path, work_dir: Path, event: dict[str, Any], card_text: dict[str, str], *, dry_run: bool = False
) -> Path:
    src = str(event.get("src") or "")
    if not src:
        raise RuntimeError(f"{event.get('shot_id', '(unknown)')} 에 src가 없습니다. PICKS.json을 확인하세요.")
    src_path = (project_dir / src).resolve()
    if src_path.suffix.lower() == ".svg":
        if dry_run:  # report the would-be path without rendering the PNG
            return work_dir / "cards" / f"{event.get('shot_id') or 'card'}.png"
        return _make_card_png(
            str(event.get("shot_id") or "card"),
            str(event.get("source_kind") or "image"),
            work_dir / "cards",
            card_text.get(str(event.get("shot_id") or "")),
        )
    if not src_path.is_file():
        hint = ""
        if event.get("source_kind") in {"remotion", "hyperframe"}:
            hint = " 먼저 `weft animate`로 SPEC.md를 만들고 이 MP4를 렌더하세요."
        raise RuntimeError(f"영상 에셋 없음: {src_path}.{hint}")
    return src_path


def _audio_events(project_dir: Path, render_plan: dict[str, Any]) -> list[tuple[dict[str, Any], Path]]:
    events = []
    for event in render_plan.get("audio", []):
        src = str(event.get("src") or "")
        if not src:
            continue
        path = (project_dir / src).resolve()
        if not path.is_file():
            raise RuntimeError(f"오디오 에셋 없음: {path}. 먼저 weft tts를 실행하세요.")
        events.append((event, path))
    return events


def _subtitle_events(render_plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [event for event in render_plan.get("subtitles", []) if str(event.get("text") or "").strip()]


def _write_ass(path: Path, events: list[dict[str, Any]], width: int, height: int, sample_rate: int) -> None:
    """One .ass file for the whole timeline, styled to match the old PIL subtitle PNGs.

    Bottom-center white text with a soft black outline; metrics scale with the
    render height exactly like the PIL path did (54px font / 5px stroke / 96px
    bottom margin at 1080p).
    """
    scale = height / CANVAS_H
    font_size = max(28, round(54 * scale))
    outline = max(2, round(5 * scale))
    margin_v = max(8, round(96 * scale))
    margin_x = max(16, round(width * 0.11))  # mirrors the PIL wrap width of 78%
    lines = [
        "[Script Info]",
        "; generated by weft ffmpeg exporter",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        # &H46 outline alpha == the old PIL stroke alpha 185 (ASS alpha is transparency)
        f"Style: Weft,{SUBTITLE_FONT},{font_size},&H00FFFFFF,&H00FFFFFF,&H46000000,&H7F000000,"
        f"0,0,0,0,100,100,0,0,1,{outline},0,2,{margin_x},{margin_x},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for event in sorted(events, key=lambda item: int(item["start"])):
        start = _ass_time(int(event["start"]) / sample_rate)
        end = _ass_time(int(event["end"]) / sample_rate)
        lines.append(f"Dialogue: 0,{start},{end},Weft,,0,0,0,,{_ass_text(str(event['text']).strip())}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ass_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # A zero-width space keeps user backslashes from forming ASS escapes (\N \h \{ …);
    # braces must be escaped or libass would parse {...} as an override block.
    text = text.replace("\\", "\\\u200b")
    text = text.replace("{", "\\{").replace("}", "\\}")
    return text.replace("\n", "\\N")


def _ass_time(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    hours, rem = divmod(centis, 360_000)
    minutes, rem = divmod(rem, 6_000)
    secs, centis = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _premix_audio(
    work_dir: Path,
    audio_events: list[tuple[dict[str, Any], Path]],
    sample_rate: int,
    total_seconds: float,
    *,
    dry_run: bool = False,
) -> Path | None:
    """Sum all beat wavs at their timeline offsets into ONE 16-bit wav.

    K beats used to mean K ffmpeg inputs + K adelay/amix branches (fd limit and a
    huge graph on 200+ beat scripts); the premix keeps ffmpeg at a single audio
    input. The mix is padded with silence to the full video length (the old apad
    intent) and summed with int16 clamping where beats overlap.
    """
    if not audio_events:
        return None
    mix_path = work_dir / AUDIO_MIX_NAME
    if dry_run:  # report the would-be path without reading or writing anything
        return mix_path

    channels = 1
    for _event, path in audio_events:  # cheap header pass: validate + pick channel count
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            if rate != sample_rate:
                raise RuntimeError(
                    f"오디오 샘플레이트 불일치: {path} 는 {rate}Hz, 프로젝트는 {sample_rate}Hz 입니다. "
                    "TTS 출력을 프로젝트 샘플레이트로 다시 생성하세요."
                )
            if handle.getsampwidth() != 2:
                raise RuntimeError(f"오디오 비트심도 미지원: {path} (16-bit PCM wav만 지원합니다)")
            if handle.getnchannels() not in (1, 2):
                raise RuntimeError(f"오디오 채널 미지원: {path} (mono/stereo wav만 지원합니다)")
            channels = max(channels, handle.getnchannels())

    total_frames = max(1, round(total_seconds * sample_rate))
    buffer = bytearray(total_frames * channels * 2)  # silence-padded to video length
    for event, path in audio_events:
        with wave.open(str(path), "rb") as handle:
            source_channels = handle.getnchannels()
            data = handle.readframes(handle.getnframes())
        start_frame = int(event["start"])
        if start_frame >= total_frames or not data:
            continue
        samples = array("h")
        samples.frombytes(data)
        if sys.byteorder == "big":  # wav payload is little-endian
            samples.byteswap()
        if source_channels == 1 and channels == 2:
            stereo = array("h", bytes(4 * len(samples)))
            stereo[0::2] = samples
            stereo[1::2] = samples
            samples = stereo
        available = (total_frames - start_frame) * channels
        if len(samples) > available:
            samples = samples[:available]
        offset = start_frame * channels * 2
        end = offset + len(samples) * 2
        region = buffer[offset:end]
        if any(region):  # overlapping beats: sum with int16 clamping (rare path)
            mixed = array("h")
            mixed.frombytes(bytes(region))
            if sys.byteorder == "big":
                mixed.byteswap()
            for i, value in enumerate(samples):
                total = mixed[i] + value
                mixed[i] = -32768 if total < -32768 else (32767 if total > 32767 else total)
            samples = mixed
        if sys.byteorder == "big":
            samples.byteswap()
        buffer[offset:end] = samples.tobytes()

    mix_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(mix_path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(buffer)
    return mix_path


def load_bgm_config(
    project_dir: str | Path,
    *,
    bgm_file: str | None = None,
    default_gain_db: float = BGM_DEFAULT_GAIN_DB,
    base_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """BGM 설정을 해석한다 — 프로젝트의 BGM.json 이 BGM_FILE 보다 우선.

    Returns [{"path": 절대경로, "from": str|None, "to": str|None, "gain_db": float}, ...]
    (빈 리스트 = BGM 없음). 상대 경로는 base_dir(보통 CONTI.md 폴더) → 프로젝트 폴더 →
    프로젝트 상위 폴더 순으로 찾는다.
    """
    project_dir = Path(project_dir)
    bgm_json = project_dir / BGM_JSON_NAME
    if bgm_json.is_file():
        entries = _read_bgm_json(bgm_json)
    elif bgm_file and bgm_file.strip():
        entries = [{"file": bgm_file.strip(), "from": None, "to": None, "gain_db": None}]
    else:
        return []

    resolved: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, start=1):
        gain = entry.get("gain_db")
        try:
            gain_db = float(gain) if gain not in (None, "") else float(default_gain_db)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"{bgm_json} 의 {index}번째 곡: gain_db 는 숫자(dB)여야 합니다: {gain!r}"
            ) from exc
        resolved.append(
            {
                "path": str(_resolve_bgm_file(str(entry["file"]), project_dir, base_dir)),
                "from": entry.get("from"),
                "to": entry.get("to"),
                "gain_db": gain_db,
            }
        )
    return resolved


def _read_bgm_json(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{path} 를 읽지 못했습니다 (JSON 형식 오류: {exc}). "
            '예시: [{"file": "music/bgm.mp3", "from": "0:00", "to": "", "gain_db": -16}]'
        ) from exc
    if not isinstance(raw, list):
        raise RuntimeError(
            f"{path} 은 곡 목록(JSON 배열)이어야 합니다. "
            '예시: [{"file": "music/bgm.mp3", "from": "0:00", "to": ""}]'
        )
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or not str(item.get("file") or "").strip():
            raise RuntimeError(f'{path} 의 {index}번째 곡에 "file" 항목(음원 경로)이 없습니다.')
        entries.append(
            {
                "file": str(item["file"]).strip(),
                "from": item.get("from"),
                "to": item.get("to"),
                "gain_db": item.get("gain_db"),
            }
        )
    return entries


def _resolve_bgm_file(file_value: str, project_dir: Path, base_dir: str | Path | None) -> Path:
    candidate = Path(file_value).expanduser()
    if candidate.suffix.lower() not in _BGM_EXTS:
        raise RuntimeError(
            f"지원하지 않는 BGM 파일 형식입니다: {file_value} — mp3/wav/m4a 파일을 사용하세요."
        )
    if candidate.is_absolute():
        searched = [candidate]
    else:
        bases: list[Path] = []
        for base in (base_dir, project_dir, Path(project_dir).resolve().parent):
            base = Path(base).resolve() if base else None
            if base and base not in bases:
                bases.append(base)
        searched = [base / candidate for base in bases]
    for path in searched:
        if path.is_file():
            return path.resolve()
    tried = "\n".join(f"  - {path}" for path in searched)
    raise RuntimeError(
        f"BGM 파일을 찾지 못했습니다: {file_value}\n확인한 위치:\n{tried}\n"
        "WEFT_SETTINGS.txt 의 BGM_FILE 또는 BGM.json 의 file 경로를 확인하세요 "
        "(CONTI.md 기준 상대 경로 또는 절대 경로, mp3/wav/m4a)."
    )


def resolve_bgm_segments(bgm: list[dict[str, Any]], total_seconds: float) -> list[dict[str, Any]]:
    """from/to 타임코드("분:초")를 초 단위 구간으로 바꾸고 영상 길이에 맞춰 자른다."""
    segments: list[dict[str, Any]] = []
    total = float(total_seconds)
    for index, entry in enumerate(bgm, start=1):
        start = _bgm_time(entry.get("from"), index, "from", default=0.0)
        end = _bgm_time(entry.get("to"), index, "to", default=total)  # to 빈값 = 영상 끝까지
        start = max(0.0, start)
        end = min(end, total)
        if end - start <= 0.05:
            raise RuntimeError(
                f"BGM {index}번째 곡의 구간이 비어 있습니다 "
                f'(from={entry.get("from")!r}, to={entry.get("to")!r}, 영상 길이 {total:.1f}초). '
                "BGM.json 의 from/to 값을 확인하세요."
            )
        segments.append(
            {"path": entry["path"], "start": start, "end": end, "gain_db": float(entry["gain_db"])}
        )
    return segments


def _bgm_time(value: Any, index: int, field: str, *, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        return float(parse_timecode(str(value)))
    except ValueError as exc:
        raise RuntimeError(
            f'BGM {index}번째 곡의 "{field}" 값이 잘못되었습니다: {value!r} — '
            '"분:초" 형식으로 적어주세요 (예: "1:30").'
        ) from exc


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_seconds(value: float) -> str:
    return f"{value:.3f}"
