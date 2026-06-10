from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from .markdown import first_heading, parse_markdown_tables
from .timecode import parse_time_range


TOKEN_NEW = "\u25b6"
TOKEN_HOLD = "\u2193"
TOKEN_MONTAGE = "\u25a6"
TOKEN_REUSE = "\u21ba"
TOKEN_PAUSE = "\u23f8"
TOKEN_TEXT_CARD = "\u275d"
TOKEN_BUILD = "\u2934"
TOKEN_TITLE = "\U0001f3f7"
TOKEN_END = "\U0001f3c1"


def parse_conti(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    tables = parse_markdown_tables(text)
    conti_rows = _find_table(tables, "beat")
    shot_rows = _find_table(tables, "shot id")
    shot_meta = _parse_shot_meta(shot_rows)

    beats = [_parse_beat(row) for row in conti_rows]
    beat_index: dict[str, int] = {}
    for index, beat in enumerate(beats):
        if beat["id"] in beat_index:
            raise ValueError(
                f"beat id {beat['id']!r}가 콘티 표에 두 번 이상 나옵니다 — beat 열의 id가 겹치지 않게 바꿔 주세요"
            )
        beat_index[beat["id"]] = index
    shots = _derive_shots(conti_rows, shot_meta, beat_index)

    return {
        "project": {
            "schema": "weft-project-v1",
            "title": first_heading(text),
            "source_conti": str(path),
            "fps": 30,
            "sample_rate": 48_000,
            "dryrun": True,
        },
        "style_bible": _extract_style_bible(text),
        "narration": {"schema": "weft-narration-v1", "beats": beats},
        "visuals": {"schema": "weft-visual-v1", "style_bible": _extract_style_bible(text), "shots": shots},
    }


def _find_table(tables: list[dict[str, object]], first_column: str) -> list[dict[str, str]]:
    for table in tables:
        header = table["header"]
        if isinstance(header, list) and header and str(header[0]).strip().lower() == first_column:
            mismatches = table.get("mismatches") or []
            if mismatches:
                details = " / ".join(
                    f"{item['line']}행은 {item['got']}칸: {item['content']}"
                    for item in mismatches[:5]  # type: ignore[index]
                )
                raise ValueError(
                    f"CONTI.md의 {first_column!r} 표에 칸 수가 헤더({len(header)}칸)와 다른 행이 있습니다 — "
                    f"{details} — 해당 행의 | 구분 개수를 헤더와 맞춰 주세요 (셀 안의 |는 \\|로 적습니다)"
                )
            return [_normalize_row_keys(row) for row in table["rows"]]  # type: ignore[union-attr]
    raise ValueError(f"could not find markdown table with first column {first_column!r}")


def _normalize_row_keys(row: dict[str, str]) -> dict[str, str]:
    return {str(key).strip().lower(): value for key, value in row.items()}


_STYLE_BIBLE_RE = re.compile(r"^\s*>?\s*\*{0,2}스타일\s*바이블[\s*]*:")


def _extract_style_bible(text: str) -> str:
    for line in text.splitlines():
        if _STYLE_BIBLE_RE.match(line):
            value = line.split(":", 1)[-1].strip()
            return re.sub(r"\*\*", "", value).strip()
    return ""


def _parse_beat(row: dict[str, str]) -> dict[str, Any]:
    beat_id = row["beat"].strip()
    visual = row["시각(shot)"].strip().replace("\ufe0f", "")  # drop emoji variation selectors (▶️ → ▶)
    raw_text = row.get("나레이션 (tts)", "").strip()
    tone, clean_text = _extract_tone_and_clean_text(raw_text)
    try:
        time_range = parse_time_range(row.get("시간", ""))
    except ValueError as exc:
        raise ValueError(f"beat {beat_id}: 시간 열을 읽을 수 없습니다 ({exc}) — 0:00~0:11 형식으로 적어 주세요") from exc
    if time_range:
        start, end = time_range
        duration = end - start
    else:
        start = None
        duration = _estimate_duration(clean_text)

    subtitle = row.get("자막", "").strip()
    kind = "narration"

    if visual.startswith(TOKEN_PAUSE):
        kind = "pause"
        clean_text = ""
        subtitle = ""
        parsed = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*초", visual)
        if parsed:
            duration = Decimal(parsed.group(1))
    elif visual.startswith((TOKEN_TITLE, TOKEN_END)):
        kind = "screen"
        clean_text = ""
        subtitle = ""
    elif not clean_text and raw_text.startswith("*["):
        kind = "visual"

    beat: dict[str, Any] = {
        "id": beat_id,
        "kind": kind,
        "text": clean_text,
        "subtitle": subtitle,
        "duration": float(duration),
    }
    if start is not None:
        beat["source_start"] = float(start)
    if tone:
        beat["tone"] = tone
    if row.get("모션·메모", "").strip():
        beat["memo"] = row["모션·메모"].strip()
    return beat


def _estimate_duration(text: str) -> Decimal:
    cleaned = re.sub(r"<[^>]+>", "", text)
    cleaned = re.sub(r"\s+", "", cleaned)
    if not cleaned:
        return Decimal("1")
    return max(Decimal("1"), Decimal(len(cleaned)) / Decimal("4.8"))


def _extract_tone_and_clean_text(raw: str) -> tuple[str | None, str]:
    tone_parts = re.findall(r"\*\[([^\]]+)\]\*", raw)
    cleaned = re.sub(r"\*\[[^\]]+\]\*\s*", "", raw).strip()
    if cleaned in {"*(정적)*", "*(엔딩)*"}:
        cleaned = ""
    return ("; ".join(tone_parts) if tone_parts else None), cleaned


def _parse_shot_meta(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    meta_by_alias: dict[str, dict[str, Any]] = {}
    seen_ids: set[str] = set()
    for row in rows:
        raw_id = row["shot id"].strip()
        shot_id, aliases = _normalize_shot_id(raw_id)
        if shot_id in seen_ids:
            raise ValueError(
                f"shot id {shot_id!r}가 샷 리스트 표에 두 번 이상 정의되어 있습니다 — 중복 행을 하나로 합치거나 id를 바꿔 주세요"
            )
        seen_ids.add(shot_id)
        source_raw = row.get("source_kind", "").strip()
        source_kind = _source_kind(source_raw)
        reuse_of = _reuse_target(source_raw) if source_kind == "reuse" else None
        meta = {
            "id": shot_id,
            "aliases": sorted(aliases),
            "source_kind": source_kind,
            "reuse_of": reuse_of,
            "listed_cover": row.get("cover", "").strip(),
            "motion_raw": row.get("모션", "").strip(),
            "prompt": row.get("프롬프트 / 문구", "").strip(),
        }
        for alias in aliases:
            meta_by_alias[alias] = meta
    return meta_by_alias


def _normalize_shot_id(raw: str) -> tuple[str, set[str]]:
    aliases = {raw}
    match = re.search(r"\((s_[A-Za-z0-9_]+)\)", raw)
    if match:
        canonical = match.group(1)
        aliases.add(raw.split("(", 1)[0].strip())
        aliases.add(canonical)
        return canonical, aliases
    canonical = raw.strip()
    aliases.add(canonical)
    return canonical, aliases


def _source_kind(raw: str) -> str:
    lowered = raw.lower()
    if lowered.startswith("text_card"):
        return "text_card"
    if lowered.startswith("screen_element"):
        return "screen_element"
    if lowered.startswith("reuse"):
        return "reuse"
    if lowered.startswith("clip"):
        return "clip"
    if lowered.startswith("stock_clip"):
        return "stock_clip"
    if lowered.startswith("remotion"):
        return "remotion"
    if lowered.startswith("hyperframe"):
        return "hyperframe"
    return "image"


def _reuse_target(raw: str) -> str | None:
    match = re.search(r"[→>-]\s*([A-Za-z0-9_]+)", raw)
    return match.group(1) if match else None


def _derive_shots(
    conti_rows: list[dict[str, str]],
    shot_meta: dict[str, dict[str, Any]],
    beat_index: dict[str, int],
) -> list[dict[str, Any]]:
    shots_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    current: str | None = None

    def canonical(raw: str) -> str:
        base = _base_stage_id(raw.strip())
        return shot_meta.get(base, {}).get("id", base)

    def ensure(shot_id: str, beat_id: str, default_kind: str = "image", reuse_of: str | None = None) -> dict[str, Any]:
        shot_id = canonical(shot_id)
        meta = shot_meta.get(shot_id, {})
        if not meta:
            for maybe in shot_meta.values():
                if shot_id in maybe.get("aliases", []):
                    meta = maybe
                    shot_id = maybe["id"]
                    break
        if shot_id not in shots_by_id:
            source_kind = meta.get("source_kind", default_kind)
            shot = {
                "id": shot_id,
                "cover": {"from": beat_id, "to": beat_id},
                "source_kind": source_kind,
                "motion": {
                    "type": _infer_motion_type(meta.get("motion_raw", "")),
                    "raw": meta.get("motion_raw", ""),
                },
                "prompt": meta.get("prompt", ""),
            }
            target = reuse_of if reuse_of else meta.get("reuse_of")
            if source_kind == "reuse" and target:
                shot["reuse_of"] = canonical(target)
            if source_kind in {"clip", "stock_clip"} and meta.get("prompt"):
                shot["src"] = meta["prompt"]
            shots_by_id[shot_id] = shot
            order.append(shot_id)
        else:
            _extend_cover(shots_by_id[shot_id], beat_id, beat_index)
        return shots_by_id[shot_id]

    for row in conti_rows:
        beat_id = row["beat"].strip()
        visual = row["시각(shot)"].strip().replace("\ufe0f", "")  # drop emoji variation selectors (▶️ → ▶)
        if visual.startswith(TOKEN_PAUSE):
            continue
        if not visual or visual.startswith(TOKEN_HOLD):
            if current is None:
                raise ValueError(
                    f"beat {beat_id}: ↓(홀드)인데 유지할 직전 shot이 없습니다 — "
                    f"▦ 몽타주 바로 다음 행에서는 ↓ 대신 ▶(새 그림)나 ↺(재사용)로 shot을 지정해 주세요"
                )
            _extend_cover(shots_by_id[current], beat_id, beat_index)
            continue
        if visual.startswith(TOKEN_MONTAGE):
            ids = [part.strip() for part in visual[1:].split("/") if part.strip()]
            anchors = _infer_anchor_texts(row.get("자막", ""), len(ids))
            for index, raw_id in enumerate(ids):
                source_id = canonical(raw_id)
                if source_id in shots_by_id:
                    # The shot already covers earlier beats; overwriting its cover
                    # would orphan them. Synthesize a reuse shot for this slot instead.
                    shot = ensure(f"s_mont_{beat_id}_{index}_{source_id}", beat_id, default_kind="reuse", reuse_of=source_id)
                    shot["source_kind"] = "reuse"
                    shot["reuse_of"] = source_id
                else:
                    shot = ensure(raw_id, beat_id)
                shot["cover"] = {"from": beat_id, "to": beat_id}
                shot["montage_slot"] = {
                    "index": index,
                    "of": len(ids),
                    "weight": 1.0,
                }
                if index < len(anchors):
                    shot["montage_slot"]["anchor_text"] = anchors[index]
            current = None
            continue
        if visual.startswith(TOKEN_REUSE):
            source_id = canonical(visual[1:].strip())
            shot_id = f"s_reuse_{beat_id}_{source_id}"
            shot = ensure(shot_id, beat_id, default_kind="reuse", reuse_of=source_id)
            shot["source_kind"] = "reuse"
            shot["reuse_of"] = source_id
            current = shot["id"]
            continue
        if visual.startswith(TOKEN_BUILD):
            raw_id = visual[1:].strip()
            stage_id = raw_id
            shot_id = canonical(raw_id)
            shot = ensure(shot_id, beat_id)
            if current == shot["id"] or beat_index[shot["cover"]["to"]] < beat_index[beat_id]:
                _extend_cover(shot, beat_id, beat_index)
            shot.setdefault("stages", []).append({"at": beat_id, "stage": stage_id})
            current = shot["id"]
            continue
        if visual.startswith(TOKEN_TEXT_CARD):
            shot = ensure(visual[1:].strip(), beat_id, default_kind="text_card")
            shot["source_kind"] = "text_card"
            current = shot["id"]
            continue
        if visual.startswith((TOKEN_TITLE, TOKEN_END)):
            shot = ensure(visual[1:].strip(), beat_id, default_kind="screen_element")
            shot["source_kind"] = "screen_element"
            current = shot["id"]
            continue
        if visual.startswith(TOKEN_NEW):
            shot = ensure(visual[1:].strip(), beat_id)
            current = shot["id"]
            continue
        raise ValueError(f"unknown visual token at {beat_id}: {visual}")

    return [shots_by_id[shot_id] for shot_id in order]


def _base_stage_id(raw: str) -> str:
    return re.sub(r"\.[0-9]+$", "", raw.strip())


def _extend_cover(shot: dict[str, Any], beat_id: str, beat_index: dict[str, int]) -> None:
    cover = shot["cover"]
    if beat_index[beat_id] < beat_index[cover["from"]]:
        cover["from"] = beat_id
    if beat_index[beat_id] > beat_index[cover["to"]]:
        cover["to"] = beat_id


def _infer_motion_type(value: str) -> str:
    lowered = value.lower()
    if "zoom_out" in lowered or "zoom out" in lowered:
        return "zoom_out"
    if "zoom_in" in lowered or "push" in lowered or "micro zoom" in lowered or "zoom" in lowered:
        return "zoom_in"
    if "pan_lr" in lowered or "pan" in lowered:
        return "pan_lr"
    if "fade" in lowered:
        return "fade"
    if "shake" in lowered:
        return "shake"
    return "static"


def _infer_anchor_texts(subtitle: str, count: int) -> list[str]:
    if not subtitle or count <= 0:
        return []
    parts = [p.strip(" ?!·") for p in re.split(r"[·,/]|\s{2,}", subtitle) if p.strip(" ?!·")]
    if len(parts) == count:
        return parts
    question_parts = [p.strip() for p in re.split(r"\?", subtitle) if p.strip()]
    if len(question_parts) == count:
        return question_parts
    return []
