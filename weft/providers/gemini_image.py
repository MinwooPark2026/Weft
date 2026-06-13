"""Gemini image provider (stdlib ``urllib`` only — no new dependencies).

Calls ``POST /v1/models/<model>:generateContent`` on the Generative Language
API with an ``x-goog-api-key`` header. Aspect ratio is requested natively via
``generationConfig.responseFormat.image.aspectRatio`` (the documented June-2026
shape); if the server rejects that shape with HTTP 400, the request is retried
once with the older ``generationConfig.imageConfig`` wire shape that the
official SDKs still emit.

Reference images (e.g. a recurring character sheet) are passed as additional
``inline_data`` parts next to the text prompt, which is Gemini's native way of
doing character-consistent generation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.request import Request, urlopen

API_ROOT = "https://generativelanguage.googleapis.com/v1"
DEFAULT_MODEL = "gemini-3.1-flash-image"
DEFAULT_IMAGE_SIZE = "1K"  # 512 | 1K | 2K | 4K (uppercase K)
DEFAULT_TIMEOUT = 180.0

# gemini-2.5-flash-image only outputs ~1K and rejects an imageSize knob.
_NO_IMAGE_SIZE_MODELS = ("gemini-2.5-flash-image",)


@dataclass
class GeminiImage:
    """Google Gemini image provider (Nano Banana family) with native 16:9."""

    api_key: str
    model: str = DEFAULT_MODEL
    aspect: str = "16:9"
    image_size: str = DEFAULT_IMAGE_SIZE
    timeout: float = DEFAULT_TIMEOUT

    # The assets layer checks this before passing a character reference sheet.
    supports_reference_images: ClassVar[bool] = True

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        size: str | None = None,  # accepted for registry symmetry; Gemini sizes via GEMINI_IMAGE_SIZE
        quality: str | None = None,  # accepted and ignored — Gemini has no quality knob
        aspect: str | None = None,
    ) -> "GeminiImage":
        # SDK convention: GOOGLE_API_KEY wins when both are set.
        api_key = (
            os.environ.get("GOOGLE_API_KEY", "").strip()
            or os.environ.get("GEMINI_API_KEY", "").strip()
        )
        if not api_key:
            raise RuntimeError(
                "환경변수 GEMINI_API_KEY 가 비어 있습니다. "
                "Google AI Studio(https://aistudio.google.com/apikey)에서 API 키를 만들어 "
                ".env 의 GEMINI_API_KEY 에 채워 주세요. (GOOGLE_API_KEY 로 지정해도 됩니다)"
            )
        return cls(
            api_key=api_key,
            model=model or os.environ.get("GEMINI_IMAGE_MODEL", "").strip() or DEFAULT_MODEL,
            aspect=aspect or os.environ.get("IMAGE_ASPECT", "").strip() or "16:9",
            image_size=os.environ.get("GEMINI_IMAGE_SIZE", "").strip() or DEFAULT_IMAGE_SIZE,
        )

    # ------------------------------------------------------------ provider ---

    def cache_key(self, prompt: str) -> str:
        payload = "|".join(["gemini-image", self.model, self.aspect, self.image_size, prompt])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def generate(self, prompt: str, n: int = 2, references: list[bytes] | None = None) -> list[bytes]:
        out: list[bytes] = []
        for _ in range(n):  # generateContent returns one image set per call
            out.extend(self._generate_once(prompt, references or []))
            if len(out) >= n:
                break
        return out[:n]

    # ---------------------------------------------------------------- HTTP ---

    def _image_config(self) -> dict[str, str]:
        config = {"aspectRatio": self.aspect}
        if not any(self.model.startswith(m) for m in _NO_IMAGE_SIZE_MODELS):
            config["imageSize"] = self.image_size
        return config

    def _body(self, prompt: str, references: list[bytes], *, legacy_shape: bool) -> bytes:
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for blob in references:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(blob).decode("ascii"),
                    }
                }
            )
        generation_config: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"]}
        if legacy_shape:
            generation_config["imageConfig"] = self._image_config()
        else:
            generation_config["responseFormat"] = {"image": self._image_config()}
        body = {"contents": [{"parts": parts}], "generationConfig": generation_config}
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    def _generate_once(self, prompt: str, references: list[bytes]) -> list[bytes]:
        try:
            raw = self._post(self._body(prompt, references, legacy_shape=False))
        except _BadRequest:
            # Server rejected the documented responseFormat shape — retry once
            # with the older imageConfig wire shape (official SDKs still use it).
            try:
                raw = self._post(self._body(prompt, references, legacy_shape=True))
            except _BadRequest as exc:
                raise RuntimeError(exc.message) from exc
        return self._extract_images(raw)

    def _post(self, body: bytes) -> bytes:
        request = Request(
            f"{API_ROOT}/models/{self.model}:generateContent",
            data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:  # HTTPError first — it subclasses URLError
            detail = exc.read().decode("utf-8", "replace")[:500]
            message = (
                f"Gemini 이미지 생성 실패 (HTTP {exc.code}, model={self.model}): {detail}\n"
                "API 키(GEMINI_API_KEY)와 모델 이름(GEMINI_IMAGE_MODEL)을 확인하세요. "
                "Gemini 이미지 모델은 무료 등급이 없어 결제가 활성화된 키가 필요합니다."
            )
            if exc.code == 400:
                raise _BadRequest(message) from exc
            raise RuntimeError(message) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                "Gemini 서버(generativelanguage.googleapis.com)에 연결할 수 없습니다. "
                "네트워크 연결을 확인한 뒤 다시 시도하세요."
            ) from exc

    def _extract_images(self, raw: bytes) -> list[bytes]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Gemini 응답 JSON 파싱 실패: {raw[:200]!r}") from exc
        blobs: list[bytes] = []
        for candidate in payload.get("candidates", []):
            for part in (candidate.get("content") or {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    blobs.append(base64.b64decode(inline["data"]))
        if not blobs:
            raise RuntimeError(
                "Gemini 응답에 이미지가 없습니다 (안전 필터로 차단되었거나 모델이 텍스트만 반환). "
                f"프롬프트를 바꿔 다시 시도하세요: {raw[:300]!r}"
            )
        return blobs


class _BadRequest(RuntimeError):
    """HTTP 400 — request-shape fallback is allowed exactly once."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
