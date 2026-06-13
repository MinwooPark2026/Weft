---
name: visual-qa
description: >-
  Weft 무인 파이프라인의 품질 게이트 — 비전으로 생성 이미지 후보(SHOTS/*/images/gen/candidate_*.png)와
  최종 MP4(EXPORTS/weft_render.mp4)를 직접 보고 검수한다. 글자 누출·스타일 일탈·프롬프트 불일치·구조 오류·
  변주 실패를 잡아 PICKS.json 선택을 직접 고치고, 필요한 shot만 수정 프롬프트로 재생성한다. 다음 상황에 사용:
  "이미지 검수", "후보 골라줘", "렌더 확인", weft images 또는 weft ffmpeg 실행 직후 품질 확인을 요청할 때.
---

# visual-qa — 생성 이미지·렌더 비전 검수

`weft images` 는 shot별 이미지 후보를 만들 뿐 품질은 모른다 — **auto-pick은 무조건
candidate_001**이다. `weft ffmpeg` 도 받은 그대로 렌더한다. 무인 실행에서는 품질 신호가
0이므로, 이 스킬이 **이미지를 Read로 직접 보고** 품질 게이트 역할을 한다.

- **`weft pick`(사람용 picker)과의 역할 구분**: picker는 사람이 브라우저에서 눈으로 고르는
  UI다. 이 스킬은 같은 일을 에이전트가 한다 — 후보 PNG를 직접 보고, picker가 저장하는 것과
  **동일한 스키마·의미**로 PICKS.json을 수정한다. `weft pick` 서버를 띄우지 않는다.
- 두 모드: **A 이미지 후보 검수**(`weft images` 후) / **B 렌더 검수**(`weft ffmpeg` 후).
  요청이 모호하면 산출물 존재로 판단한다(MP4가 있으면 B까지).

## 파일 지도 (`<project>` = 보통 `generated_project/`)

| 경로 | 내용 |
|------|------|
| `<project>/VISUALS.json` | shot 목록·프롬프트의 **정본**. `shots[].prompt` 가 생성 입력 |
| `<project>/PICKS.json` | shot별 선택. `selections` / `auto_picked` / `overridden` |
| `<project>/SHOTS/<id>/images/gen/candidate_*.png` | 생성 후보 (프로바이더 무관 중립 경로) |
| `<project>/SHOTS/<id>/images/gen/.key` | 캐시 키 사이드카(프롬프트+스타일 해시) |
| `<project>/SHOTS/<id>/PROMPT.md`, `SHOT.json` | 사람이 보는 사이드카(정본 아님) |
| `STYLE.txt` (프로젝트 루트 또는 `<project>/`) | 전 shot 공통 `Style:` 접미사 — 일탈 판정 기준 |
| `<project>/EXPORTS/render_plan.json` | video/audio/subtitles 이벤트 타임라인 |
| `<project>/EXPORTS/weft_render.mp4` | 최종 렌더 (기본 출력 경로) |

> `selections` 값이 `images/dryrun/candidate_001.svg` 면 아직 실이미지 생성 전(placeholder)
> 이다 — 검수 대상이 아니므로 "`weft images` 먼저"라고 보고하고 끝낸다.
> 구버전 프로젝트는 후보가 `images/openai/` 에 있을 수 있다(legacy 폴백 — 읽기는 양쪽 다
> 유효, 신규 생성만 `images/gen/`). 검수할 땐 두 경로를 모두 확인한다.

## 모드 A — 이미지 후보 검수 (`weft images` 후)

### A-1. 로드

1. `<project>/VISUALS.json` 에서 `source_kind == "image"` 인 shot의 id·prompt·cover를 모은다.
2. `STYLE.txt` 를 읽어 스타일 기준(팔레트·그림체·"no text" 규칙)을 잡는다.
3. shot마다 `SHOTS/<id>/images/gen/candidate_*.png` 를 **Read로 직접 본다**
   (파일명·용량으로 추측 금지). `PICKS.json` 의 현재 선택도 함께 확인.

### A-2. 검사 항목 (우선순위순)

| 심각도 | 항목 | 판정 기준 |
|--------|------|-----------|
| 치명 | **글자/문자 누출** | 이미지 안에 글자·숫자·라벨·유사문자(글자처럼 보이는 획) — 스타일 바이블 최우선 위반. 모든 후보에서 누출이면 재생성 |
| 치명 | **프롬프트 불일치** | 프롬프트가 요구한 핵심 피사체·구도가 화면에 없음 (나레이션과 어긋나는 그림) |
| 치명 | **구조적 오류** | 왜곡된 손·얼굴·신체, 무너진 기하(불가능한 원근·붙어버린 사물) |
| 경고 | **스타일 일탈** | STYLE.txt와 팔레트·그림체·질감·무드가 다름. 한 컷만 톤이 튀면 전체 일관성이 깨진다 |
| 경고 | **후보 간 사실상 동일** | 후보 N장이 변주 없이 거의 같음 — 고를 가치가 없으므로 메모, 심하면 프롬프트 구체화 후 재생성 |

### A-3. shot이 많을 때 우선순위

- **글자를 부르기 쉬운 프롬프트 먼저 전수 검사**: 도식·차트·그래프·타임라인·라벨·연도·
  순위·UI류 ("diagram", "chart", "graph", "timeline" 등이 프롬프트에 있는 shot).
- 나머지는 **샘플링**(예: 3~4개당 1 shot + 첫/마지막 shot). 샘플에서 스타일 일탈·구조 오류가
  나오면 **전수로 확대**한다.

### A-4. 판정과 액션

shot마다 둘 중 하나로 판정한다.

#### 합격(추천 후보 N) → PICKS.json 직접 수정

picker의 저장 로직과 동일하게 **세 가지를 반드시 함께** 고친다(코드: `picker/server.py::_save_pick`):

```json
{
  "schema": "weft-picks-v1",
  "selections": { "s07_yearly_trend": "images/gen/candidate_002.png" },
  "auto_picked": ["..."],          // ← 여기서 s07_yearly_trend 제거
  "overridden": ["s07_yearly_trend"]  // ← 여기에 추가
}
```

- `selections` 값은 **`SHOTS/<shot_id>/` 기준 상대경로** `images/gen/candidate_NNN.png`
  (외부 투입 이미지는 `images/gen/external_NNN.png`; 구 프로젝트의 `images/openai/...` 선택은
  그대로 유효 — 파일이 실제 있는 쪽 경로를 쓴다).
- **`overridden` 추가를 빼먹으면** 다음 `weft images` 실행이 선택을 candidate_001로 되돌린다
  (auto-pick 로직이 overridden 아닌 shot만 덮어씀).
- 수정 후 EXPORTS(render_plan·SRT)에 반영하려면 재컴파일이 필요하다:
  - 기본: `weft images <project>` 재실행 — 전 shot 캐시 일치 상태면 **API 호출 0회**로
    끝에 재컴파일만 수행. 단, 프롬프트를 고쳐둔 shot이 있으면 그 shot이 재생성(과금)되니
    픽 반영과 프롬프트 수정을 한 실행에 섞지 말 것.
  - 과금 위험 0의 순수 재컴파일: `.venv/bin/python -c "from weft.assets import recompile_exports; recompile_exports('generated_project')"`

#### 재생성 권장 → 수정 프롬프트 제시 + 정확한 절차

캐시 동작(코드: `assets.py::generate_images`): 캐시 키 = `(프롬프트 + "\n\n" + STYLE.txt)` 의
해시로 `SHOTS/<id>/images/gen/.key` 에 저장된다. **VISUALS.json에서 그 shot의 prompt만
고치면 그 shot만 키 불일치 → 그 shot만 재생성**되고 나머지는 cache(과금 0)로 통과한다.

1. **수정 프롬프트 작성** — 영어, 피사체/구도만(스타일 언급 금지, STYLE.txt가 자동으로 붙음).
   글자 누출 shot은 글자를 부르는 표현을 순수 시각으로 바꾸고 끝에
   "absolutely no text, letters, numbers, or labels" 를 명시. 글자 자체가 내용인 shot은
   재생성 대신 **`❝` 텍스트카드로 빼야 한다**고 사용자에게 보고(콘티 수정 사항).
2. `<project>/VISUALS.json` 의 해당 `shots[].prompt` 를 교체한다. (선택) `SHOTS/<id>/PROMPT.md`
   와 `SHOT.json` 의 `prompt` 도 맞춰 둔다 — 생성은 VISUALS.json만 읽지만 사이드카가 어긋난다.
3. 재생성 실행:
   ```bash
   weft images <project> --shots s07_yearly_trend,s12_oldpaper
   ```
   - 해당 shot의 기존 `candidate_*.png` 는 **전부 삭제 후 새로 생성**된다(append 아님,
     `external_*.png` 는 보존). 선택은 candidate_001로 초기화.
   - **overridden 보호**: 사람이(또는 이 스킬이) 이미 고른 shot은 재생성이 자동으로
     건너뛰어진다(`"picker에서 직접 선택한 후보가 있어 재생성 건너뜀 (--force 로 재생성)"`,
     summary의 `protected`). 정말 다시 만들려면 `--force` — 이때 고른 파일이 삭제되고
     선택이 candidate_001로 리셋되며 overridden에서도 빠진다.
   - `--force` 는 캐시·보호를 모두 무시하는 강제 재생성(전 대상 과금)이다. 평소엔 불필요 —
     프롬프트 수정만으로 해당 shot만 재생성된다.
   - **STYLE.txt를 고치면 모든 shot의 키가 불일치 → 전체 재생성(전체 과금)**. 반드시 사전 확인.
4. 새 후보를 다시 Read로 검수 → 합격 절차로 픽.

**순서 규칙**: 재생성이 픽을 리셋하므로 **재생성을 먼저 끝내고, 픽(PICKS.json 수정)은 마지막에**.

(보조 경로) 기존 후보를 지우지 않고 추가 생성하려면 picker의 `+생성`과 같은 함수를 쓴다:
```bash
.venv/bin/python -c "from weft.assets import append_candidates; print(append_candidates('generated_project', 's07_yearly_trend', n=2, prompt='...'))"
```
candidate_003… 으로 **추가**되고 VISUALS.json·PROMPT.md·`.key` 가 자동 동기화된다.
단 PICKS.json은 건드리지 않으므로 합격 절차로 직접 픽한다.

### A-5. 과금 가드

- 재생성 대상이 **5 shot 이상**이면 실행 전에 멈추고 사용자 확인을 받는다:
  `weft images <project> --shots ... --estimate` 로 **API 호출 없이** 생성 장수
  (shot 수 × 후보 n)를 미리 보여준다. 기본 모델 gpt-image-2 기준 대략 medium·1920×1080 ≈
  장당 $0.05~0.1 (low ≈ $0.01, high ≈ $0.2+; 해상도에 비례 — 정확 단가는 OpenAI 가격표)으로
  예상 비용을 언급한다.
- 5 미만이어도 `--force` 전체 재생성·STYLE.txt 변경은 항상 사전 확인.

## 모드 B — 렌더 검수 (`weft ffmpeg` 후)

### B-1. 대표 프레임 추출

1. `<project>/EXPORTS/render_plan.json` 을 읽는다.
   - `video[]` 이벤트: `shot_id`, `src`, **`start_seconds` / `end_seconds`**(초), `start_clock`.
   - `subtitles[]` 이벤트: `start` / `end` 는 **샘플 단위** — 초 = `start ÷ sample_rate`
     (최상위 필드, 기본 48000).
2. 프레임은 **반드시 /tmp 임시 폴더**에 추출한다(SHOTS/ 안에 저장 금지):
   ```bash
   QA=/tmp/weft_qa_$(date +%s) && mkdir -p "$QA"
   # video 이벤트별 대표 프레임 = 시작 + 0.5초 (이벤트가 0.5초보다 짧으면 중간점)
   ffmpeg -y -ss <start_seconds+0.5> -i <project>/EXPORTS/weft_render.mp4 -frames:v 1 "$QA/<shot_id>.png"
   ```
3. video 이벤트는 **전수**(이벤트당 1프레임이라 저렴), 자막은 **몇 곳 샘플** — 긴 자막,
   숫자·고유명사 포함, 두 줄 의심 자막의 **구간 중간점**에서 추출. zoom/모션 이벤트는
   의심되면 시작·끝 2프레임으로 과확대·잘림을 본다.
4. 추출한 프레임을 한 장씩 **Read로 확인**한다.

### B-2. 검사 항목

| 심각도 | 항목 | 판정 기준 |
|--------|------|-----------|
| 치명 | 검은 화면/빈 구간 | 이벤트 경계 프레임이 단색·검정 (src 누락, 경계 빈틈) |
| 치명 | 텍스트카드 렌더 깨짐 | `❝` shot에서 글자 깨짐·잘림, 또는 **shot id 문자열이 그대로 화면에** 보임(CARDS.json 문구 누락) |
| 치명 | 자막 겹침·잘림·오타 | 화면 자막을 `EXPORTS/subtitles.srt` 텍스트와 대조 — 두 줄 겹침, 화면 밖 잘림, 깨진 글자 |
| 경고 | 종횡비/레터박스 이상 | 위아래·좌우 검은 띠, 찌그러진 비율(이미지 1536×1024 → 캔버스 1920×1080) |
| 경고 | 이미지-나레이션 불일치 | 그 시각의 자막 텍스트와 화면 피사체가 안 맞음(엉뚱한 그림이 걸린 구간) |

### B-3. 후속 액션 연결

- 문제 프레임의 `shot_id` 로 원인을 추적한다: 후보 자체 문제면 **모드 A로 돌아가** 픽 교체
  또는 재생성, 카드/자막 문제면 CARDS.json·콘티 수정 사항으로 보고. 픽이나 에셋을 고쳤으면
  재컴파일 후 `weft ffmpeg <project>` 재렌더 → 고친 구간만 다시 추출해 확인.

## 출력 형식

모드별 표 + 요약 판정으로 보고한다.

```markdown
### 이미지 후보 검수 (모드 A)
| shot | 판정 | 심각도 | 문제 | 액션 |
|------|------|--------|------|------|
| s07_yearly_trend | 재생성 권장 | 치명 | cand 1·2 모두 축 라벨 글자 누출 | 프롬프트 교체(아래) 후 `weft images --shots s07_yearly_trend` |
| s09_gtx580 | 합격 (후보 2) | - | cand 1은 기하 왜곡 | PICKS.json → candidate_002, overridden 추가 |

### 렌더 검수 (모드 B)
| 시각 | shot/beat | 심각도 | 문제 | 액션 |
|------|-----------|--------|------|------|
| 04:51 | s27_number_card | 치명 | 카드에 shot id 문자열 노출 | CARDS.json에 문구 추가 후 재렌더 |

**요약 판정**: ✅ 배포 가능 / ⚠️ 조건부(픽 교체만으로 해결, 재생성 불필요) / ❌ 재작업 필요
(재생성 N shot — 예상 약 M장·$X, 사용자 확인 대기)
```

재생성 권장 shot에는 **수정 프롬프트 전문**(영어)을 함께 제시한다.

## 주의사항

- 판정은 반드시 **Read로 본 화면**에 근거한다. 파일명·로그·추측으로 판정하지 않는다.
- 재생성은 곧 **API 과금**이다 — A-5 가드를 지키고, 실행한 명령과 생성 장수를 보고에 남긴다.
- stub provider 산출물(`provider=stub`, 베이지 배경에 "stub candidate" 글자)은 품질 판정
  대상이 아니다 — 파이프라인 절차 확인용임을 보고하고 끝낸다.
- `weft pick` 은 사람용이므로 띄우지 않는다. API 키 등 secret 값은 출력하지 않는다.
- /tmp 임시 폴더 밖(특히 `SHOTS/`, `EXPORTS/`)에 검수용 파일을 만들지 않는다.
