# STYLE.txt 예시 모음

[`../STYLE_GUIDE.md`](../STYLE_GUIDE.md) 의 스타일들을 **바로 복사해 쓸 수 있는 `STYLE.txt` 파일**로 모아둔 곳입니다.

쓰는 법 — 마음에 드는 걸 프로젝트 폴더에 `STYLE.txt`로 복사한 뒤 이미지만 다시 생성:

```bash
cp STYLE_GUIDE_assets/soft-clay.STYLE.txt my-video/STYLE.txt
cd my-video && weft images
```

| 파일 | 느낌 |
|---|---|
| `3blue1brown.STYLE.txt` | 기본값 — 깔끔한 도식 + 웜 라이트 (코드의 `DEFAULT_STYLE`과 동일) |
| `soft-clay.STYLE.txt` | 미니멀 소프트 3D 클레이 |
| `papercut.STYLE.txt` | 페이퍼컷 콜라주 |
| `watercolor.STYLE.txt` | 수채 그림책 |
| `risograph.STYLE.txt` | 레트로 리소그래프 |
| `blueprint.STYLE.txt` | 블루프린트 / 테크니컬 |

> 각 파일은 **순수 `Style:` 문자열 한 줄**뿐입니다. 이 내용은 통째로 모든 이미지 프롬프트에 붙으므로, 파일 안에 주석이나 설명을 넣지 마세요. 4축 설명·예시 이미지·직접 만드는 법은 [`../STYLE_GUIDE.md`](../STYLE_GUIDE.md) 참고.

`cat_diagram_3b1b.png` / `cat_clay_soft3d.png` 는 가이드에서 쓰는 비교용 예시 이미지입니다.
