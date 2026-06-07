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
    non_reuse = {shot["id"] for shot in shots if shot.get("source_kind") != "reuse"}

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
        if not selections <= non_reuse:
            violations.append(_violation("I7", "error", "PICKS.json", "selections contains reuse or unknown shots"))
        missing = non_reuse - selections
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
