from __future__ import annotations

import hashlib
import io
import os
import textwrap
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .comfyui_image import ComfyUIImage
from .gemini_image import GeminiImage
from .gemini_image import DEFAULT_MODEL as GEMINI_DEFAULT_MODEL
from .openai_image import OpenAIImage
from .openai_image import DEFAULT_MODEL as OPENAI_DEFAULT_MODEL
from .typecast_tts import TypecastTTS

# Target aspect ratios Weft can normalize candidates to (IMAGE_ASPECT).
IMAGE_ASPECTS: dict[str, tuple[int, int]] = {
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "3:2": (3, 2),
}


def aspect_ratio(value: str) -> tuple[int, int]:
    """Validate an IMAGE_ASPECT value and return its (w, h) integer ratio."""
    ratio = IMAGE_ASPECTS.get((value or "").strip())
    if ratio is None:
        raise RuntimeError(
            f"IMAGE_ASPECT 값이 잘못되었습니다: {value!r}. "
            f"사용 가능한 값: {', '.join(IMAGE_ASPECTS)}"
        )
    return ratio


class ImageProvider(Protocol):
    def cache_key(self, prompt: str) -> str: ...
    def generate(self, prompt: str, n: int = 2) -> list[bytes]: ...


class TTSProvider(Protocol):
    def cache_key(self, text: str) -> str: ...
    def synthesize(self, text: str) -> bytes: ...


@dataclass
class ProviderBundle:
    provider: ImageProvider | TTSProvider
    metadata: dict[str, str]


def _instantiate(cls: Any, **overrides: Any) -> Any:
    """Build a provider via its ``from_env`` classmethod (env + explicit overrides).

    Test doubles patched over the class name (e.g. ``weft.providers.registry.OpenAIImage``)
    may omit ``from_env``; those are constructed directly with the non-None overrides.
    """
    from_env = getattr(cls, "from_env", None)
    if callable(from_env):
        return from_env(**overrides)
    return cls(**{key: value for key, value in overrides.items() if value is not None})


# ------------------------------------------------------------------ images ---

def _openai_image_bundle(*, model: str | None, size: str | None, quality: str | None, aspect: str | None) -> ProviderBundle:
    provider = _instantiate(OpenAIImage, model=model, size=size, quality=quality, aspect=aspect)
    return ProviderBundle(provider, _image_metadata("openai", provider, model=model, size=size, quality=quality, aspect=aspect))


def _gemini_image_bundle(*, model: str | None, size: str | None, quality: str | None, aspect: str | None) -> ProviderBundle:
    provider = _instantiate(GeminiImage, model=model, size=size, quality=quality, aspect=aspect)
    metadata = _image_metadata("gemini", provider, model=model, size=size, quality=quality, aspect=aspect)
    metadata["size"] = str(getattr(provider, "image_size", size or ""))
    return ProviderBundle(provider, metadata)


def _comfyui_image_bundle(*, model: str | None, size: str | None, quality: str | None, aspect: str | None) -> ProviderBundle:
    # model/size/quality are decided by the workflow JSON, not by env knobs.
    # The target aspect is still enforced downstream by the candidate-save crop.
    provider = _instantiate(ComfyUIImage)
    metadata = {
        "provider": "comfyui",
        "model": Path(getattr(provider, "workflow_path", "") or "").name or "comfyui-workflow",
        "url": str(getattr(provider, "url", "")),
        "size": size or "",
        "quality": quality or "",
        "aspect": aspect or "",
    }
    return ProviderBundle(provider, metadata)


def _stub_image_bundle(*, model: str | None, size: str | None, quality: str | None, aspect: str | None) -> ProviderBundle:
    provider = _instantiate(StubImageProvider, model=model, size=size, quality=quality, aspect=aspect)
    return ProviderBundle(provider, _image_metadata("stub", provider, model=model, size=size, quality=quality, aspect=aspect))


def _image_metadata(
    name: str, provider: Any, *, model: str | None, size: str | None, quality: str | None, aspect: str | None
) -> dict[str, str]:
    return {
        "provider": name,
        "model": str(getattr(provider, "model", model or "")),
        "size": str(getattr(provider, "size", size or "")),
        "quality": str(getattr(provider, "quality", quality or "")),
        "aspect": str(getattr(provider, "aspect", aspect or "")),
    }


_IMAGE_FACTORIES: dict[str, Callable[..., ProviderBundle]] = {
    "openai": _openai_image_bundle,
    "gemini": _gemini_image_bundle,
    "comfyui": _comfyui_image_bundle,
    "stub": _stub_image_bundle,
}

_IMAGE_LABELS: dict[str, Callable[[], str]] = {
    "openai": lambda: os.environ.get("OPENAI_IMAGE_MODEL", "").strip() or OPENAI_DEFAULT_MODEL,
    "gemini": lambda: os.environ.get("GEMINI_IMAGE_MODEL", "").strip() or GEMINI_DEFAULT_MODEL,
    "comfyui": lambda: Path(os.environ.get("COMFYUI_WORKFLOW", "").strip()).name or "comfyui-workflow",
    "stub": lambda: "stub-image",
}

# Selectable model ids per provider, shown in the picker's regeneration panel.
# (comfyui/stub expose their single label instead.)
KNOWN_IMAGE_MODELS: dict[str, list[str]] = {
    "openai": [
        OPENAI_DEFAULT_MODEL,  # gpt-image-2 — 현행 플래그십, 유일한 네이티브 16:9 (예: 1920x1080)
        "gpt-image-1-mini",    # 최저가 — 2026-12-01 종료 예정
        "gpt-image-1.5",       # 2026-12-01 종료 예정
        "gpt-image-1",         # deprecated — 2026-10-23 종료
        "chatgpt-image-latest",
    ],
    "gemini": [
        GEMINI_DEFAULT_MODEL,      # gemini-3.1-flash-image — 품질/비용 균형 기본값
        "gemini-2.5-flash-image",  # 최저가 (1K 전용)
        "gemini-3-pro-image",      # 고품질/4K
    ],
}


def create_image_provider(
    *,
    provider_name: str,
    model: str | None = None,
    size: str | None = None,
    quality: str | None = None,
    aspect: str | None = None,
) -> ProviderBundle:
    factory = _IMAGE_FACTORIES.get(provider_name)
    if factory is None:
        raise RuntimeError(
            f"알 수 없는 IMAGE_PROVIDER={provider_name!r}. 지원: {', '.join(_IMAGE_FACTORIES)}"
        )
    return factory(model=model, size=size, quality=quality, aspect=aspect)


def image_provider_label(provider_name: str) -> str:
    """Model/workflow label for estimates — never constructs a provider, so no API key needed."""
    label = _IMAGE_LABELS.get(provider_name)
    if label is None:
        raise RuntimeError(
            f"알 수 없는 IMAGE_PROVIDER={provider_name!r}. 지원: {', '.join(_IMAGE_FACTORIES)}"
        )
    return label()


def image_provider_options() -> dict[str, Any]:
    """Provider/model choices for UI dropdowns (picker) — no API keys needed.

    Returns the current default provider/model (from env/settings) plus, per
    provider, the selectable model ids with the env default listed first.
    """
    current = os.environ.get("IMAGE_PROVIDER", "openai").strip().lower()
    providers = []
    for name in _IMAGE_FACTORIES:
        default_model = _IMAGE_LABELS[name]()
        models = [default_model] + [m for m in KNOWN_IMAGE_MODELS.get(name, []) if m != default_model]
        providers.append({"name": name, "default_model": default_model, "models": models})
    return {
        "provider": current if current in _IMAGE_FACTORIES else "openai",
        "model": _IMAGE_LABELS[current]() if current in _IMAGE_LABELS else "",
        "aspect": os.environ.get("IMAGE_ASPECT", "").strip() or "16:9",
        "providers": providers,
    }


# --------------------------------------------------------------------- tts ---

def _typecast_tts_bundle(
    *, voice_id: str | None, model: str | None, language: str | None, emotion: str | None
) -> ProviderBundle:
    provider = _instantiate(TypecastTTS, voice_id=voice_id, model=model, language=language, emotion=emotion)
    return ProviderBundle(provider, _tts_metadata("typecast", provider))


def _stub_tts_bundle(
    *, voice_id: str | None, model: str | None, language: str | None, emotion: str | None
) -> ProviderBundle:
    provider = _instantiate(StubTTSProvider, voice_id=voice_id, model=model, language=language, emotion=emotion)
    return ProviderBundle(provider, _tts_metadata("stub", provider))


def _tts_metadata(name: str, provider: Any) -> dict[str, str]:
    return {
        "provider": name,
        "voice_id": str(getattr(provider, "voice_id", "")),
        "model": str(getattr(provider, "model", "")),
        "language": str(getattr(provider, "language", "")),
        "emotion": str(getattr(provider, "emotion", "")),
    }


_TTS_FACTORIES: dict[str, Callable[..., ProviderBundle]] = {
    "typecast": _typecast_tts_bundle,
    "stub": _stub_tts_bundle,
}


def create_tts_provider(
    *,
    provider_name: str,
    voice_id: str | None = None,
    model: str | None = None,
    language: str | None = None,
    emotion: str | None = None,
) -> ProviderBundle:
    factory = _TTS_FACTORIES.get(provider_name)
    if factory is None:
        raise RuntimeError(
            f"알 수 없는 TTS_PROVIDER={provider_name!r}. 지원: {', '.join(_TTS_FACTORIES)}"
        )
    return factory(voice_id=voice_id, model=model, language=language, emotion=emotion)


# Stub render size per aspect — already exactly the target ratio, so the
# candidate-save crop is a no-op for stub images.
_STUB_SIZES = {"16:9": "1280x720", "9:16": "720x1280", "1:1": "1024x1024", "3:2": "1536x1024"}


@dataclass
class StubImageProvider:
    model: str = "stub-image"
    size: str = ""  # empty = derive from aspect
    quality: str = "standard"
    aspect: str = "16:9"

    def __post_init__(self) -> None:
        if not self.size:
            self.size = _STUB_SIZES.get(self.aspect, _STUB_SIZES["16:9"])

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        aspect: str | None = None,
    ) -> "StubImageProvider":
        return cls(
            model=model or "stub-image",
            size=size or os.environ.get("IMAGE_SIZE", "").strip(),
            quality=quality or os.environ.get("IMAGE_QUALITY", "").strip() or "standard",
            aspect=aspect or os.environ.get("IMAGE_ASPECT", "").strip() or "16:9",
        )

    def cache_key(self, prompt: str) -> str:
        return _hash("|".join(["stub-image", self.model, self.size, self.quality, self.aspect, prompt]))

    def generate(self, prompt: str, n: int = 2) -> list[bytes]:
        from PIL import Image, ImageDraw, ImageFont

        width, height = _parse_size(self.size)
        out = []
        for index in range(n):
            img = Image.new("RGB", (width, height), (246, 234, 210))
            draw = ImageDraw.Draw(img)
            font = ImageFont.load_default()
            text = f"stub candidate {index + 1}\n" + "\n".join(textwrap.wrap(prompt[:240], width=48))
            draw.multiline_text((48, 48), text, fill=(47, 42, 34), font=font, spacing=8)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            out.append(buf.getvalue())
        return out


@dataclass
class StubTTSProvider:
    voice_id: str = "stub"
    model: str = "stub-tts"
    language: str = "kor"
    emotion: str = "normal"

    @classmethod
    def from_env(
        cls,
        *,
        voice_id: str | None = None,
        model: str | None = None,
        language: str | None = None,
        emotion: str | None = None,
    ) -> "StubTTSProvider":
        return cls(
            voice_id=voice_id or "stub",
            model=model or "stub-tts",
            language=language or "kor",
            emotion=emotion or "normal",
        )

    def cache_key(self, text: str) -> str:
        return _hash("|".join(["stub-tts", self.model, self.voice_id, self.language, self.emotion, text]))

    def synthesize(self, text: str) -> bytes:
        sample_rate = 48_000
        seconds = max(0.75, min(8.0, len(text.strip()) / 12.0))
        frames = int(sample_rate * seconds)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(b"\x00\x00" * frames)
        return buf.getvalue()


def _parse_size(value: str) -> tuple[int, int]:
    try:
        width, height = value.lower().split("x", 1)
        return int(width), int(height)
    except Exception:
        return 1536, 1024


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
