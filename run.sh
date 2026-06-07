#!/usr/bin/env bash
# Weft 파이프라인 러너 — 대본/콘티 → 캡컷 드래프트
#
# 사용법:  ./run.sh <command> [project]
#   setup            venv 생성 + 의존성 설치 (최초 1회)
#   conti  [proj]    콘티 → 정본 JSON / 검증 / 내보내기   (※ generated_project 새로 만듦)
#   tts    [proj]    나레이션 TTS (Typecast)              (※ API 비용)
#   images [proj]    이미지 생성 (gpt-image-1, 후보 N장)   (※ API 비용)
#   pick   [proj]    후보 선택 picker (브라우저, 왼손 단축키)
#   capcut [proj]    CapCut 드래프트 빌드 (CapCut 종료 상태에서)
#   all    [proj]    conti→tts→images→capcut 한 번에
#
# 변수로 조정:  PROJECT(폴더), N(이미지 후보수), FOLDER(draft명), CONTI/OUT(경로)
#   예) N=3 ./run.sh images          /  PROJECT=ep3 ./run.sh all  /  ./run.sh all ep3
#
# 주의: conti / all 은 generated_project 를 CONTI.md 에서 다시 만든다 → picker 픽이 초기화됨.
#       이미 pick 으로 골랐다면 capcut 만 다시 실행할 것.

set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/bin/python"

PROJECT="${2:-${PROJECT:-example}}"
CONTI="${CONTI:-$PROJECT/CONTI.md}"
OUT="${OUT:-$PROJECT/generated_project}"
FOLDER="${FOLDER:-weft_$PROJECT}"
N="${N:-2}"

hr(){ printf '\n\033[1;36m▶ %s\033[0m\n' "$1"; }
need_venv(){ [ -x "$PY" ] || { echo "venv 없음 → 먼저: ./run.sh setup"; exit 1; }; }
usage(){ awk 'NR>1{ if(/^#/){sub(/^# ?/,"");print} else exit }' "$0"; }

case "${1:-help}" in
  setup)
    hr "venv 생성 + 의존성 설치"
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip openai Pillow
    echo "✓ 완료. .env 에 OPENAI_API_KEY / TYPECAST_API_KEY 를 채우세요." ;;
  conti)  need_venv; hr "콘티 → 정본 JSON ($PROJECT)"; "$PY" -m weft.cli dryrun "$CONTI" --out "$OUT" ;;
  tts)    need_venv; hr "나레이션 TTS ($PROJECT)";      "$PY" -m weft.cli tts "$OUT" ;;
  images) need_venv; hr "이미지 생성 N=$N ($PROJECT)";  "$PY" -m weft.cli images "$OUT" --n "$N" ;;
  pick)   need_venv; hr "후보 picker ($PROJECT)";       "$PY" -m weft.cli pick "$OUT" ;;
  capcut) need_venv; hr "CapCut 빌드 → $FOLDER";        "$PY" -m weft.cli capcut "$OUT" --folder "$FOLDER" ;;
  all)
    need_venv
    hr "전체 파이프라인 ($PROJECT)   ※ TTS·이미지 API 비용 발생"
    "$PY" -m weft.cli dryrun "$CONTI" --out "$OUT"
    "$PY" -m weft.cli tts "$OUT"
    "$PY" -m weft.cli images "$OUT" --n "$N"
    "$PY" -m weft.cli capcut "$OUT" --folder "$FOLDER"
    echo ""
    echo "✓ 완료. 후보를 직접 고르려면:  ./run.sh pick $PROJECT" ;;
  *) usage ;;
esac
