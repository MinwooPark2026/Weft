from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parser import parse_conti
from .validate import validate_project
from .writer import write_project

DEFAULT_CONTI = "CONTI.md"
DEFAULT_PROJECT = "generated_project"


def _missing_conti(path: str) -> str | None:
    if Path(path).is_file():
        return None
    return (
        f"'{path}' 가 없습니다. CONTI.md 가 있는 프로젝트 폴더에서 실행하거나 "
        f"경로를 인자로 주세요 (예: weft conti path/to/CONTI.md)."
    )


def _missing_project(path: str) -> str | None:
    if Path(path).is_dir():
        return None
    return f"'{path}' 폴더가 없습니다. 먼저 'weft conti' 로 프로젝트를 생성하세요."


def _default_capcut_folder(project_dir: str | Path) -> str:
    path = Path(project_dir).resolve()
    project_name = path.parent.name if path.name == DEFAULT_PROJECT else path.name
    return f"weft_{project_name or Path.cwd().name or 'project'}"


def _capcut_registration(no_register: bool) -> tuple[bool, bool]:
    from .exporters.capcut_draft import capcut_running

    running = capcut_running()
    return (not no_register and not running), running


def _skill_paths() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    return {
        "codex": str(root / ".agents" / "skills" / "script-to-conti" / "SKILL.md"),
        "claude": str(root / ".claude" / "skills" / "script-to-conti" / "SKILL.md"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="weft",
        description="Weft — dual-track explainer video toolchain",
        epilog="AI assistants: run `weft whereisskill` to locate the script-to-conti SKILL.md files.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    parse_cmd = sub.add_parser("parse", help="Parse CONTI.md and print project JSON")
    parse_cmd.add_argument("conti", nargs="?", default=DEFAULT_CONTI)

    validate_cmd = sub.add_parser("validate", help="Parse and validate CONTI.md")
    validate_cmd.add_argument("conti", nargs="?", default=DEFAULT_CONTI)

    conti_cmd = sub.add_parser("conti", aliases=["dryrun"], help="Build a project from CONTI.md (parse/validate/compile)")
    conti_cmd.add_argument("conti", nargs="?", default=DEFAULT_CONTI)
    conti_cmd.add_argument("--out", default=DEFAULT_PROJECT, help="output project dir (default ./generated_project)")
    conti_cmd.add_argument("--no-assets", action="store_true", help="Do not materialize placeholder SVG assets")

    tts_cmd = sub.add_parser("tts", help="Synthesize narration audio (Typecast) into the project dir")
    tts_cmd.add_argument("project_dir", nargs="?", default=DEFAULT_PROJECT)
    tts_cmd.add_argument("--voice", help="voice_id override (default from .env TYPECAST_VOICE)")
    tts_cmd.add_argument("--limit", type=int, help="only the first N voice beats (smoke test)")
    tts_cmd.add_argument("--beats", help="comma-separated beat ids to (re)generate")
    tts_cmd.add_argument("--force", action="store_true", help="ignore cache and re-synthesize")
    tts_cmd.add_argument("--no-recompile", action="store_true", help="skip recompiling exports")
    tts_cmd.add_argument("--allow-partial", action="store_true", help="return success even if some beats fail")

    img_cmd = sub.add_parser("images", help="Generate shot images (OpenAI gpt-image-1) into the project dir")
    img_cmd.add_argument("project_dir", nargs="?", default=DEFAULT_PROJECT)
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
    cap_cmd.add_argument("project_dir", nargs="?", default=DEFAULT_PROJECT)
    cap_cmd.add_argument("--folder", default=None, help="CapCut draft folder name (default weft_<project folder>)")
    cap_cmd.add_argument("--capcut-root", help="CapCut Projects/com.lveditor.draft path override")
    cap_cmd.add_argument("--no-motion", action="store_true", help="place clips static (no keyframes)")
    cap_cmd.add_argument("--no-audio", action="store_true", help="skip audio track")
    cap_cmd.add_argument("--images-only", action="store_true", help="only image shots (skip cards) — smoke test")
    cap_cmd.add_argument("--limit", type=int, help="only the first N video events (smoke test)")
    cap_cmd.add_argument("--no-register", action="store_true", help="do not touch root_meta_info.json")

    pick_cmd = sub.add_parser("pick", help="Launch the image-candidate picker UI (local browser)")
    pick_cmd.add_argument("project_dir", nargs="?", default=DEFAULT_PROJECT)
    pick_cmd.add_argument("--port", type=int, default=8770)
    pick_cmd.add_argument("--no-browser", action="store_true")

    all_cmd = sub.add_parser("all", help="conti -> tts -> images -> capcut (TTS/이미지 API 비용 발생)")
    all_cmd.add_argument("conti", nargs="?", default=DEFAULT_CONTI)
    all_cmd.add_argument("--out", default=DEFAULT_PROJECT)
    all_cmd.add_argument("--n", type=int, help="image candidates per shot")
    all_cmd.add_argument("--folder", default=None, help="CapCut draft folder name (default weft_<project folder>)")
    all_cmd.add_argument("--no-register", action="store_true", help="do not touch root_meta_info.json")

    skill_cmd = sub.add_parser("whereisskill", help="Print script-to-conti SKILL.md paths for AI assistants")
    skill_cmd.add_argument("--json", action="store_true", help="print machine-readable paths")

    args = parser.parse_args(argv)

    if args.command == "parse":
        msg = _missing_conti(args.conti)
        if msg:
            print(msg, file=sys.stderr)
            return 2
        project = parse_conti(args.conti)
        print(json.dumps(project, ensure_ascii=False, indent=2))
        return 0
    if args.command == "validate":
        msg = _missing_conti(args.conti)
        if msg:
            print(msg, file=sys.stderr)
            return 2
        project = parse_conti(args.conti)
        violations = validate_project(project)
        print(json.dumps(violations, ensure_ascii=False, indent=2))
        return 1 if any(item["severity"] == "error" for item in violations) else 0
    if args.command == "conti":  # argparse maps the `dryrun` alias to this too
        msg = _missing_conti(args.conti)
        if msg:
            print(msg, file=sys.stderr)
            return 2
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
        msg = _missing_project(args.project_dir)
        if msg:
            print(msg, file=sys.stderr)
            return 2
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
        msg = _missing_project(args.project_dir)
        if msg:
            print(msg, file=sys.stderr)
            return 2
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
        msg = _missing_project(args.project_dir)
        if msg:
            print(msg, file=sys.stderr)
            return 2
        from .exporters.capcut_draft import build_capcut_draft

        folder = args.folder or _default_capcut_folder(args.project_dir)
        register, running = _capcut_registration(args.no_register)
        summary = build_capcut_draft(
            args.project_dir,
            folder_name=folder,
            capcut_root=args.capcut_root,
            with_motion=not args.no_motion,
            with_audio=not args.no_audio,
            images_only=args.images_only,
            limit=args.limit,
            register=register,
        )
        summary["capcut_running"] = running
        summary["folder_name"] = folder
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    if args.command == "pick":
        msg = _missing_project(args.project_dir)
        if msg:
            print(msg, file=sys.stderr)
            return 2
        from .picker.server import serve

        serve(args.project_dir, port=args.port, open_browser=not args.no_browser)
        return 0
    if args.command == "all":
        msg = _missing_conti(args.conti)
        if msg:
            print(msg, file=sys.stderr)
            return 2
        from .assets import generate_images, generate_tts
        from .exporters.capcut_draft import build_capcut_draft

        project = parse_conti(args.conti)
        result = write_project(project, Path(args.out), materialize_assets=True)
        errors = [item for item in result["violations"] if item["severity"] == "error"]
        if errors:
            print(f"validation_errors={len(errors)} — 콘티를 고친 뒤 다시 실행하세요.", file=sys.stderr)
            return 1
        print(f"✓ conti → {args.out}")
        generate_tts(args.out)
        print("✓ tts")
        generate_images(args.out, n=args.n)
        print("✓ images")
        folder = args.folder or _default_capcut_folder(args.out)
        register, running = _capcut_registration(args.no_register)
        build_capcut_draft(args.out, folder_name=folder, register=register)
        if running and not args.no_register:
            print(f"✓ capcut → {folder} (CapCut 실행 중이라 등록 생략)")
        else:
            print(f"✓ capcut → {folder}")
        print("후보를 직접 고르려면:  weft pick")
        return 0
    if args.command == "whereisskill":
        paths = _skill_paths()
        if args.json:
            print(json.dumps({"skill": "script-to-conti", "paths": paths}, ensure_ascii=False, indent=2))
        else:
            print("script-to-conti skill paths:")
            print(f"  Codex : {paths['codex']}")
            print(f"  Claude: {paths['claude']}")
            print("Ask the AI to read the matching SKILL.md, then convert SCRIPT.md to CONTI.md and run `weft conti`.")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
