#!/usr/bin/env bash
# Weft 제거 — install.sh 가 만든 것을 깔끔히 되돌린다.
#   ./uninstall.sh
# 지움:  ~/.local/bin/weft 심볼릭 링크 · repo 의 .venv · *.egg-info(빌드 산물)
# 남김:  .env(키 보존) · 소스 폴더 · ~/.zshrc(안내만)
set -euo pipefail
cd "$(dirname "$0")"
REPO="$(pwd)"
BIN="$HOME/.local/bin"

echo "▶ Weft 제거  (repo: $REPO)"

# 1) ~/.local/bin/weft 심볼릭 링크 — 이 repo 를 가리킬 때만 제거
if [ -L "$BIN/weft" ]; then
  target="$(readlink "$BIN/weft")"
  if [ "$target" = "$REPO/.venv/bin/weft" ]; then
    rm -f "$BIN/weft"
    echo "✓ 심볼릭 링크 제거: $BIN/weft"
  else
    echo "⚠ $BIN/weft 가 다른 곳을 가리킴 ($target) — 건드리지 않음"
  fi
elif [ -e "$BIN/weft" ]; then
  echo "⚠ $BIN/weft 가 심볼릭 링크가 아님 — 건드리지 않음"
else
  echo "· $BIN/weft 없음 (건너뜀)"
fi

# 2) .venv 제거
if [ -d .venv ]; then
  rm -rf .venv
  echo "✓ .venv 제거"
else
  echo "· .venv 없음 (건너뜀)"
fi

# 3) 빌드 산물 *.egg-info 제거
removed_egg=0
for d in *.egg-info; do
  [ -e "$d" ] || continue
  rm -rf "$d"
  removed_egg=1
  echo "✓ $d 제거"
done
[ "$removed_egg" = 0 ] && echo "· *.egg-info 없음 (건너뜀)" || true

echo ""
echo "완료. weft 명령이 사라졌습니다.  (새 터미널에서 확인:  command -v weft)"

# 자동으로 건드리지 않는 것들 — 안내만
if [ -f .env ]; then
  echo ""
  echo "· .env 는 키 보존을 위해 남겨뒀습니다. 완전히 지우려면:  rm \"$REPO/.env\""
fi
if grep -qs '^# weft$' "$HOME/.zshrc" 2>/dev/null; then
  echo "· 설치 때 ~/.zshrc 에 PATH 줄을 추가했었습니다. 다른 도구도 ~/.local/bin 을 쓸 수 있어"
  echo "  자동으로 지우지 않습니다. 필요하면 아래 두 줄을 직접 지우세요 (~/.zshrc):"
  echo "      # weft"
  echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
echo "· 소스 폴더는 그대로입니다. 완전히 없애려면 이 폴더째 삭제하세요."
