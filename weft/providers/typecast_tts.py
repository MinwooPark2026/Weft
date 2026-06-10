from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

_ENDPOINT = "https://api.typecast.ai/v1/text-to-speech"

# ssfm-v30 preset emotions (Typecast). "normal" = neutral → omitted from the
# payload so the request stays byte-identical to the validated default call.
_EMOTION_PRESETS = {"normal", "happy", "sad", "angry", "whisper", "toneup", "tonedown"}


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
    emotion_intensity: float = 1.0
    audio_format: str = "wav"
    timeout: int = 120
    retries: int = 4

    @classmethod
    def from_env(
        cls,
        *,
        voice_id: str | None = None,
        model: str | None = None,
        language: str | None = None,
        emotion: str | None = None,
    ) -> "TypecastTTS":
        """Build from env vars (``TYPECAST_API_KEY``/``VOICE``/``MODEL`` …).

        Explicit keyword overrides win over the environment so CLI flags keep
        their priority.
        """
        from .env import require

        return cls(
            api_key=require("TYPECAST_API_KEY"),
            voice_id=voice_id or require("TYPECAST_VOICE"),
            model=model or os.environ.get("TYPECAST_MODEL", "").strip() or "ssfm-v30",
            language=language or os.environ.get("TYPECAST_LANGUAGE", "").strip() or "kor",
            emotion=emotion or os.environ.get("TYPECAST_EMOTION", "").strip() or "normal",
        )

    def cache_key(self, text: str) -> str:
        payload = "|".join(
            ["typecast", self.model, self.voice_id, self.language, self.emotion, text]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _payload(self, text: str) -> dict:
        body = {
            "voice_id": self.voice_id,
            "text": text,
            "model": self.model,
            "language": self.language,
            "output": {"audio_format": self.audio_format},
        }
        # Only attach an emotion prompt for a recognized non-neutral preset; an
        # unknown TYPECAST_EMOTION falls back to neutral instead of a 400 mid-run.
        if self.emotion in _EMOTION_PRESETS and self.emotion != "normal":
            body["prompt"] = {
                "emotion_type": "preset",
                "emotion_preset": self.emotion,
                "emotion_intensity": self.emotion_intensity,
            }
        return body

    def synthesize(self, text: str) -> bytes:
        body = json.dumps(self._payload(text)).encode("utf-8")
        request = urllib.request.Request(
            _ENDPOINT,
            data=body,
            headers={"Content-Type": "application/json", "X-API-KEY": self.api_key},
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(self.retries):
            delay = 2.0 * (attempt + 1)  # 2s, 4s, 6s backoff
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:500]
                last_error = RuntimeError(f"Typecast HTTP {exc.code}: {detail}")
                if exc.code < 500 and exc.code != 429:  # client error — retrying won't help
                    raise last_error from exc
                if exc.code == 429:  # honor server-requested wait when present
                    retry_after = _retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None)
                    if retry_after is not None:
                        delay = retry_after
            except urllib.error.URLError as exc:  # network blip
                last_error = RuntimeError(f"Typecast network error: {exc}")
            if attempt < self.retries - 1:
                time.sleep(delay)
        raise last_error if last_error else RuntimeError("Typecast: unknown failure")


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a numeric ``Retry-After`` header, clamped to a sane range."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:  # HTTP-date form — fall back to default backoff
        return None
    return min(120.0, max(0.5, seconds))
