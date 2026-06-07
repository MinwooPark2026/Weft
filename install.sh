#!/usr/bin/env bash
# Weft 설치 — 전역 `weft` 명령을 만든다 (최초 1회).
#   ./install.sh
# 이후 어느 폴더에서든:
#   cd <CONTI.md 가 있는 프로젝트 폴더>
#   weft conti && weft tts && weft images && weft pick && weft capcut
set -euo pipefail
cd "$(dirname "$0")"
REPO="$(pwd)"
BIN="$HOME/.local/bin"

echo "▶ Weft 설치  (repo: $REPO)"

# 1) 가상환경 + editable 설치 (uv 있으면 uv, 없으면 python venv)
#    editable 이어야 repo 의 .env 를 어느 폴더에서 실행하든 찾는다.
if command -v uv >/dev/null 2>&1; then
  uv venv .venv
  uv pip install --python .venv/bin/python -e .
else
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -e .
fi

# 2) ~/.local/bin 에 weft 심볼릭 링크 (어느 폴더에서든 호출 가능)
mkdir -p "$BIN"
ln -sf "$REPO/.venv/bin/weft" "$BIN/weft"
echo "✓ weft → $BIN/weft"

# 3) .env 시드
if [ ! -f .env ]; then
  cp .env.example .env
  echo "✓ .env 생성됨 — 키를 채우세요: $REPO/.env"
fi

# 4) PATH 확인 (~/.local/bin)
case ":$PATH:" in
  *":$BIN:"*) : ;;
  *)
    echo "⚠ $BIN 이 PATH 에 없습니다 → ~/.zshrc 에 추가합니다."
    printf '\n# weft\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zshrc"
    echo "  새 터미널을 열거나  source ~/.zshrc  실행 후 사용하세요."
    ;;
esac

echo ""
echo "완료. 다음 순서로 쓰세요:"
echo "  1) $REPO/.env 에 OPENAI_API_KEY / TYPECAST_API_KEY 채우기"
echo "  2) cd <프로젝트 폴더>            # CONTI.md 가 있는 곳"
echo "  3) weft conti && weft tts && weft images && weft pick && weft capcut"
