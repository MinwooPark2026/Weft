"""ComfyUI local image provider (stdlib ``urllib`` only — no new dependencies).

Flow per candidate: POST ``/prompt`` with the materialized workflow graph →
poll ``/history/<prompt_id>`` until outputs appear → download each image via
``/view?filename=…&subfolder=…&type=…`` and return raw PNG bytes.

The workflow template is a JSON file exported from ComfyUI with
"Save (API Format)". Inside it:

- ``__WEFT_PROMPT__``  (required) marks where the shot prompt goes. It is
  substituted JSON-escape-safely (``json.dumps`` of the prompt), so quotes and
  newlines in prompts never break the graph.
- ``__WEFT_SEED__``    (optional) is replaced with a fresh random seed per
  candidate. Without it, every ``"seed"``/``"noise_seed"`` integer in the graph
  is randomized per candidate instead, so the N candidates are real variations.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

PROMPT_PLACEHOLDER = "__WEFT_PROMPT__"
SEED_PLACEHOLDER = "__WEFT_SEED__"
DEFAULT_URL = "http://127.0.0.1:8188"
DEFAULT_TIMEOUT = 300.0

# Graph keys treated as sampler seeds when no __WEFT_SEED__ placeholder exists.
_SEED_KEYS = {"seed", "noise_seed"}


@dataclass
class ComfyUIImage:
    """Local ComfyUI image provider driven by a "Save (API Format)" workflow."""

    url: str = DEFAULT_URL
    workflow_path: str = ""
    timeout: float = DEFAULT_TIMEOUT
    poll_interval: float = 0.75

    def __post_init__(self) -> None:
        self.url = (self.url or DEFAULT_URL).rstrip("/")
        self._template = self._load_template()

    @classmethod
    def from_env(cls, **_overrides: Any) -> "ComfyUIImage":
        """Build from ``COMFYUI_URL`` / ``COMFYUI_WORKFLOW`` / ``COMFYUI_TIMEOUT``.

        ``model``/``size``/``quality`` overrides are accepted and ignored — for
        ComfyUI the workflow JSON decides the model, resolution, and sampler.
        """
        raw_timeout = os.environ.get("COMFYUI_TIMEOUT", "").strip()
        try:
            timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT
        except ValueError as exc:
            raise RuntimeError(f"COMFYUI_TIMEOUT 은 숫자(초)여야 합니다: {raw_timeout!r}") from exc
        return cls(
            url=os.environ.get("COMFYUI_URL", "").strip() or DEFAULT_URL,
            workflow_path=os.environ.get("COMFYUI_WORKFLOW", "").strip(),
            timeout=timeout,
        )

    # ----------------------------------------------------------- template ---

    def _load_template(self) -> str:
        if not self.workflow_path:
            raise RuntimeError(
                "환경변수 COMFYUI_WORKFLOW 가 비어 있습니다. ComfyUI에서 'Save (API Format)'으로 "
                "내보낸 워크플로 JSON 경로를 .env 또는 WEFT_SETTINGS.txt 에 지정하세요."
            )
        path = Path(self.workflow_path).expanduser()
        if not path.is_file():
            raise RuntimeError(
                f"COMFYUI_WORKFLOW 파일이 없습니다: {path}\n"
                "ComfyUI에서 'Save (API Format)'으로 내보낸 워크플로 JSON 경로인지 확인하세요."
            )
        text = path.read_text(encoding="utf-8")
        if PROMPT_PLACEHOLDER not in text:
            raise RuntimeError(
                f"워크플로 JSON({path})에 {PROMPT_PLACEHOLDER} 플레이스홀더가 없습니다. "
                "ComfyUI 'Save (API Format)' JSON에서 긍정 프롬프트(CLIPTextEncode 등)의 "
                f"text 값을 {PROMPT_PLACEHOLDER} 로 바꿔 주세요."
            )
        return text

    def _materialize(self, prompt: str, seed: int) -> dict[str, Any]:
        """Substitute placeholders and return the per-candidate graph dict."""
        escaped = json.dumps(prompt, ensure_ascii=False)[1:-1]  # JSON-escaped, no quotes
        text = self._template.replace(PROMPT_PLACEHOLDER, escaped)
        has_seed_placeholder = SEED_PLACEHOLDER in self._template
        # Quoted form first ("__WEFT_SEED__" -> 12345 keeps the value numeric),
        # then any bare occurrence the user left unquoted.
        text = text.replace(f'"{SEED_PLACEHOLDER}"', str(seed))
        text = text.replace(SEED_PLACEHOLDER, str(seed))
        try:
            graph = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"COMFYUI_WORKFLOW JSON 파싱 실패 ({self.workflow_path}): {exc}\n"
                "ComfyUI 'Save (API Format)'으로 내보낸 JSON인지 확인하세요."
            ) from exc
        if not has_seed_placeholder:
            _randomize_seeds(graph, seed)
        return graph

    # ------------------------------------------------------------ provider ---

    def cache_key(self, prompt: str) -> str:
        workflow_hash = hashlib.sha256(self._template.encode("utf-8")).hexdigest()
        payload = "|".join(["comfyui-image", workflow_hash, prompt])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def generate(self, prompt: str, n: int = 2) -> list[bytes]:
        out: list[bytes] = []
        for _ in range(n):  # one queued run per candidate, each with its own seed
            seed = random.randrange(1, 2**31 - 1)
            graph = self._materialize(prompt, seed)
            prompt_id = self._queue(graph)
            outputs = self._wait(prompt_id)
            images = self._download(outputs)
            if not images:
                raise RuntimeError(
                    "ComfyUI 실행은 끝났지만 이미지 출력이 없습니다 — "
                    "워크플로에 SaveImage 노드가 있는지 확인하세요."
                )
            out.extend(images)
        return out

    # ---------------------------------------------------------------- HTTP ---

    def _queue(self, graph: dict[str, Any]) -> str:
        body = json.dumps({"prompt": graph}).encode("utf-8")
        data = self._request_json("/prompt", body=body)
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI /prompt 응답에 prompt_id 가 없습니다: {str(data)[:300]}")
        return str(prompt_id)

    def _wait(self, prompt_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while True:
            history = self._request_json(f"/history/{prompt_id}")
            entry = history.get(prompt_id) or {}
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                raise RuntimeError(
                    f"ComfyUI 워크플로 실행 실패 (prompt_id={prompt_id}): "
                    f"{json.dumps(status, ensure_ascii=False)[:400]}"
                )
            outputs = entry.get("outputs") or {}
            if outputs:
                return outputs
            if status.get("completed"):
                return {}  # finished but produced nothing — caller reports it
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"ComfyUI 생성이 {self.timeout:.0f}초 안에 끝나지 않았습니다 "
                    "(COMFYUI_TIMEOUT 으로 대기 시간을 늘릴 수 있습니다)."
                )
            time.sleep(self.poll_interval)

    def _download(self, outputs: dict[str, Any]) -> list[bytes]:
        blobs: list[bytes] = []
        for node_output in outputs.values():
            for image in node_output.get("images", []):
                query = urllib.parse.urlencode(
                    {
                        "filename": image.get("filename", ""),
                        "subfolder": image.get("subfolder", ""),
                        "type": image.get("type", "output"),
                    }
                )
                blobs.append(self._request_raw(f"/view?{query}"))
        return blobs

    def _request_raw(self, path: str, body: bytes | None = None) -> bytes:
        headers = {"Content-Type": "application/json"} if body is not None else {}
        request = Request(self.url + path, data=body, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:  # HTTPError first — it subclasses URLError
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"ComfyUI HTTP {exc.code} ({path}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"ComfyUI 서버({self.url})에 연결할 수 없습니다. ComfyUI를 실행했는지 확인하세요."
            ) from exc

    def _request_json(self, path: str, body: bytes | None = None) -> dict[str, Any]:
        raw = self._request_raw(path, body=body)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"ComfyUI 응답 JSON 파싱 실패 ({path}): {raw[:200]!r}") from exc


def _randomize_seeds(node: Any, seed: int) -> None:
    """Recursively set every integer ``seed``/``noise_seed`` in the graph."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _SEED_KEYS and isinstance(value, int) and not isinstance(value, bool):
                node[key] = seed
            else:
                _randomize_seeds(value, seed)
    elif isinstance(node, list):
        for item in node:
            _randomize_seeds(item, seed)
