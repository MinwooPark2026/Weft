# Weft

<p align="left"><a href="#한국어"><b>한국어</b></a> · <a href="#english"><b>English</b></a></p>

---

<a id="한국어"></a>

## 한국어

Weft는 긴 호흡의 설명형 영상을 위한 **이중 트랙(dual-track) 워크플로우**입니다. 입력은 **사용자가 직접 쓴 산문 대본**이고, 주제는 가리지 않습니다 — 역사, 과학, 문화 분석, 사고실험, 철학 비교 등 무엇이든 됩니다 (대본 작성 자체는 Weft 범위 밖).

다음 둘을 분리합니다:

- **나레이션 비트(narration beats)**: 대본, 자막, TTS 타이밍
- **비주얼 샷(visual shots)**: 생성 이미지, 텍스트 카드, 재사용, 몽타주, 모션

이렇게 하면 하나의 비주얼이 여러 나레이션 비트를 덮거나, 하나의 나레이션 비트가 여러 비주얼을 쓸 수 있습니다. 실질적 목표는 "문장마다 이미지 한 장" 식의 편집 노동을 줄이면서, ffmpeg로 **완성 MP4를 기본 산출물**로 뽑는 것입니다. NLE 편집이 필요할 때는 CapCut 드래프트나 FCPXML로 핸드오프합니다.

### 구성 요소

- `install.sh`: 설치 스크립트 (venv 생성, `weft` 명령 설치, `.env` 시드)
- `uninstall.sh`: 제거 스크립트 (`weft` 명령·venv 제거, `.env`는 보존)
- `weft/`: 파이썬 CLI와 핵심 파이프라인
- `.claude/skills/script-to-conti/`: 대본을 Weft 이중 트랙 `CONTI.md`로 바꾸는 스킬
- `.claude/skills/conti-qa/`: 콘티 품질 검토(리듬·프롬프트·카드·사실/추측 lint) 스킬
- `.claude/skills/visual-qa/`: 생성 이미지 후보·최종 MP4를 비전으로 검수하는 스킬
- `.claude/skills/animation-render/`: remotion/hyperframe 샷을 `clip.mp4`로 렌더하는 스킬
- `.agents/skills/`: Codex에서도 같은 스킬들을 쓰기 위한 미러
- `weft/picker/`: 로컬 이미지 후보 선택기(picker)
- `STYLE_GUIDE.md`: 이미지 스타일 커스터마이즈 가이드
- `WORKFLOW.html`: 시각적 워크플로우 개요
- `example/CONTI.md`, `example/SCRIPT.md`: 예시 입력

생성물(오디오·이미지·CapCut 드래프트)은 로컬에서 만들어지며 저장소에 커밋되지 않습니다.

### 설치 (최초 1회)

저장소 안에서 한 번만 실행하세요:

```bash
./install.sh
```

`install.sh`는 venv를 만들고 패키지를 editable로 설치(`weft` 명령 생성)한 뒤 `~/.local/bin`에 심볼릭 링크하고 `.env.example`로부터 `.env`를 시드합니다. 그다음부터 `weft`는 **어느 디렉터리에서나** 동작합니다.

설치 후 저장소의 `.env`에 본인 키를 채우세요:

- TTS용 `TYPECAST_API_KEY`, `TYPECAST_VOICE`
- 이미지 생성용 `OPENAI_API_KEY`

editable 설치라서 weft는 어느 작업 디렉터리에서든 이 `.env`를 찾습니다. 현재 폴더에 둔 프로젝트별 `.env`도 인식됩니다.

### 제거

설치한 것을 깔끔히 되돌리려면 저장소 안에서:

```bash
./uninstall.sh
```

`weft` 명령(심볼릭 링크)·venv·빌드 산물을 지웁니다. 키가 든 `.env`는 **보존**됩니다(완전히 지우려면 안내대로 직접 삭제).

### 대본을 CONTI.md로 바꾸기

직접 쓴 산문 대본(`SCRIPT.md` 등)이 있으면 먼저 AI 어시스턴트에게 **`script-to-conti` 스킬을 사용해서 Weft `CONTI.md`로 변환**하라고 요청하세요. 이 스킬은 한 문장마다 이미지를 하나씩 만들지 않고, `▶` 새 그림·`↓` 홀드·`▦` 몽타주·`↺` 재사용 같은 이중 트랙 지시를 넣어 이미지 수를 줄이는 콘티를 만듭니다.

- Claude용: `.claude/skills/script-to-conti/`
- Codex용: `.agents/skills/script-to-conti/`

터미널에서 스킬 파일 위치를 확인하려면:

```bash
weft whereisskill
```

예시 요청:

```text
이 대본을 script-to-conti 스킬로 Weft CONTI.md로 바꿔줘.
스킬 위치는 `weft whereisskill`로 확인해서 SKILL.md를 읽어줘.
검증은 weft conti까지 돌려서 validation_errors=0이 되게 해줘.
```

### 빠른 시작

프로젝트는 `CONTI.md`가 들어 있는 폴더입니다. 그 폴더로 `cd`한 뒤, 현재 디렉터리를 대상으로 동작하는 하위 명령을 실행하세요(경로 인자 불필요):

```bash
cd my-video       # CONTI.md가 있는 폴더
weft conti        # ./CONTI.md -> ./generated_project/ (파싱 + 검증 + 컴파일)
weft tts          # ./generated_project (Typecast 나레이션; API 비용)
weft images       # ./generated_project (OpenAI gpt-image-1; API 비용)
weft animate      # Remotion/HyperFrame shot spec 생성·렌더된 clip 확인
weft ffmpeg       # ./generated_project -> EXPORTS/weft_render.mp4 (먼저 볼 MP4)
weft pick         # 선택: 이미지 후보를 사람이 고름
weft capcut       # 선택: 마음에 안 들 때 CapCut 드래프트 생성
weft fcpxml       # 선택: FCPXML 핸드오프
```

한 번에:

```bash
weft all          # 사람용 빠른 자동 실행: conti -> tts -> images -> animate check -> ffmpeg
weft all --capcut # MP4에 더해 CapCut 드래프트도 생성
weft all --fcpxml # MP4에 더해 FCPXML도 생성
```

### 프로젝트 명령

`CONTI.md`가 있는 폴더 안에서 실행하세요:

```bash
weft conti            # ./CONTI.md -> JSON 트랙·픽·SRT·렌더 플랜
weft parse            # CONTI.md 파싱 결과 JSON을 출력만 (파일 생성 없음)
weft validate         # CONTI.md 파싱+검증 결과만 출력 (파일 생성 없음)
weft tts              # Typecast 나레이션 WAV
weft images           # OpenAI 이미지 후보
weft pick             # 로컬 브라우저 picker
weft animate          # Remotion/HyperFrame animation shot 준비·검사
weft ffmpeg           # ffmpeg MP4 렌더러 (자막 기본 burn-in)
weft capcut           # 선택: CapCut 드래프트 빌더
weft fcpxml           # 선택: Final Cut/Premiere/Resolve용 FCPXML 핸드오프
weft all              # 사람용 빠른 자동 MP4 렌더
weft all --capcut     # MP4 + CapCut
weft all --fcpxml     # MP4 + FCPXML
weft settings         # WEFT_SETTINGS.txt 생성/확인
weft whereisskill     # AI에게 읽힐 script-to-conti 스킬 경로 출력
```

별칭: `weft dryrun`은 `weft conti`와 같고, `weft render`는 `weft ffmpeg`와 같습니다.

conti의 입력은 기본값이 `./CONTI.md`, 나머지 프로젝트 명령의 프로젝트 디렉터리는 기본값이 `./generated_project`입니다. 파워 유저는 경로를 명시적으로 넘길 수도 있습니다.

`weft capcut`와 `weft all --capcut`은 `--no-register`로 CapCut 목록 등록을 건너뛸 수 있습니다(드래프트 파일만 생성).

### WEFT_SETTINGS.txt

`weft conti` 또는 `weft all`을 실행하면 프로젝트 폴더(`CONTI.md` 옆)에 `WEFT_SETTINGS.txt`가 없을 때 기본 파일을 만듭니다. VASP `INCAR`처럼 이 파일 하나를 다른 프로젝트에 복사하면 같은 렌더/provider 옵션을 재사용할 수 있습니다. CLI로 직접 준 옵션은 이 파일보다 우선합니다. 기본 빠른 산출물은 ffmpeg MP4이고, CapCut/FCPXML은 필요할 때 추가 실행합니다.

바로 쓸 수 있는 예시는 `setting_examples/`에 있습니다:

- `youtube_4k_high.WEFT_SETTINGS.txt`
- `youtube_high.WEFT_SETTINGS.txt`
- `standard.WEFT_SETTINGS.txt`
- `low.WEFT_SETTINGS.txt`

테스트 영상 용량을 줄이는 예:

```env
EXPORT_FFMPEG=true
FFMPEG_ENCODER=libx264
FFMPEG_CRF=32
FFMPEG_PRESET=veryfast
FFMPEG_BITRATE=2M
```

그 뒤:

```bash
weft settings
```

로 현재 프로젝트 설정 파일을 만들거나 확인할 수도 있습니다.

```bash
weft all
```

`weft ffmpeg`는 `EXPORTS/render_plan.json`, 선택 이미지, TTS WAV, 컴파일된 자막 이벤트를 바로 `EXPORTS/weft_render.mp4`로 렌더합니다. 자막은 기본으로 영상에 입혀지며(`--no-subtitles`로 비활성화), `--encoder auto`가 macOS의 `h264_videotoolbox`를 우선 시도하고 실패하면 `libx264`로 fallback합니다. 소프트웨어 인코딩 확인용은 `--preset ultrafast`, 더 작은/고화질 출력은 `--crf` 값 조정으로 맞추세요.

`weft animate`는 `source_kind=remotion` 또는 `source_kind=hyperframe` shot마다 `SHOTS/<shot>/animation/SPEC.md`를 만들고, AI가 렌더해야 할 출력 경로 `SHOTS/<shot>/rendered/clip.mp4`를 검사합니다. 렌더된 MP4는 일반 clip처럼 `weft ffmpeg`, `weft capcut`, `weft fcpxml`에 들어갑니다.

`weft fcpxml`은 같은 `render_plan`에서 편집 가능한 FCPXML을 만듭니다. 이미지/클립/애니메이션 클립은 비디오 레인, TTS는 오디오 레인, 자막은 타이틀 레인으로 들어갑니다.

provider는 `.env`에서 바꿀 수 있습니다:

```env
IMAGE_PROVIDER=openai   # openai | comfyui | stub
TTS_PROVIDER=typecast   # typecast | stub
```

`stub`은 API 키 없이 로컬 테스트용 PNG/WAV를 만듭니다.

`comfyui`는 로컬 ComfyUI 서버(`COMFYUI_URL`, 기본 `http://127.0.0.1:8188`)로 이미지를 생성합니다.
`COMFYUI_WORKFLOW`에는 ComfyUI에서 **Save (API Format)**으로 내보낸 워크플로 JSON 경로를 지정하고,
JSON 안의 긍정 프롬프트 자리에 `__WEFT_PROMPT__`를 넣어 두면 shot 프롬프트로 치환됩니다.
(선택: `__WEFT_SEED__`를 넣으면 후보마다 다른 시드로 치환 — 없으면 `"seed"` 키를 자동으로 바꿔 후보가 서로 다른 변주가 됩니다.)

AI 에이전트 주의: API 키가 `__`로 시작하거나 placeholder처럼 보여도 문자열 모양만으로 무효라고 판단하지 마세요. 키 유효성은 실제 실행 결과로 판단합니다. `TYPECAST_API_KEY`/`TYPECAST_VOICE`가 비어 있거나 `TTS_PROVIDER=stub`일 때만 stub/placeholder 운용으로 보고, 그 외에는 `weft tts` 실행 결과(Typecast HTTP 응답, 생성 WAV, sidecar의 `provider=typecast`)로 확인하세요. secret 값은 로그나 답변에 출력하지 마세요.

다른 프로젝트 폴더 사용 — 그냥 그 폴더로 `cd`하세요:

```bash
cd that-folder
weft all
```

이미지 후보 개수 지정:

```bash
weft images --n 3
```

### Script-To-Conti 스킬

AI 어시스턴트로 대본을 Weft `CONTI.md`로 변환할 때는 `script-to-conti` 스킬을 사용하세요. Claude에서는 `.claude/skills/script-to-conti/`, Codex에서는 `.agents/skills/script-to-conti/`가 같은 내용을 제공합니다. AI agent의 기본 작업 방식은 `weft all`이 아니라 단계별 CLI 실행입니다. 정적인 은유/삽화는 `image`, 숫자 변화·도표·수식 전개는 `remotion` 또는 `hyperframe`, 기존 영상은 `clip`으로 고르게 하세요.

AI agent가 CLI로 weft를 제어할 때의 권장 순서: `weft conti`가 0건으로 통과하면 **먼저 `conti-qa` 스킬로 품질 검토를 한 차례 돌리고** 나서 `weft tts`/`weft images`(API 과금)로 진행하세요 — 과금 전에 콘티를 고치는 것이 가장 쌉니다. 나레이션 칸의 TTS 발음 표기는 script-to-conti가 변환 시점에 적용합니다.

콘티 이후 단계에도 전용 스킬이 있습니다 (`weft whereisskill`로 경로 확인):

- `conti-qa`: `weft conti` 통과 후 콘티 품질 검토 — 리듬·이미지 프롬프트·텍스트카드·사실/추측 lint
- `visual-qa`: `weft images`/`weft ffmpeg` 후 후보 PNG와 최종 MP4를 비전으로 검수, 픽 수정·재생성 제안
- `animation-render`: `weft animate`가 만든 SPEC.md를 받아 remotion/hyperframe 샷을 `clip.mp4`로 렌더

이 스킬의 출력:

- 나레이션 비트 행
- 비주얼 샷 지시자: `▶`, `↓`, `▦`, `↺`, `⏸`, `❝`, `⤴`
- 샷 프롬프트
- 모션 메모
- 자막

그다음 프로젝트 폴더 안에서 실행:

```bash
weft conti
```

### 이미지 스타일

모든 생성 이미지는 다음을 받습니다:

```text
샷별 프롬프트 + 공유 Style 접미사
```

프로젝트 폴더(`CONTI.md` 옆)에 `STYLE.txt` 파일을 두면 공유 스타일을 덮어쓸 수 있습니다. 파일이 없으면 처음 이미지를 생성할 때 기본 3b1b 스타일 문장이 `STYLE.txt`로 자동 생성됩니다:

- `STYLE.txt` (프로젝트 폴더, `CONTI.md` 옆)
- 또는 `generated_project/STYLE.txt`

기본값은 `weft/assets.py`의 `DEFAULT_STYLE`과 같은 문장입니다. 그다음 프로젝트 폴더 안에서 이미지를 다시 생성:

```bash
weft images
```

템플릿과 예시는 `STYLE_GUIDE.md`를 참고하세요.

### 참고

- `conti`를 다시 실행하면 `generated_project`가 새로 만들어지고 픽이 초기화됩니다.
- picker로 고른 뒤에는, 프로젝트를 의도적으로 다시 만들 게 아니라면 `capcut`만 바로 실행하세요.
- `weft capcut`은 **CapCut이 바로 여는 드래프트(프로젝트)**를 만들어 CapCut 목록에 등록합니다. 등록은 CapCut의 프로젝트 목록 파일(`root_meta_info.json`)을 고치는데, **CapCut이 켜져 있으면 종료할 때 그 파일을 자기 메모리로 덮어써 새 드래프트가 사라집니다.** 그래서 빌드는 CapCut을 종료한 상태에서 하세요. (켜진 채 실행하면 등록을 건너뛰고 파일만 만들어 둡니다 — CapCut을 종료한 뒤 `weft capcut`을 다시 실행하면 목록에 등록됩니다.)

### 라이선스

MIT © 2026 Minwoo Park. [LICENSE](LICENSE) 참고.

---

<a id="english"></a>

## English

Weft is a **dual-track workflow** for long-form explainer videos. The input is **a prose script you write yourself**, on any topic — history, science, cultural analysis, thought experiments, philosophical comparisons, and so on (writing the script is outside Weft's scope).

It separates:

- **narration beats**: script, subtitles, TTS timing
- **visual shots**: generated images, text cards, reuse, montage, motion

This lets one visual cover multiple narration beats, or one narration beat use several visuals. The practical goal is to reduce one-image-per-sentence editing work while producing a **finished MP4 via ffmpeg as the default output**; CapCut drafts and FCPXML are optional handoffs when NLE editing is needed.

### Included Tools

- `install.sh`: installer (creates venv, installs the `weft` command, seeds `.env`)
- `uninstall.sh`: uninstaller (removes the `weft` command and venv; keeps `.env`)
- `weft/`: Python CLI and core pipeline
- `.claude/skills/script-to-conti/`: skill for turning a script into a Weft dual-track `CONTI.md`
- `.claude/skills/conti-qa/`: conti quality-review skill (rhythm, prompt, card, fact/speculation lint)
- `.claude/skills/visual-qa/`: vision review skill for generated image candidates and the final MP4
- `.claude/skills/animation-render/`: skill that renders remotion/hyperframe shots into `clip.mp4`
- `.agents/skills/`: Codex mirrors of the same skills
- `weft/picker/`: local image candidate picker
- `STYLE_GUIDE.md`: image style customization guide
- `WORKFLOW.html`: visual workflow overview
- `example/CONTI.md`, `example/SCRIPT.md`: sample input

Generated media (audio, images, CapCut drafts) is produced locally and is not committed.

### Setup (once)

Run this once inside the repo:

```bash
./install.sh
```

`install.sh` creates a venv, editable-installs the package (creating the `weft` command), symlinks it into `~/.local/bin`, and seeds `.env` from `.env.example`. After that, `weft` works from **any directory**.

Then fill the repo's `.env` with your own keys:

- `TYPECAST_API_KEY`, `TYPECAST_VOICE` for TTS
- `OPENAI_API_KEY` for image generation

Because the install is editable, weft finds this `.env` from any working directory. A per-project `.env` in the current folder also works.

### Uninstall

To cleanly undo the install, run inside the repo:

```bash
./uninstall.sh
```

It removes the `weft` command (symlink), the venv, and build artifacts. Your `.env` (with keys) is **kept**.

### Turn A Script Into CONTI.md

If you already have your own prose script (`SCRIPT.md`, etc.), first ask the AI assistant to **use the `script-to-conti` skill and convert it into a Weft `CONTI.md`**. The skill writes dual-track visual directives such as `▶` new image, `↓` hold, `▦` montage, and `↺` reuse, so the result does not default to one image per sentence.

- Claude: `.claude/skills/script-to-conti/`
- Codex: `.agents/skills/script-to-conti/`

To print the skill file paths from a terminal:

```bash
weft whereisskill
```

Example prompt:

```text
Use the script-to-conti skill to convert this script into a Weft CONTI.md.
Find the SKILL.md path with `weft whereisskill` and read it first.
Run weft conti and iterate until validation_errors=0.
```

### Quick Start

A project is a folder containing `CONTI.md`. `cd` into it and run subcommands that operate on the current directory (no path args needed):

```bash
cd my-video       # folder containing CONTI.md
weft conti        # ./CONTI.md -> ./generated_project/ (parse + validate + compile)
weft tts          # ./generated_project (Typecast narration; API cost)
weft images       # ./generated_project (OpenAI gpt-image-1; API cost)
weft animate      # prepare/check Remotion/HyperFrame animation shot clips
weft ffmpeg       # ./generated_project -> EXPORTS/weft_render.mp4 (first review MP4)
weft pick         # optional: manual image candidate picking
weft capcut       # optional: build CapCut draft if the MP4 needs editing
weft fcpxml       # optional: FCPXML handoff
```

One shot:

```bash
weft all          # human quick run: conti -> tts -> images -> animate check -> ffmpeg
weft all --capcut # also build a CapCut draft
weft all --fcpxml # also export FCPXML
```

### Project Commands

Run these inside the folder that contains `CONTI.md`:

```bash
weft conti            # ./CONTI.md -> JSON tracks, picks, SRT, render plan
weft parse            # print parsed CONTI.md as JSON only (writes nothing)
weft validate         # print parse+validation results only (writes nothing)
weft tts              # Typecast narration WAVs
weft images           # OpenAI image candidates
weft pick             # local browser picker
weft animate          # prepare/check Remotion/HyperFrame animation shot clips
weft ffmpeg           # ffmpeg MP4 renderer (burns in subtitles by default)
weft capcut           # optional CapCut draft builder
weft fcpxml           # optional FCPXML handoff for Final Cut/Premiere/Resolve
weft all              # human quick MP4 render
weft all --capcut     # MP4 + CapCut
weft all --fcpxml     # MP4 + FCPXML
weft settings         # create/show WEFT_SETTINGS.txt
weft whereisskill     # print script-to-conti skill paths for AI assistants
```

Aliases: `weft dryrun` equals `weft conti`, and `weft render` equals `weft ffmpeg`.

conti's CONTI source defaults to `./CONTI.md`; the other project commands default their project dir to `./generated_project`. Power users can still pass paths explicitly.

`weft capcut` and `weft all --capcut` accept `--no-register` to skip registering the draft in CapCut's list (the draft files are still written).

### WEFT_SETTINGS.txt

`weft conti` or `weft all` creates a default `WEFT_SETTINGS.txt` next to `CONTI.md` when it is missing. Like VASP `INCAR`, copy this one file into another project to reuse the same render/provider options. Explicit CLI flags override this file for one-off runs. The default quick output is an ffmpeg MP4; CapCut/FCPXML are extra handoff commands when needed.

Ready-to-copy examples live in `setting_examples/`:

- `youtube_4k_high.WEFT_SETTINGS.txt`
- `youtube_high.WEFT_SETTINGS.txt`
- `standard.WEFT_SETTINGS.txt`
- `low.WEFT_SETTINGS.txt`

Smaller test-render example:

```env
EXPORT_FFMPEG=true
FFMPEG_ENCODER=libx264
FFMPEG_CRF=32
FFMPEG_PRESET=veryfast
FFMPEG_BITRATE=2M
```

Then:

```bash
weft settings
```

also creates or shows the current project settings file.

```bash
weft all
```

`weft ffmpeg` renders `EXPORTS/render_plan.json`, picked images, TTS WAVs, and compiled subtitle events directly into `EXPORTS/weft_render.mp4`. Subtitles are burned in by default (`--no-subtitles` disables them), and `--encoder auto` tries macOS `h264_videotoolbox` first before falling back to `libx264`. Use `--preset ultrafast` for faster software-encoding checks, or adjust `--crf` for size/quality.

`weft animate` creates `SHOTS/<shot>/animation/SPEC.md` for `source_kind=remotion` and `source_kind=hyperframe` shots, then checks for the expected rendered output `SHOTS/<shot>/rendered/clip.mp4`. Once rendered, these MP4s enter `weft ffmpeg`, `weft capcut`, and `weft fcpxml` like normal clips.

`weft fcpxml` exports the same `render_plan` as an editable FCPXML timeline: images/clips/animation clips on a video lane, TTS on an audio lane, and subtitles on a title lane.

Providers are selectable in `.env`:

```env
IMAGE_PROVIDER=openai   # openai | comfyui | stub
TTS_PROVIDER=typecast   # typecast | stub
```

`stub` creates local PNG/WAV assets without API keys for tests and offline dry runs.

`comfyui` generates images on a local ComfyUI server (`COMFYUI_URL`, default `http://127.0.0.1:8188`).
Point `COMFYUI_WORKFLOW` at a workflow JSON exported from ComfyUI with **Save (API Format)**,
and put `__WEFT_PROMPT__` where the positive prompt text goes — Weft substitutes the shot prompt there (JSON-escape safe).
(Optional: `__WEFT_SEED__` gets a fresh seed per candidate; without it, `"seed"` keys are randomized so the N candidates are real variations.)

AI agent note: do not decide whether an API key is real from its string shape. A Typecast key may start with `__` and still be valid. Treat TTS as stub/placeholder only when `TYPECAST_API_KEY` or `TYPECAST_VOICE` is empty, or when `TTS_PROVIDER=stub` is set. Otherwise verify by running `weft tts` and checking the Typecast HTTP result, generated WAVs, or sidecar metadata with `provider=typecast`. Never print secret values in logs or responses.

Use another project folder — just `cd` into it:

```bash
cd that-folder
weft all
```

Set image candidate count:

```bash
weft images --n 3
```

### Script-To-Conti Skill

Use the `script-to-conti` skill when you want an AI assistant to convert a script into a Weft `CONTI.md`. Claude reads `.claude/skills/script-to-conti/`; Codex reads `.agents/skills/script-to-conti/`. AI agents should normally run the CLI step by step instead of using `weft all`: choose `image` for static illustration/metaphor shots, `remotion` or `hyperframe` for charts/equations/process animation, and `clip` for existing video.

Recommended order when an AI agent drives weft via the CLI: once `weft conti` passes with zero violations, **run the `conti-qa` skill once before** `weft tts`/`weft images` (which bill API usage) — fixing the conti before billing is the cheapest point. TTS-friendly pronunciation in the narration column is applied by script-to-conti at conversion time.

Dedicated skills also cover the stages after the conti (`weft whereisskill` prints the paths):

- `conti-qa`: quality review after `weft conti` passes — rhythm, image prompts, text cards, fact/speculation lint
- `visual-qa`: vision review of candidate PNGs and the final MP4 after `weft images`/`weft ffmpeg`, with pick fixes and regeneration suggestions
- `animation-render`: takes the SPEC.md from `weft animate` and renders remotion/hyperframe shots into `clip.mp4`

The skill outputs:

- narration beat rows
- visual shot directives: `▶`, `↓`, `▦`, `↺`, `⏸`, `❝`, `⤴`
- shot prompts
- motion notes
- subtitles

Then, from inside the project folder, run:

```bash
weft conti
```

### Image Style

Every generated image receives:

```text
shot-specific prompt + shared Style suffix
```

Override the shared style by placing a `STYLE.txt` file in the project folder (next to `CONTI.md`). If the file is missing, the first image-generation run materializes the default 3b1b-style sentence into `STYLE.txt`:

- `STYLE.txt` (in the project folder, next to `CONTI.md`)
- or `generated_project/STYLE.txt`

The generated default matches `DEFAULT_STYLE` in `weft/assets.py`. Then, from inside the project folder, regenerate images:

```bash
weft images
```

See `STYLE_GUIDE.md` for templates and examples.

### Notes

- Re-running `conti` rebuilds `generated_project` and resets picks.
- After using the picker, run `capcut` directly unless you intentionally want to regenerate the project.
- `weft capcut` builds a **CapCut-openable draft (project)** and registers it in CapCut's project list. Registration edits CapCut's project-list file (`root_meta_info.json`), and **if CapCut is running it overwrites that file from memory on quit, so a freshly registered draft disappears.** Build with CapCut closed. (If it's running, registration is skipped and only the files are written — close CapCut and run `weft capcut` again to register it in the project list.)

### License

MIT © 2026 Minwoo Park. See [LICENSE](LICENSE).
