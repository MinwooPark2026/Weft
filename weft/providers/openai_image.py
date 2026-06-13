from __future__ import annotations

import base64
import hashlib
import io
import os
from dataclasses import dataclass
from typing import ClassVar

# Default = cheapest current model. OpenAI deprecation schedule (2026-06 공지):
# gpt-image-1 은 2026-10-23, gpt-image-1-mini / gpt-image-1.5 / chatgpt-image-latest 는
# 2026-12-01 서비스 종료 — 이후에는 gpt-image-2 로 전환해야 한다 (네이티브 16:9 지원).
DEFAULT_MODEL = "gpt-image-1-mini"

# Fixed sizes for the gpt-image-1 family (auto | 1024x1024 | 1536x1024 | 1024x1536):
# no native 16:9, so we request the closest landscape/portrait size and the
# assets layer center-crops the result to the exact target aspect.
_LEGACY_SIZES = {
    "16:9": "1536x1024",
    "9:16": "1024x1536",
    "1:1": "1024x1024",
    "3:2": "1536x1024",
}
# gpt-image-2 accepts arbitrary WIDTHxHEIGHT (16의 배수, 1:3~3:1, 최대 3840x2160),
# so 16:9 can be requested natively.
_GPT_IMAGE_2_SIZES = {
    "16:9": "1920x1080",
    "9:16": "1080x1920",
    "1:1": "1024x1024",
    "3:2": "1536x1024",
}


def size_for(model: str, aspect: str) -> str:
    table = _GPT_IMAGE_2_SIZES if model.startswith("gpt-image-2") else _LEGACY_SIZES
    return table.get(aspect, table["16:9"])


@dataclass
class OpenAIImage:
    """OpenAI ``gpt-image-*`` image provider.

    Returns raw PNG bytes per candidate (the gpt-image family always responds
    with base64, never URLs). When ``size`` is empty it is derived from
    ``aspect`` per model — gpt-image-2 natively supports 16:9 (1920x1080),
    older models get the closest fixed size and are center-cropped downstream.
    With ``references`` (e.g. a character sheet) generation goes through
    ``images.edit`` so the model sees the reference image.
    """

    api_key: str
    model: str = DEFAULT_MODEL
    size: str = ""  # empty = derive from aspect
    quality: str = "medium"
    aspect: str = "16:9"

    # The assets layer checks this before passing a character reference sheet.
    supports_reference_images: ClassVar[bool] = True

    def __post_init__(self) -> None:
        if not self.size:
            self.size = size_for(self.model, self.aspect)
        self._client = None  # built lazily so tests can construct without the SDK

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        aspect: str | None = None,
    ) -> "OpenAIImage":
        """Build from env vars (``OPENAI_API_KEY``, ``OPENAI_IMAGE_MODEL`` …).

        Explicit keyword overrides win over the environment so CLI flags keep
        their priority.
        """
        from .env import require

        return cls(
            api_key=require("OPENAI_API_KEY"),
            model=model or os.environ.get("OPENAI_IMAGE_MODEL", "").strip() or DEFAULT_MODEL,
            size=size or os.environ.get("IMAGE_SIZE", "").strip(),
            quality=quality or os.environ.get("IMAGE_QUALITY", "").strip() or "medium",
            aspect=aspect or os.environ.get("IMAGE_ASPECT", "").strip() or "16:9",
        )

    def _ensure_client(self):
        if self._client is None:
            from openai import OpenAI  # imported lazily so offline code stays import-free

            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def cache_key(self, prompt: str) -> str:
        payload = "|".join(["openai-image", self.model, self.size, self.quality, self.aspect, prompt])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def generate(self, prompt: str, n: int = 2, references: list[bytes] | None = None) -> list[bytes]:
        client = self._ensure_client()
        if references:
            # Reference-image generation = images.edit (supported by the whole
            # gpt-image family, incl. gpt-image-1-mini).
            files = [_named_png(blob, f"reference_{i + 1}.png") for i, blob in enumerate(references)]
            result = client.images.edit(
                model=self.model,
                image=files if len(files) > 1 else files[0],
                prompt=prompt,
                size=self.size,
                quality=self.quality,
                n=n,
            )
        else:
            result = client.images.generate(
                model=self.model,
                prompt=prompt,
                size=self.size,
                quality=self.quality,
                n=n,
            )
        return [base64.b64decode(item.b64_json) for item in result.data]


def _named_png(blob: bytes, name: str) -> io.BytesIO:
    handle = io.BytesIO(blob)
    handle.name = name  # the SDK uses .name for the multipart filename/mime
    return handle
