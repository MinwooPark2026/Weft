"""External asset providers (TTS, image) for Weft.

These are the only modules that make billed network calls. Everything else in
``weft`` stays offline. Each provider exposes a ``cache_key(...)`` so callers
can skip re-billing unchanged inputs.
"""

from __future__ import annotations

from .env import load_env

__all__ = ["load_env"]
