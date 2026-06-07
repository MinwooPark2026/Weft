from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

_ENDPOINT = "https://api.typecast.ai/v1/text-to-speech"


@dataclass
class TypecastTTS:
    """Typecast text-to-speech provider (REST, stdlib only).

    Validated 2026-06-06: POST ``/v1/text-to-speech`` with header ``X-API-KEY``
    returns raw ``audio/wav`` bytes. ``language="kor"`` + ``model="ssfm-v30"``.
    """

    api_key: str
    voice_id: str
    model: str = "ssfm-v30"
    language: str = "kor"
    emotion: str = "normal"
    audio_format: str = "wav"
    timeout: int = 120
    retries: int = 4

    def cache_key(self, text: str) -> str:
        payload = "|".join(
            ["typecast", self.model, self.voice_id, self.language, self.emotion, text]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def synthesize(self, text: str) -> bytes:
        body = json.dumps(
            {
                "voice_id": self.voice_id,
                "text": text,
                "model": self.model,
                "language": self.language,
                "output": {"audio_format": self.audio_format},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            _ENDPOINT,
            data=body,
            headers={"Content-Type": "application/json", "X-API-KEY": self.api_key},
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:500]
                last_error = RuntimeError(f"Typecast HTTP {exc.code}: {detail}")
                if exc.code < 500 and exc.code != 429:  # client error — retrying won't help
                    raise last_error from exc
            except urllib.error.URLError as exc:  # network blip
                last_error = RuntimeError(f"Typecast network error: {exc}")
            if attempt < self.retries - 1:
                time.sleep(2.0 * (attempt + 1))  # 2s, 4s, 6s backoff
        raise last_error if last_error else RuntimeError("Typecast: unknown failure")
