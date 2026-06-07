from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parser import parse_conti
from .validate import validate_project
from .writer import write_project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weft", description="Weft dual-track dry-run CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    parse_cmd = sub.add_parser("parse", help="Parse CONTI.md and print project JSON")
    parse_cmd.add_argument("conti")

    validate_cmd = sub.add_parser("validate", help="Parse and validate CONTI.md")
    validate_cmd.add_argument("conti")

    dryrun_cmd = sub.add_parser("dryrun", help="Generate a dry-run Weft project")
    dryrun_cmd.add_argument("conti")
    dryrun_cmd.add_argument("--out", required=True)
    dryrun_cmd.add_argument("--no-assets", action="store_true", help="Do not materialize placeholder SVG assets")

    tts_cmd = sub.add_parser("tts", help="Synthesize narration audio (Typecast) into a project dir")
    tts_cmd.add_argument("project_dir")
    tts_cmd.add_argument("--voice", help="voice_id override (default from .env TYPECAST_VOICE)")
    tts_cmd.add_argument("--limit", type=int, help="only the first N voice beats (smoke test)")
    tts_cmd.add_argument("--beats", help="comma-separated beat ids to (re)generate")
    tts_cmd.add_argument("--force", action="store_true", help="ignore cache and re-synthesize")
    tts_cmd.add_argument("--no-recompile", action="store_true", help="skip recompiling exports")
    tts_cmd.add_argument("--allow-partial", action="store_true", help="return success even if some beats fail")

    img_cmd = sub.add_parser("images", help="Generate shot images (OpenAI gpt-image-1) into a project dir")
    img_cmd.add_argument("project_dir")
    img_cmd.add_argument("--limit", type=int, help="only the first N image shots (smoke test)")
    img_cmd.add_argument("--shots", help="comma-separated shot ids to (re)generate")
    img_cmd.add_argument("--n", type=int, help="candidates per shot (default .env IMAGE_CANDIDATES_N)")
    img_cmd.add_argument("--quality", help="low|medium|high (default medium)")
    img_cmd.add_argument("--size", help="image size, default 1536x1024")
    img_cmd.add_argument("--estimate", action="store_true", help="print count/cost preview, do not call API")
    img_cmd.add_argument("--force", action="store_true", help="ignore cache and re-generate")
    img_cmd.add_argument("--no-recompile", action="store_true", help="skip recompiling exports")
    img_cmd.add_argument("--allow-partial", action="store_true", help="return success even if some shots fail")

    cap_cmd = sub.add_parser("capcut", help="Build a CapCut draft from a project's render_plan")
    cap_cmd.add_argument("project_dir")
    cap_cmd.add_argument("--folder", default="weft_ep2", help="CapCut draft folder name")
    cap_cmd.add_argument("--capcut-root", help="CapCut Projects/com.lveditor.draft path override")
    cap_cmd.add_argument("--no-motion", action="store_true", help="place clips static (no keyframes)")
    cap_cmd.add_argument("--no-audio", action="store_true", help="skip audio track")
    cap_cmd.add_argument("--images-only", action="store_true", help="only image shots (skip cards) — smoke test")
    cap_cmd.add_argument("--limit", type=int, help="only the first N video events (smoke test)")
    cap_cmd.add_argument("--no-register", action="store_true", help="do not touch root_meta_info.json")

    pick_cmd = sub.add_parser("pick", help="Launch the image-candidate picker UI (local browser)")
    pick_cmd.add_argument("project_dir")
    pick_cmd.add_argument("--port", type=int, default=8770)
    pick_cmd.add_argument("--no-browser", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "parse":
        project = parse_conti(args.conti)
        print(json.dumps(project, ensure_ascii=False, indent=2))
        return 0
    if args.command == "validate":
        project = parse_conti(args.conti)
        violations = validate_project(project)
        print(json.dumps(violations, ensure_ascii=False, indent=2))
        return 1 if any(item["severity"] == "error" for item in violations) else 0
    if args.command == "dryrun":
        project = parse_conti(args.conti)
        result = write_project(project, Path(args.out), materialize_assets=not args.no_assets)
        errors = [item for item in result["violations"] if item["severity"] == "error"]
        print(f"wrote {args.out}")
        print(f"validation_errors={len(errors)}")
        print(f"video_events={len(result['render_plan']['video'])}")
        print(f"subtitle_events={len(result['render_plan']['subtitles'])}")
        print(f"total_seconds={result['render_plan']['total_seconds']:.3f}")
        return 1 if errors else 0
    if args.command == "tts":
        from .assets import generate_tts

        def _progress(i: int, total: int, beat_id: str, status: str, dur: float) -> None:
            mark = "·" if status == "cache" else "✓"
            print(f"  {mark} [{i:>3}/{total}] {beat_id} {dur:6.2f}s {status}", flush=True)

        beat_ids = [b.strip() for b in args.beats.split(",")] if args.beats else None
        summary = generate_tts(
            args.project_dir,
            voice_id=args.voice,
            limit=args.limit,
            beat_ids=beat_ids,
            force=args.force,
            recompile=not args.no_recompile,
            progress=_progress,
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 1 if summary.get("failed") and not args.allow_partial else 0
    if args.command == "images":
        from .assets import generate_images

        def _progress(i: int, total: int, shot_id: str, status: str, _dur: float) -> None:
            mark = "·" if status == "cache" else "✓"
            print(f"  {mark} [{i:>3}/{total}] {shot_id} {status}", flush=True)

        shot_ids = [s.strip() for s in args.shots.split(",")] if args.shots else None
        summary = generate_images(
            args.project_dir,
            limit=args.limit,
            shot_ids=shot_ids,
            n=args.n,
            quality=args.quality,
            size=args.size,
            force=args.force,
            recompile=not args.no_recompile,
            estimate=args.estimate,
            progress=None if args.estimate else _progress,
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 1 if summary.get("failed") and not args.allow_partial else 0
    if args.command == "capcut":
        from .exporters.capcut_draft import build_capcut_draft

        summary = build_capcut_draft(
            args.project_dir,
            folder_name=args.folder,
            capcut_root=args.capcut_root,
            with_motion=not args.no_motion,
            with_audio=not args.no_audio,
            images_only=args.images_only,
            limit=args.limit,
            register=not args.no_register,
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    if args.command == "pick":
        from .picker.server import serve

        serve(args.project_dir, port=args.port, open_browser=not args.no_browser)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
