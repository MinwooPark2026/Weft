from __future__ import annotations

from pathlib import Path
from typing import Any


def validate_project(project: dict[str, Any], picks: dict[str, Any] | None = None, root: str | Path | None = None) -> list[dict[str, str]]:
    beats = project["narration"]["beats"]
    shots = project["visuals"]["shots"]
    beat_index = {beat["id"]: index for index, beat in enumerate(beats)}
    coverable = [beat["id"] for beat in beats if beat.get("kind") != "pause"]
    violations: list[dict[str, str]] = []

    coverage: dict[str, list[str]] = {beat_id: [] for beat_id in coverable}
    montage_groups: dict[str, list[dict[str, Any]]] = {}
    shot_ids = {shot["id"] for shot in shots}
    pick_required = {
        shot["id"]
        for shot in shots
        if shot.get("source_kind") not in {"reuse", "clip", "stock_clip", "remotion", "hyperframe"}
    }

    for shot in shots:
        cover = shot.get("cover")
        if not isinstance(cover, dict) or "from" not in cover or "to" not in cover:
            violations.append(_violation("I2", "error", shot["id"], "cover must be {from,to}"))
            continue
        start = cover["from"]
        end = cover["to"]
        if start not in beat_index or end not in beat_index:
            violations.append(_violation("I2", "error", shot["id"], "cover references an unknown beat"))
            continue
        if beat_index[start] > beat_index[end]:
            violations.append(_violation("I2", "error", shot["id"], "cover.from must not be after cover.to"))
            continue
        covered = beats[beat_index[start] : beat_index[end] + 1]
        if beats[beat_index[start]].get("kind") == "pause" or beats[beat_index[end]].get("kind") == "pause":
            violations.append(_violation("I9", "error", shot["id"], "cover endpoints must not be pause beats"))
        for beat in covered:
            if beat.get("kind") != "pause":
                coverage.setdefault(beat["id"], []).append(shot["id"])
        slot = shot.get("montage_slot")
        if slot:
            if start != end:
                violations.append(_violation("I3", "error", shot["id"], "montage slots must cover exactly one beat"))
            montage_groups.setdefault(start, []).append(shot)
            if slot.get("weight", 1) <= 0:
                violations.append(_violation("I5", "error", shot["id"], "montage_slot.weight must be positive"))
        if shot.get("source_kind") == "reuse":
            target = shot.get("reuse_of")
            if not target or target not in shot_ids:
                violations.append(_violation("I6", "error", shot["id"], "reuse_of target does not exist"))
            elif next(s for s in shots if s["id"] == target).get("source_kind") == "reuse":
                violations.append(_violation("I6", "error", shot["id"], "reuse chains are not allowed"))

    for beat_id, shot_list in coverage.items():
        if not shot_list:
            violations.append(_violation("I1", "error", beat_id, "beat is not covered by any shot"))
        elif len(shot_list) > 1 and not all(_shot_by_id(shots, shot_id).get("montage_slot") for shot_id in shot_list):
            violations.append(_violation("I3", "error", beat_id, "overlapping non-montage coverage"))

    prev_id: str | None = None
    prev_source_end: float | None = None
    for beat in beats:
        source_start = beat.get("source_start")
        if source_start is None:
            prev_id = None
            prev_source_end = None
            continue
        if prev_source_end is not None and abs(float(source_start) - prev_source_end) > 1e-6:
            violations.append(
                _violation(
                    "I11",
                    "warning",
                    beat["id"],
                    f"시간 열이 이어지지 않습니다: {prev_id} 끝 {prev_source_end:g}s ≠ {beat['id']} 시작 "
                    f"{float(source_start):g}s — CONTI.md에서 두 행의 시간 열을 확인하세요",
                )
            )
        prev_id = beat["id"]
        prev_source_end = float(source_start) + float(beat.get("duration", 0))

    # I12 — 페이스 경고(warning): 한 video 이벤트(=한 shot이 화면을 차지하는 구간)가 15초를 넘으면
    # 시각 정체로 본다. 움직임이 내장된 소스(clip/stock_clip/remotion/hyperframe)는 제외.
    moving_kinds = {"clip", "stock_clip", "remotion", "hyperframe"}
    for shot in shots:
        cover = shot.get("cover")
        if not isinstance(cover, dict):
            continue
        start = cover.get("from")
        end = cover.get("to")
        if start not in beat_index or end not in beat_index or beat_index[start] > beat_index[end]:
            continue
        if shot.get("source_kind") in moving_kinds:
            continue
        seconds = sum(
            float(beat.get("duration", 0.0)) for beat in beats[beat_index[start] : beat_index[end] + 1]
        )
        slot = shot.get("montage_slot")
        if slot:
            siblings = montage_groups.get(start, [])
            total_weight = sum(float(s["montage_slot"].get("weight", 1.0)) for s in siblings) or 1.0
            seconds *= float(slot.get("weight", 1.0)) / total_weight
        if seconds > 15.0:
            violations.append(
                _violation(
                    "I12",
                    "warning",
                    shot["id"],
                    f"페이스 경고: {shot['id']}가 {seconds:.1f}초({start}~{end}) — 한 장면이 15초를 초과합니다. "
                    "▶ 분할·▦ 몽타주·⤴ 빌드업으로 장면을 나눠 보세요",
                )
            )

    for beat_id, slot_shots in montage_groups.items():
        of_values = {shot["montage_slot"].get("of") for shot in slot_shots}
        if len(of_values) != 1:
            violations.append(_violation("I4", "error", beat_id, "montage slots disagree on 'of'"))
            continue
        expected = int(next(iter(of_values)))
        indexes = sorted(int(shot["montage_slot"].get("index", -1)) for shot in slot_shots)
        if indexes != list(range(expected)):
            violations.append(_violation("I4", "error", beat_id, "montage indexes must be 0..of-1 exactly once"))

    if picks is not None:
        selections = set(picks.get("selections", {}).keys())
        auto_picked = set(picks.get("auto_picked", []))
        overridden = set(picks.get("overridden", []))
        if not selections <= pick_required:
            extra = selections - pick_required
            violations.append(
                _violation(
                    "I7",
                    "error",
                    "PICKS.json",
                    "selections contains reuse or unknown shots: " + ", ".join(sorted(extra)[:8]),
                )
            )
        missing = pick_required - selections
        if missing:
            violations.append(_violation("I7", "error", "PICKS.json", "missing selections: " + ", ".join(sorted(missing)[:8])))
        if auto_picked & overridden:
            violations.append(_violation("I8", "error", "PICKS.json", "auto_picked and overridden overlap"))
        if auto_picked | overridden != selections:
            violations.append(_violation("I8", "error", "PICKS.json", "selection provenance does not match selections"))
        if root is not None:
            root_path = Path(root)
            for shot_id, rel_path in picks.get("selections", {}).items():
                if not (root_path / "SHOTS" / shot_id / rel_path).exists():
                    violations.append(_violation("I10", "error", shot_id, f"selected file missing: {rel_path}"))

    return violations


def _shot_by_id(shots: list[dict[str, Any]], shot_id: str) -> dict[str, Any]:
    return next(shot for shot in shots if shot["id"] == shot_id)


def _violation(invariant: str, severity: str, where: str, fix_hint: str) -> dict[str, str]:
    return {"invariant": invariant, "severity": severity, "where": where, "fix_hint": fix_hint}
