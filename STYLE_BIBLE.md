# Weft 스타일 바이블 — 인사이트 채널 v1

> 2026-06-05 · B1=인사이트 채널, B2=아래 4축으로 확정. 모든 이미지 프롬프트 끝에 `Style:`로 상속(script-to-conti/scaffold가 자동 부착).

## 정한 4축 (사용자 선택)

| 축 | 선택 |
|---|---|
| 그림체 | **3Blue1Brown식 깔끔한 도식** (정확·미니멀, 매끈한 벡터) |
| 팔레트·무드 | **라이트 + 웜** (밝고 친근, 신뢰감) |
| 인물 | **인물 없음** — 개념·사물·도식·은유 중심 |
| 도식 vs 은유 | **반반** (정확한 도식 + 개념 은유) |

> 조합 의도: 3b1b의 **도식적 명료함**을 어두운 톤이 아니라 **밝고 따뜻한 룩**에 얹어,
> 비전공자에게 부담 없으면서(웜·라이트) 정확하고 신뢰감 있는(클린 다이어그램) 인사이트 영상 룩.

## 스타일 바이블 문자열 (영어 — 프롬프트 상속용, 이걸 그대로 사용)

```
Style: clean diagrammatic explainer illustration with precise educational-diagram clarity
(3Blue1Brown-like structure); warm light palette — soft cream / off-white background with
warm amber, terracotta, and muted teal accents; smooth vector shapes, consistent thin-to-medium
line weight, generous negative space; balanced mix of accurate schematic diagrams and conceptual
metaphor imagery; no human figures (concepts, objects, and metaphors only); soft ambient shading,
gentle depth; friendly yet intellectually credible mood; 16:9; no text inside generated images.
```

## 규칙

- **생성 이미지엔 텍스트 금지.** 인용구·핵심어 타이포는 별도 **text_card**(`❝`)로 디자인한다.
- 16:9 와이드. 컷별 프롬프트엔 그 컷 고유의 피사체·구도·도식만 쓰고, 위 `Style:`은 자동 부착.
- 도식 컷은 **홀드**(한 그림 여러 설명)에, 은유/사물 나열은 **몽타주**에 잘 맞음 — 콘티에서 의도적으로 배치.
- 일관성 체크: 영상 한 편의 60~80장이 같은 팔레트·라인웨이트·무드로 보이는지.

## 변경 이력

- v1 (2026-06-05): 최초 확정. 채널 톤은 "깊이 파고들되 차분·명료" 결, 시각은 위와 같이 밝고 도식적.
