from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parser import parse_conti
from .settings import (
    apply_project_settings,
    ensure_settings_file,
    find_settings_file,
    load_project_settings,
    parse_settings,
    SETTINGS_FILE,
    setting_bool,
    setting_int,
    setting_str,
    settings_payload,
)
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


def _missing_project(path: str, required: str | None = None) -> str | None:
    root = Path(path)
    if not root.is_dir():
        return f"'{path}' 폴더가 없습니다. 먼저 'weft conti' 로 프로젝트를 생성하세요."
    if required is not None and not (root / required).is_file():
        return (
            f"'{path}' 안에 {required} 가 없습니다. "
            f"'weft conti' 를 먼저 실행해 프로젝트 파일을 만든 뒤 다시 시도하세요."
        )
    return None


def _default_capcut_folder(project_dir: str | Path) -> str:
    path = Path(project_dir).resolve()
    project_name = path.parent.name if path.name == DEFAULT_PROJECT else path.name
    return f"weft_{project_name or Path.cwd().name or 'project'}"


def _capcut_registration(no_register: bool) -> tuple[bool, bool]:
    from .exporters.capcut_draft import capcut_running

    running = capcut_running()
    return (not no_register and not running), running


def _seed_cards(conti: str | Path, out_dir: str | Path) -> bool:
    """CONTI.md 옆의 CARDS.json 을 프로젝트 출력으로 복사한다.

    텍스트카드 문구를 generated_project 에만 두면 conti 재실행 때 사라지고
    저장소에 커밋할 수도 없다 — STYLE.txt 처럼 콘티 옆이 정본이다.
    """
    src = Path(conti).resolve().parent / "CARDS.json"
    if not src.is_file():
        return False
    dest = Path(out_dir) / "CARDS.json"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def _skill_paths() -> dict[str, dict[str, str]]:
    """저장소의 모든 스킬을 {스킬명: {claude: 경로, codex: 경로}} 로 수집한다."""
    root = Path(__file__).resolve().parents[1]
    skills: dict[str, dict[str, str]] = {}
    for agent, base in (("claude", ".claude"), ("codex", ".agents")):
        for skill_md in sorted(root.glob(f"{base}/skills/*/SKILL.md")):
            skills.setdefault(skill_md.parent.name, {})[agent] = str(skill_md)
    return skills


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except (RuntimeError, FileNotFoundError) as exc:
        # Provider/setup errors (예: API 키 미설정) 는 친절한 메시지를 담고 있다.
        # 비개발자 사용자에게 트레이스백 대신 메시지만 보여준다.
        print(str(exc), file=sys.stderr)
        return 1


def _run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="weft",
        description="Weft — dual-track explainer video toolchain",
        epilog="AI assistants: run `weft whereisskill` to locate the Weft SKILL.md files (script-to-conti, conti-qa, visual-qa, animation-render).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    parse_cmd = sub.add_parser("parse", help="Parse CONTI.md and print project JSON")
    parse_cmd.add_argument("conti", nargs="?", default=DEFAULT_CONTI)

    validate_cmd = sub.add_parser("validate", help="Parse and validate CONTI.md")
    validate_cmd.add_argument("conti", nargs="?", default=DEFAULT_CONTI)

    conti_cmd = sub.add_parser("conti", aliases=["dryrun"], help="Build a project from CONTI.md (parse/validate/compile)")
    conti_cmd.set_defaults(command="conti")  # `weft dryrun` alias also dispatches here
    conti_cmd.add_argument("conti", nargs="?", default=DEFAULT_CONTI)
    conti_cmd.add_argument("--out", default=None, help="output project dir (default ./generated_project)")
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

    anim_cmd = sub.add_parser("animate", help="Prepare/check Remotion/HyperFrame animation clip shots")
    anim_cmd.add_argument("project_dir", nargs="?", default=DEFAULT_PROJECT)
    anim_cmd.add_argument("--refresh-specs", action="store_true", help="overwrite generated animation SPEC.md files")
    anim_cmd.add_argument("--check", action="store_true", help="return nonzero if animation output clips are missing")
    anim_cmd.add_argument("--no-recompile", action="store_true", help="skip recompiling exports before checking")

    cap_cmd = sub.add_parser("capcut", help="Build a CapCut draft from a project's render_plan")
    cap_cmd.add_argument("project_dir", nargs="?", default=DEFAULT_PROJECT)
    cap_cmd.add_argument("--folder", default=None, help="CapCut draft folder name (default weft_<project folder>)")
    cap_cmd.add_argument("--capcut-root", help="CapCut Projects/com.lveditor.draft path override")
    cap_cmd.add_argument("--no-motion", action="store_true", help="place clips static (no keyframes)")
    cap_cmd.add_argument("--no-audio", action="store_true", help="skip audio track")
    cap_cmd.add_argument("--images-only", action="store_true", help="only image shots (skip cards) — smoke test")
    cap_cmd.add_argument("--limit", type=int, help="only the first N video events (smoke test)")
    cap_cmd.add_argument("--no-register", action="store_true", help="do not touch root_meta_info.json")

    fcpxml_cmd = sub.add_parser("fcpxml", help="Export a project's render_plan as Final Cut Pro XML")
    fcpxml_cmd.add_argument("project_dir", nargs="?", default=DEFAULT_PROJECT)
    fcpxml_cmd.add_argument("--output", "-o", help="output FCPXML path (default ./generated_project/EXPORTS/weft_timeline.fcpxml)")

    ffmpeg_cmd = sub.add_parser("ffmpeg", aliases=["render"], help="Render an MP4 from a project's render_plan with ffmpeg")
    ffmpeg_cmd.set_defaults(command="ffmpeg")
    ffmpeg_cmd.add_argument("project_dir", nargs="?", default=DEFAULT_PROJECT)
    ffmpeg_cmd.add_argument("--output", "-o", help="output mp4 path (default ./generated_project/EXPORTS/weft_render.mp4)")
    ffmpeg_cmd.add_argument("--ffmpeg-bin", default=None, help="ffmpeg executable path/name")
    ffmpeg_cmd.add_argument("--encoder", default=None, help="video encoder: auto|h264_videotoolbox|libx264")
    ffmpeg_cmd.add_argument("--width", type=int, default=None, help="output width in pixels")
    ffmpeg_cmd.add_argument("--height", type=int, default=None, help="output height in pixels")
    ffmpeg_cmd.add_argument("--preset", default=None, help="libx264 preset")
    ffmpeg_cmd.add_argument("--crf", type=int, default=None, help="libx264 CRF quality; lower is higher quality")
    ffmpeg_cmd.add_argument("--bitrate", default=None, help="VideoToolbox target bitrate")
    ffmpeg_cmd.add_argument("--no-motion", action="store_true", help="render all stills static")
    ffmpeg_cmd.add_argument("--no-audio", action="store_true", help="skip narration audio")
    ffmpeg_cmd.add_argument("--no-subtitles", action="store_true", help="do not burn subtitles into the mp4")
    ffmpeg_cmd.add_argument("--dry-run", action="store_true", help="print the planned ffmpeg command without rendering")

    pick_cmd = sub.add_parser("pick", help="Launch the image-candidate picker UI (local browser)")
    pick_cmd.add_argument("project_dir", nargs="?", default=DEFAULT_PROJECT)
    pick_cmd.add_argument("--port", type=int, default=8770)
    pick_cmd.add_argument("--no-browser", action="store_true")

    all_cmd = sub.add_parser("all", help="quick auto run for humans: conti -> tts -> images -> animate check -> ffmpeg")
    all_cmd.add_argument("conti", nargs="?", default=DEFAULT_CONTI)
    all_cmd.add_argument("--out", default=None)
    all_cmd.add_argument("--n", type=int, help="image candidates per shot")
    all_cmd.add_argument("--folder", default=None, help="CapCut draft folder name (default weft_<project folder>)")
    all_cmd.add_argument("--no-register", action="store_true", help="do not touch root_meta_info.json")
    all_cmd.add_argument("--capcut", action="store_true", help="also build a CapCut draft after the MP4 render")
    all_cmd.add_argument("--fcpxml", action="store_true", help="also export ./generated_project/EXPORTS/weft_timeline.fcpxml")
    all_cmd.add_argument("--fcpxml-output", help="FCPXML output path when --fcpxml is used")
    all_cmd.add_argument("--ffmpeg", action="store_true", help="render MP4 (default; kept for compatibility)")
    all_cmd.add_argument("--no-ffmpeg", action="store_true", help="skip the default MP4 render")
    all_cmd.add_argument("--ffmpeg-output", help="mp4 output path for the default ffmpeg render")
    all_cmd.add_argument("--ffmpeg-encoder", default=None, help="video encoder for --ffmpeg")
    all_cmd.add_argument("--ffmpeg-width", type=int, default=None, help="output width for --ffmpeg")
    all_cmd.add_argument("--ffmpeg-height", type=int, default=None, help="output height for --ffmpeg")
    all_cmd.add_argument("--ffmpeg-preset", default=None, help="libx264 preset for --ffmpeg")
    all_cmd.add_argument("--ffmpeg-crf", type=int, default=None, help="libx264 CRF for --ffmpeg")
    all_cmd.add_argument("--ffmpeg-bitrate", default=None, help="VideoToolbox bitrate for --ffmpeg")
    all_cmd.add_argument("--no-subtitles", action="store_true", help="do not burn subtitles when --ffmpeg is used")
    all_cmd.add_argument("--allow-partial", action="store_true", help="tts/images 일부가 실패해도 계속 진행")

    settings_cmd = sub.add_parser("settings", help=f"Show or create project {SETTINGS_FILE} settings")
    settings_cmd.add_argument("path", nargs="?", default=".", help="project folder, generated_project folder, or CONTI.md")
    settings_cmd.add_argument("--init", action="store_true", help="create WEFT_SETTINGS.txt if missing")
    settings_cmd.add_argument("--json", action="store_true", help="print machine-readable active settings")

    skill_cmd = sub.add_parser("whereisskill", help="Print Weft SKILL.md paths for AI assistants")
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
    if args.command == "conti":
        msg = _missing_conti(args.conti)
        if msg:
            print(msg, file=sys.stderr)
            return 2
        settings_path = ensure_settings_file(args.conti)
        settings = load_project_settings(args.conti)
        out_dir = args.out or setting_str(settings, "PROJECT_OUT", DEFAULT_PROJECT) or DEFAULT_PROJECT
        project = parse_conti(args.conti)
        result = write_project(project, Path(out_dir), materialize_assets=not args.no_assets)
        if _seed_cards(args.conti, out_dir):
            print("cards=CARDS.json (CONTI.md 옆 파일을 복사)")
        errors = [item for item in result["violations"] if item["severity"] == "error"]
        print(f"settings={settings_path}")
        print(f"wrote {out_dir}")
        print(f"validation_errors={len(errors)}")
        print(f"video_events={len(result['render_plan']['video'])}")
        print(f"subtitle_events={len(result['render_plan']['subtitles'])}")
        print(f"total_seconds={result['render_plan']['total_seconds']:.3f}")
        return 1 if errors else 0
    if args.command == "tts":
        msg = _missing_project(args.project_dir, required="NARRATION.json")
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
        msg = _missing_project(args.project_dir, required="VISUALS.json")
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
    if args.command == "animate":
        msg = _missing_project(args.project_dir, required="VISUALS.json")
        if msg:
            print(msg, file=sys.stderr)
            return 2
        from .animation import prepare_animation_shots

        summary = prepare_animation_shots(
            args.project_dir,
            refresh_specs=args.refresh_specs,
            check=args.check,
            recompile=not args.no_recompile,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if args.check and summary.get("pending") else 0
    if args.command == "capcut":
        msg = _missing_project(args.project_dir, required="EXPORTS/render_plan.json")
        if msg:
            print(msg, file=sys.stderr)
            return 2
        from .exporters.capcut_draft import build_capcut_draft

        settings = load_project_settings(args.project_dir)
        folder = args.folder or setting_str(settings, "CAPCUT_FOLDER") or _default_capcut_folder(args.project_dir)
        no_register = args.no_register or setting_bool(settings, "CAPCUT_NO_REGISTER")
        no_motion = args.no_motion or setting_bool(settings, "CAPCUT_NO_MOTION")
        no_audio = args.no_audio or setting_bool(settings, "CAPCUT_NO_AUDIO")
        register, running = _capcut_registration(no_register)
        summary = build_capcut_draft(
            args.project_dir,
            folder_name=folder,
            capcut_root=args.capcut_root,
            with_motion=not no_motion,
            with_audio=not no_audio,
            images_only=args.images_only,
            limit=args.limit,
            register=register,
        )
        summary["capcut_running"] = running
        summary["folder_name"] = folder
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    if args.command == "fcpxml":
        msg = _missing_project(args.project_dir, required="EXPORTS/render_plan.json")
        if msg:
            print(msg, file=sys.stderr)
            return 2
        from .exporters.fcpxml import export_fcpxml

        try:
            settings = load_project_settings(args.project_dir)
            output = args.output or setting_str(settings, "FCPXML_OUTPUT")
            summary = export_fcpxml(args.project_dir, output=output)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.command == "ffmpeg":
        msg = _missing_project(args.project_dir, required="EXPORTS/render_plan.json")
        if msg:
            print(msg, file=sys.stderr)
            return 2
        from .exporters.ffmpeg_render import render_ffmpeg

        try:
            settings = load_project_settings(args.project_dir)
            summary = render_ffmpeg(
                args.project_dir,
                output=args.output or setting_str(settings, "FFMPEG_OUTPUT"),
                ffmpeg_bin=args.ffmpeg_bin or setting_str(settings, "FFMPEG_BIN", "ffmpeg") or "ffmpeg",
                with_motion=not (args.no_motion or setting_bool(settings, "FFMPEG_NO_MOTION")),
                with_audio=not (args.no_audio or setting_bool(settings, "FFMPEG_NO_AUDIO")),
                with_subtitles=not (args.no_subtitles or setting_bool(settings, "FFMPEG_NO_SUBTITLES")),
                encoder=args.encoder or setting_str(settings, "FFMPEG_ENCODER", "auto") or "auto",
                width=args.width if args.width is not None else (setting_int(settings, "FFMPEG_WIDTH", 1920) or 1920),
                height=args.height if args.height is not None else (setting_int(settings, "FFMPEG_HEIGHT", 1080) or 1080),
                preset=args.preset or setting_str(settings, "FFMPEG_PRESET", "veryfast") or "veryfast",
                crf=args.crf if args.crf is not None else (setting_int(settings, "FFMPEG_CRF", 20) or 20),
                bitrate=args.bitrate or setting_str(settings, "FFMPEG_BITRATE", "8M") or "8M",
                dry_run=args.dry_run,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.command == "pick":
        msg = _missing_project(args.project_dir, required="VISUALS.json")
        if msg:
            print(msg, file=sys.stderr)
            return 2
        from .picker.server import serve

        serve(args.project_dir, port=args.port, open_browser=not args.no_browser)
        return 0
    if args.command == "settings":
        settings_path = ensure_settings_file(args.path) if args.init else (find_settings_file(args.path) or ensure_settings_file(args.path))
        settings = parse_settings(settings_path)
        if args.json:
            print(json.dumps(settings_payload(settings, settings_path), ensure_ascii=False, indent=2))
        else:
            print(f"settings={settings_path}")
            print(settings_path.read_text(encoding="utf-8"), end="")
        return 0
    if args.command == "all":
        msg = _missing_conti(args.conti)
        if msg:
            print(msg, file=sys.stderr)
            return 2
        from .animation import prepare_animation_shots
        from .assets import generate_images, generate_tts

        settings_path = ensure_settings_file(args.conti)
        settings = load_project_settings(args.conti)
        apply_project_settings(args.conti)
        out_dir = args.out or setting_str(settings, "PROJECT_OUT", DEFAULT_PROJECT) or DEFAULT_PROJECT
        project = parse_conti(args.conti)
        result = write_project(project, Path(out_dir), materialize_assets=True)
        if _seed_cards(args.conti, out_dir):
            print("✓ cards → CARDS.json (CONTI.md 옆 파일을 복사)")
        errors = [item for item in result["violations"] if item["severity"] == "error"]
        if errors:
            print(f"validation_errors={len(errors)} — 콘티를 고친 뒤 다시 실행하세요.", file=sys.stderr)
            return 1
        print(f"✓ settings → {settings_path}")
        print(f"✓ conti → {out_dir}")
        tts_summary = generate_tts(out_dir)
        tts_failed = tts_summary.get("failed") or []
        if tts_failed and not args.allow_partial:
            ids = ", ".join(item.get("beat_id", "?") for item in tts_failed)
            print(
                f"tts 실패 {len(tts_failed)}건 ({ids}) — 오디오가 빠진 영상이 만들어지지 않도록 중단합니다. "
                f"원인을 해결한 뒤 다시 실행하거나, 그래도 계속하려면 --allow-partial 을 붙이세요.",
                file=sys.stderr,
            )
            return 1
        print("✓ tts" + (f" (실패 {len(tts_failed)}건 — --allow-partial 로 계속)" if tts_failed else ""))
        images_summary = generate_images(out_dir, n=args.n)
        images_failed = images_summary.get("failed") or []
        if images_failed and not args.allow_partial:
            ids = ", ".join(item.get("shot_id", "?") for item in images_failed)
            print(
                f"images 실패 {len(images_failed)}건 ({ids}) — 이미지가 빠진 영상이 만들어지지 않도록 중단합니다. "
                f"원인을 해결한 뒤 다시 실행하거나, 그래도 계속하려면 --allow-partial 을 붙이세요.",
                file=sys.stderr,
            )
            return 1
        print("✓ images" + (f" (실패 {len(images_failed)}건 — --allow-partial 로 계속)" if images_failed else ""))
        animation_summary = prepare_animation_shots(out_dir)
        if animation_summary["total"]:
            print(f"✓ animate specs → {animation_summary['done']}/{animation_summary['total']} clips ready")
        if animation_summary["pending"]:
            print("animation clips are pending. Render them, then run `weft animate --check` and `weft ffmpeg`.", file=sys.stderr)
            return 1
        run_ffmpeg = not args.no_ffmpeg and (args.ffmpeg or setting_bool(settings, "EXPORT_FFMPEG", True))
        if run_ffmpeg:
            from .exporters.ffmpeg_render import render_ffmpeg

            try:
                ffmpeg_summary = render_ffmpeg(
                    out_dir,
                    output=args.ffmpeg_output or setting_str(settings, "FFMPEG_OUTPUT"),
                    encoder=args.ffmpeg_encoder or setting_str(settings, "FFMPEG_ENCODER", "auto") or "auto",
                    width=args.ffmpeg_width if args.ffmpeg_width is not None else (setting_int(settings, "FFMPEG_WIDTH", 1920) or 1920),
                    height=args.ffmpeg_height if args.ffmpeg_height is not None else (setting_int(settings, "FFMPEG_HEIGHT", 1080) or 1080),
                    preset=args.ffmpeg_preset or setting_str(settings, "FFMPEG_PRESET", "veryfast") or "veryfast",
                    crf=args.ffmpeg_crf if args.ffmpeg_crf is not None else (setting_int(settings, "FFMPEG_CRF", 20) or 20),
                    bitrate=args.ffmpeg_bitrate or setting_str(settings, "FFMPEG_BITRATE", "8M") or "8M",
                    with_motion=not setting_bool(settings, "FFMPEG_NO_MOTION"),
                    with_audio=not setting_bool(settings, "FFMPEG_NO_AUDIO"),
                    with_subtitles=not (args.no_subtitles or setting_bool(settings, "FFMPEG_NO_SUBTITLES")),
                )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(f"✓ ffmpeg → {ffmpeg_summary['output']}")
        if args.capcut or setting_bool(settings, "EXPORT_CAPCUT"):
            from .exporters.capcut_draft import build_capcut_draft

            folder = args.folder or setting_str(settings, "CAPCUT_FOLDER") or _default_capcut_folder(out_dir)
            no_register = args.no_register or setting_bool(settings, "CAPCUT_NO_REGISTER")
            no_motion = setting_bool(settings, "CAPCUT_NO_MOTION")
            no_audio = setting_bool(settings, "CAPCUT_NO_AUDIO")
            register, running = _capcut_registration(no_register)
            build_capcut_draft(out_dir, folder_name=folder, register=register, with_motion=not no_motion, with_audio=not no_audio)
            if running and not no_register:
                print(f"✓ capcut → {folder} (CapCut 실행 중이라 등록 생략)")
            else:
                print(f"✓ capcut → {folder}")
        if args.fcpxml or setting_bool(settings, "EXPORT_FCPXML"):
            from .exporters.fcpxml import export_fcpxml

            try:
                fcpxml_summary = export_fcpxml(
                    out_dir,
                    output=args.fcpxml_output or setting_str(settings, "FCPXML_OUTPUT"),
                )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(f"✓ fcpxml → {fcpxml_summary['output']}")
        if args.no_ffmpeg and setting_str(settings, "EXPORT_FFMPEG") and setting_bool(settings, "EXPORT_FFMPEG", True):
            # 설정 파일에 EXPORT_FFMPEG 가 명시적으로 true 일 때만 안내한다.
            print("EXPORT_FFMPEG is ignored because --no-ffmpeg was passed.")
        print("후보를 직접 고르려면:  weft pick")
        return 0
    if args.command == "whereisskill":
        skills = _skill_paths()
        if args.json:
            print(json.dumps({"skills": skills}, ensure_ascii=False, indent=2))
        else:
            for name, paths in skills.items():
                print(f"{name}:")
                for agent in ("claude", "codex"):
                    if agent in paths:
                        print(f"  {agent.capitalize():6}: {paths[agent]}")
            if skills:
                print(
                    "Ask the AI to read the SKILL.md it needs. Start with script-to-conti "
                    "(script -> CONTI.md); conti-qa / visual-qa / animation-render cover "
                    "QA and animation-shot rendering."
                )
        if not skills:
            print(
                "SKILL.md 파일을 찾지 못했습니다. pip 로 설치한 weft 에는 스킬 파일이 포함되지 않습니다 — "
                "Weft 소스 저장소를 받아 .claude/skills/ 아래의 SKILL.md 를 사용하세요.",
                file=sys.stderr,
            )
            return 1
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
