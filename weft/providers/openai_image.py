from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass


@dataclass
class OpenAIImage:
    """OpenAI ``gpt-image-1`` image provider.

    Returns raw PNG bytes per candidate. ``gpt-image-1`` always responds with
    base64 (no ``response_format`` param). 16:9 has no exact size; ``1536x1024``
    (3:2 landscape) is the closest and is framed by Ken Burns motion in the NLE.
    """

    api_key: str
    model: str = "gpt-image-1"
    size: str = "1536x1024"
    quality: str = "medium"

    def __post_init__(self) -> None:
        from openai import OpenAI  # imported lazily so offline code stays import-free

        self._client = OpenAI(api_key=self.api_key)

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
    ) -> "OpenAIImage":
        """Build from env vars (``OPENAI_API_KEY``, ``OPENAI_IMAGE_MODEL`` …).

        Explicit keyword overrides win over the environment so CLI flags keep
        their priority.
        """
        from .env import require

        return cls(
            api_key=require("OPENAI_API_KEY"),
            model=model or os.environ.get("OPENAI_IMAGE_MODEL", "").strip() or "gpt-image-1",
            size=size or os.environ.get("IMAGE_SIZE", "").strip() or "1536x1024",
            quality=quality or os.environ.get("IMAGE_QUALITY", "").strip() or "medium",
        )

    def cache_key(self, prompt: str) -> str:
        payload = "|".join(["openai-image", self.model, self.size, self.quality, prompt])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def generate(self, prompt: str, n: int = 2) -> list[bytes]:
        result = self._client.images.generate(
            model=self.model,
            prompt=prompt,
            size=self.size,
            quality=self.quality,
            n=n,
        )
        return [base64.b64decode(item.b64_json) for item in result.data]
