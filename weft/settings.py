from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

SETTINGS_FILE = "WEFT_SETTINGS.txt"

DEFAULT_SETTINGS_TEXT = """# WEFT_SETTINGS.txt
# Project-local Weft options. Copy this file with CONTI.md to reuse the same run settings.
# CLI flags override this file for one-off runs. API keys still belong in .env.
# 우선순위(높은 쪽이 이김): 셸 환경변수(export) > 이 파일(WEFT_SETTINGS.txt) > .env

# Project
PROJECT_OUT=generated_project

# Images
# IMAGE_PROVIDER: openai | gemini | comfyui | stub (stub = 키 없이 로컬 placeholder)
IMAGE_PROVIDER=openai
IMAGE_CANDIDATES_N=2
IMAGE_QUALITY=medium
# 이미지 비율: 16:9 | 9:16 | 1:1 | 3:2 — 어떤 provider 든 저장 시 이 비율로 정확히 맞춘다(센터 크롭)
IMAGE_ASPECT=16:9
# IMAGE_SIZE: 보통 비워 둔다(IMAGE_ASPECT 로 자동 결정). openai 요청 크기 강제용.
#IMAGE_SIZE=
# OpenAI 모델. 기본 gpt-image-2(현행 플래그십, 네이티브 16:9 — 예: 1920x1080).
# 더 싸게: gpt-image-1-mini — 단 ⚠ 종료 일정 주의: gpt-image-1 은 2026-10-23,
# gpt-image-1-mini/1.5 는 2026-12-01 종료(이후 gpt-image-2 만 남음).
#OPENAI_IMAGE_MODEL=gpt-image-2
# Gemini 모델 (IMAGE_PROVIDER=gemini 일 때). 기본 gemini-3.1-flash-image.
# 저가형: gemini-2.5-flash-image / 고품질·4K: gemini-3-pro-image. 키는 .env 의 GEMINI_API_KEY.
#GEMINI_IMAGE_MODEL=gemini-3.1-flash-image
#GEMINI_IMAGE_SIZE=1K
# 반복 캐릭터 시트: 프롬프트에 @char 를 쓰면 CHARACTER.png(CONTI.md 옆 또는 generated_project)를
# 레퍼런스로 보낸다. 다른 경로를 쓰려면 지정:
#CHARACTER_SHEET=
# ComfyUI 로컬 생성 (IMAGE_PROVIDER=comfyui 일 때만) — 주석을 풀고 값을 채운다.
# COMFYUI_WORKFLOW 는 ComfyUI "Save (API Format)" 으로 내보낸 JSON 경로이고,
# 긍정 프롬프트 자리에 __WEFT_PROMPT__ (선택: 시드 자리에 __WEFT_SEED__) 를 넣는다.
# 해상도는 워크플로 JSON 이 결정한다 — IMAGE_ASPECT 와 같은 비율로 맞춰 두는 것을 권장
# (다르면 저장 시 센터 크롭으로 잘려 나간다).
#COMFYUI_URL=http://127.0.0.1:8188
#COMFYUI_WORKFLOW=
#COMFYUI_TIMEOUT=300

# TTS
TTS_PROVIDER=typecast
TYPECAST_MODEL=ssfm-v30
TYPECAST_LANGUAGE=kor
TYPECAST_EMOTION=normal

# CapCut draft
EXPORT_CAPCUT=false
CAPCUT_FOLDER=
CAPCUT_NO_REGISTER=false
CAPCUT_NO_MOTION=false
CAPCUT_NO_AUDIO=false

# FCPXML handoff
EXPORT_FCPXML=false
FCPXML_OUTPUT=

# MP4 render
EXPORT_FFMPEG=true
# ffmpeg 실행 파일 경로/이름 (비우면 PATH 의 ffmpeg 사용)
FFMPEG_BIN=ffmpeg
FFMPEG_OUTPUT=
FFMPEG_ENCODER=auto
FFMPEG_WIDTH=1920
FFMPEG_HEIGHT=1080
FFMPEG_PRESET=veryfast
FFMPEG_CRF=20
FFMPEG_BITRATE=8M
FFMPEG_NO_MOTION=false
FFMPEG_NO_AUDIO=false
FFMPEG_NO_SUBTITLES=false

# BGM (배경음악) — 비워 두면 BGM 없음(기본). 캡컷 없이 weft ffmpeg 가 BGM 을 깔고
# 나레이션이 나오는 동안 자동으로 음량을 낮춥니다(사이드체인 덕킹).
# 음원은 유튜브 오디오 라이브러리처럼 라이선스가 확보된 파일(mp3/wav/m4a)을 직접 준비하세요.
# 경로는 CONTI.md 기준 상대 경로 또는 절대 경로. 곡이 영상보다 짧으면 자동 반복.
#BGM_FILE=
# BGM 기본 음량(dB). 나레이션 대비 배경 수준 권장값 -16
#BGM_GAIN_DB=-16
# 나레이션 중 BGM 을 추가로 낮추는 깊이(대략 dB)
#BGM_DUCK_DB=-12
# 곡(구간) 시작/끝 페이드 길이(초) — 마지막 곡은 영상 끝에서도 페이드아웃
#BGM_FADE_SECONDS=2.0
# 막(구간)별로 다른 곡: CONTI.md 옆에 BGM.json 을 만들면 BGM_FILE 보다 우선합니다.
# [{"file": "music/op.mp3", "from": "0:00", "to": "1:30", "gain_db": -16},
#  {"file": "music/ed.mp3",  "from": "1:30", "to": ""}]   ← to 빈값 = 영상 끝까지
# 한 번만 끄려면: weft ffmpeg --no-bgm (또는 weft all --no-bgm)
"""

TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}


def load_project_settings(scope: str | Path | None = None) -> dict[str, str]:
    path = find_settings_file(scope)
    return parse_settings(path) if path else {}


def apply_project_settings(scope: str | Path | None = None) -> dict[str, str]:
    """Push WEFT_SETTINGS.txt values into ``os.environ``.

    우선순위(높은 쪽이 이김): 셸 환경변수(export) > WEFT_SETTINGS.txt > .env.
    이미 환경에 있는 키는 설정 파일(.env 등)이 넣은 것일 때만 덮어쓰고,
    사용자가 셸에서 직접 export 한 값은 절대 건드리지 않는다.
    """
    from .providers.env import is_file_provided, mark_file_provided

    settings = load_project_settings(scope)
    for key, value in settings.items():
        if key in os.environ and not is_file_provided(key):
            continue  # 셸 export 가 우선
        os.environ[key] = value
        mark_file_provided(key)
    return settings


def ensure_settings_file(scope: str | Path | None = None) -> Path:
    existing = find_settings_file(scope)
    if existing:
        return existing
    root = project_root(scope)
    root.mkdir(parents=True, exist_ok=True)  # 없는 경로를 줘도 트레이스백 대신 폴더를 만들어 준다
    path = root / SETTINGS_FILE
    path.write_text(DEFAULT_SETTINGS_TEXT, encoding="utf-8")
    return path


def find_settings_file(scope: str | Path | None = None) -> Path | None:
    override = os.environ.get("WEFT_SETTINGS", "").strip()
    if override:
        path = Path(override)
        if not path.is_file():
            raise RuntimeError(
                f"환경변수 WEFT_SETTINGS={override} 가 가리키는 설정 파일이 없습니다. "
                f"경로를 고치거나 변수를 해제(unset WEFT_SETTINGS)하세요."
            )
        return path
    for candidate in settings_candidates(scope):
        if candidate.is_file():
            return candidate
    return None


def settings_candidates(scope: str | Path | None = None) -> list[Path]:
    root = project_root(scope)
    candidates = [root / SETTINGS_FILE]
    if root.name == "generated_project":
        candidates.append(root.parent / SETTINGS_FILE)
    return candidates


def project_root(scope: str | Path | None = None) -> Path:
    if scope is None:
        return Path.cwd()
    path = Path(scope)
    if path.is_file():
        return path.parent
    return path


def parse_settings(path: str | Path) -> dict[str, str]:
    out: dict[str, str] = {}
    path = Path(path)
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            print(
                f"{path}:{line_no}: '=' 가 없어 무시한 줄입니다: {line!r} (KEY=VALUE 형식으로 적어주세요)",
                file=sys.stderr,
            )
            continue
        key, value = line.split("=", 1)
        key = key.strip().upper()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        out[key] = value
    return out


def setting_str(settings: dict[str, str], key: str, default: str | None = None) -> str | None:
    value = settings.get(key.upper())
    return value if value not in {None, ""} else default


def setting_int(settings: dict[str, str], key: str, default: int | None = None) -> int | None:
    value = setting_str(settings, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{SETTINGS_FILE}: {key} 는 정수여야 합니다: {value!r}") from exc


def setting_float(settings: dict[str, str], key: str, default: float | None = None) -> float | None:
    value = setting_str(settings, key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"{SETTINGS_FILE}: {key} 는 숫자여야 합니다: {value!r}") from exc


def setting_bool(settings: dict[str, str], key: str, default: bool = False) -> bool:
    value = settings.get(key.upper())
    if value is None or not value.strip():
        return default  # 빈 값은 다른 setting_* 와 동일하게 default 로 처리
    lowered = value.strip().lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    raise RuntimeError(f"{SETTINGS_FILE}: {key} 는 true/false 값이어야 합니다: {value!r}")


def settings_payload(settings: dict[str, str], path: Path | None = None) -> dict[str, Any]:
    return {"path": str(path) if path else None, "settings": settings}
