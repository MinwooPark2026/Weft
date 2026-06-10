---
name: animation-render
description: >-
  Weft 콘티의 애니메이션 shot(`source_kind=remotion`·`hyperframe`)을 실제 MP4로 렌더한다.
  `weft animate`가 만든 `SHOTS/<shot>/animation/SPEC.md`를 읽어 Remotion(React) 또는
  HTML/CSS 애니메이션을 작성하고, 결정적(프레임 정확) 렌더로
  `SHOTS/<shot>/rendered/clip.mp4`를 만든 뒤 `weft animate --check`와 ffprobe로 검증한다.
  다음 상황에 사용: "애니메이션 샷 렌더", "weft animate 후" 렌더가 필요할 때,
  "SPEC.md를 clip.mp4로" 만들어 달라고 할 때.
---

# animation-render — SPEC.md를 clip.mp4로

`weft animate`는 애니메이션 shot마다 SPEC.md를 만들고 출력 MP4를 **기다리기만** 한다.
실제 렌더는 AI agent의 몫 — 이 스킬이 그 렌더를 수행한다. 결과물은 `weft ffmpeg`/
`weft capcut`/`weft fcpxml`에 일반 clip처럼 들어간다.

## 1. 렌더 대상 파악

프로젝트 루트(CONTI.md 있는 곳)에서:

```bash
weft animate            # SPEC.md 생성/갱신 + 상태 JSON 출력 (기본 project dir: generated_project)
```

출력 JSON의 `pending` 배열이 렌더할 shot 목록이다(`shot_id`, `source_kind`,
`duration_seconds`, `spec`, `output`). 각 shot의 `animation/SPEC.md` 실제 필드:

- `source_kind`: `remotion` 또는 `hyperframe`
- `cover`: 이 clip이 덮는 비트 범위 (예: `b001~b003`)
- `duration_seconds`: 정확한 목표 길이(소수 3자리) — render_plan에서 역산(TTS 후엔 실측 WAV 길이, 전엔 추정치)
- `output`: `SHOTS/<shot>/rendered/clip.mp4` — **생성 프로젝트(generated_project) 기준 상대 경로**
- `## Prompt`: 콘티 shot 표의 프롬프트(구현할 내용)

**fps와 해상도는 SPEC.md에 없다.** fps는 `generated_project/EXPORTS/render_plan.json`의
`fps`(기본 30), 해상도는 캔버스 1920×1080(16:9)에 맞춘다.
**목표 프레임 수 = round(duration_seconds × fps)** (예: 5.833s × 30 = 175프레임).

> ⚠️ **`duration_seconds`가 0.000이면 렌더하지 마라.** 그 shot이 render_plan에 없다는
> 뜻이다(어느 비트도 덮지 않거나 콘티 cover 문제). 콘티 문제로 사용자에게 보고하고
> `weft conti` 검증부터 다시 한다. TTS 전에는 추정 길이이므로, 가능하면 `weft tts` 후에 렌더한다.

## 2. remotion 경로

SPEC의 Engine Guidance대로 **`useCurrentFrame()`/`interpolate()` 기반** — CSS
transition/animation 금지(프레임 단위 렌더에서 동작하지 않음). **사용 가능하면
`remotion-best-practices` 스킬을 함께 참조해 컴포넌트를 작성한다.**

1. 프로젝트 셋업: `SHOTS/<shot>/animation/` 아래(또는 임시 폴더)에 Remotion 프로젝트를
   만든다. `npx create-video@latest --blank` 또는 최소 수동 셋업(`remotion`,
   `@remotion/cli`, `react`, `react-dom`).
2. Composition을 SPEC과 **정확히** 일치시킨다:
   ```tsx
   <Composition id="Shot" component={Scene} durationInFrames={175} fps={30} width={1920} height={1080} />
   ```
   `durationInFrames`는 위에서 계산한 목표 프레임 수 그대로.
3. 렌더:
   ```bash
   npx remotion render src/index.ts Shot ../rendered/clip.mp4 --codec h264
   ```
   (출력 경로가 SPEC의 `output`과 정확히 일치하도록 — 필요하면 렌더 후 복사.)

**콘텐츠 원칙**: SPEC의 Prompt와 콘티의 모션 의도를 구현한다. 색감·분위기는 프로젝트
`STYLE.txt`(없으면 `generated_project/STYLE.txt`)를 따라 정적 이미지 shot들과 한 영상처럼
보이게 한다. **화면 하단 1/5에는 핵심 요소를 두지 마라** — 자막이 그 위에 따로 입혀진다.
나레이션 오디오는 넣지 않는다(무음 또는 오디오 트랙 없음).

## 3. hyperframe 경로

HyperFrame shot = **HTML/CSS 애니메이션을 MP4로 렌더**한 것(animation.py의 정의).
HTML 한 파일(`SHOTS/<shot>/animation/scene.html`)로 작성하되, 핵심은 **결정적 렌더**:
실시간 화면 녹화는 프레임 드랍·타이밍 오차 때문에 **금지**. 애니메이션 시간을
`현재시간 = frame / fps`로 외부에서 구동한다.

1. `scene.html`에 쿼리 파라미터로 시간을 고정하는 훅을 넣는다:
   ```html
   <script>
     const t = parseFloat(new URLSearchParams(location.search).get("t") || "0");
     // CSS animation/transition을 t초 시점에 정지
     document.getAnimations().forEach(a => { a.pause(); a.currentTime = t * 1000; });
     // JS 구동 애니메이션이면: renderAt(t) 처럼 t를 받아 그리는 순수 함수로 작성
   </script>
   ```
2. Playwright로 프레임별 스크린샷(없으면 `pip install playwright && playwright install chromium`,
   대안: puppeteer/`npx playwright`):
   ```python
   from playwright.sync_api import sync_playwright
   FPS, FRAMES = 30, 175  # round(duration_seconds * fps)
   with sync_playwright() as p:
       page = p.chromium.launch().new_page(viewport={"width": 1920, "height": 1080})
       for n in range(FRAMES):
           page.goto(f"file:///abs/path/scene.html?t={n / FPS}")
           page.screenshot(path=f"frames/{n:05d}.png")
   ```
3. ffmpeg로 인코딩:
   ```bash
   ffmpeg -y -framerate 30 -i frames/%05d.png -frames:v 175 \
     -an -c:v libx264 -pix_fmt yuv420p SHOTS/<shot>/rendered/clip.mp4
   ```

콘텐츠 원칙은 remotion 경로와 동일(STYLE.txt 색감, 하단 1/5 비우기, 무음).

## 4. 출력 규격

- 경로: 정확히 `SHOTS/<shot>/rendered/clip.mp4` (generated_project 기준 — SPEC의 `output` 그대로)
- 1920×1080, render_plan의 `fps`, `yuv420p`, 오디오 없음
- **duration을 SPEC와 프레임 단위로 일치**: exporter가 약간의 오차는 보정하지만
  (짧으면 마지막 프레임 복제, 길면 트림) 정확히 맞춰 내는 게 원칙이다. 길이가 어긋난
  중간 산출물은 한 번에 보정할 수 있다:
  ```bash
  # FRAMES = round(duration_seconds * fps). 짧으면 마지막 프레임 복제로 패딩, 길면 트림.
  ffmpeg -y -i raw.mp4 -vf "fps=30,tpad=stop_mode=clone:stop_duration=5,trim=end_frame=175,setpts=PTS-STARTPTS" \
    -an -c:v libx264 -pix_fmt yuv420p SHOTS/<shot>/rendered/clip.mp4
  ```
- 16:9가 아니면 exporter가 검은 레터박스로 fit하므로 반드시 16:9로 낸다.

## 5. 검증 루프

```bash
weft animate --check --no-recompile   # pending이 비고 exit 0 이어야 함
ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height,r_frame_rate,nb_frames,duration \
  -of default=noprint_wrappers=1 generated_project/SHOTS/<shot>/rendered/clip.mp4
```

- `weft animate --check`는 **파일 존재만** 본다. 길이·해상도는 ffprobe로 직접 대조:
  `nb_frames` = 목표 프레임 수, `width×height` = 1920×1080, `r_frame_rate` = render_plan fps.
- 어긋나면 §4의 보정 명령으로 고치고 다시 검사한다.
- 모두 통과하면 끝 — 이후 `weft ffmpeg`(또는 `weft capcut`/`weft fcpxml`)가 이 clip을
  타임라인에 자동 포함한다. SPEC.md를 다시 쓰려면 `weft animate --refresh-specs`.
