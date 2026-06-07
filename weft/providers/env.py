from __future__ import annotations

import os
from pathlib import Path

# weft/ root = parent of the package directory (weft/providers/env.py -> weft)
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def load_env(path: str | Path | None = None) -> dict[str, str]:
    """Parse a ``.env`` file into a dict and into ``os.environ`` (without overwrite).

    Search order when ``path`` is omitted: ``$WEFT_ENV`` -> ``./.env`` ->
    ``<weft root>/.env``. Lines are ``KEY=VALUE``; ``#`` comments and blanks
    are ignored. Surrounding quotes on the value are stripped. Existing
    environment variables win (so a real shell export overrides the file).
    """
    candidates: list[Path] = []
    if path is not None:
        candidates.append(Path(path))
    else:
        env_override = os.environ.get("WEFT_ENV")
        if env_override:
            candidates.append(Path(env_override))
        candidates.append(Path.cwd() / ".env")
        candidates.append(_PACKAGE_ROOT / ".env")

    values: dict[str, str] = {}
    for candidate in candidates:
        if candidate.is_file():
            values = _parse(candidate)
            break

    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values


def _parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        out[key] = value
    return out


def require(name: str) -> str:
    """Return an env var or raise a clear, actionable error."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"환경변수 {name} 가 비어 있습니다. .env 에 값을 채워 주세요."
        )
    return value
