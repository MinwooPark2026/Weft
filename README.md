# Weft

<p align="left"><a href="#한국어"><b>한국어</b></a> · <a href="#english"><b>English</b></a></p>

---

<a id="한국어"></a>

## 한국어

Weft는 긴 호흡의 설명형 영상을 위한 **이중 트랙(dual-track) 워크플로우**입니다.

다음 둘을 분리합니다:

- **나레이션 비트(narration beats)**: 대본, 자막, TTS 타이밍
- **비주얼 샷(visual shots)**: 생성 이미지, 텍스트 카드, 재사용, 몽타주, 모션

이렇게 하면 하나의 비주얼이 여러 나레이션 비트를 덮거나, 하나의 나레이션 비트가 여러 비주얼을 쓸 수 있습니다. 실질적 목표는 "문장마다 이미지 한 장" 식의 편집 노동을 줄이면서도, CapCut에서 바로 편집 가능한 드래프트를 만들어내는 것입니다.

### 구성 요소

- `weft/`: 파이썬 CLI와 핵심 파이프라인
- `.claude/skills/script-to-conti/`: 대본을 Weft 이중 트랙 `CONTI.md`로 바꾸는 스킬
- `weft/picker/`: 로컬 이미지 후보 선택기(picker)
- `STYLE_GUIDE.md`: 이미지 스타일 커스터마이즈 가이드
- `WORKFLOW.html`: 시각적 워크플로우 개요
- `dryrun/CONTI.md`, `dryrun/SCRIPT.md`: 예시 입력

생성물(오디오·이미지·CapCut 드래프트)은 로컬에서 만들어지며 저장소에 커밋되지 않습니다.

### 설치

```bash
./run.sh setup
cp .env.example .env
```

`.env`에 본인 키를 채우세요:

- TTS용 `TYPECAST_API_KEY`, `TYPECAST_VOICE`
- 이미지 생성용 `OPENAI_API_KEY`

### 빠른 시작

```bash
./run.sh conti
./run.sh tts
./run.sh images
./run.sh pick
./run.sh capcut
```

동등한 직접 실행 명령:

```bash
python3 -m unittest discover -s tests
python3 -m weft.cli validate dryrun/CONTI.md
python3 -m weft.cli dryrun dryrun/CONTI.md --out dryrun/generated_project
```

### 프로젝트 명령

```bash
./run.sh conti            # CONTI.md -> JSON 트랙·픽·SRT·렌더 플랜
./run.sh tts              # Typecast 나레이션 WAV
./run.sh images           # OpenAI 이미지 후보
./run.sh pick             # 로컬 브라우저 picker
./run.sh capcut           # CapCut 드래프트 빌더
./run.sh all              # conti -> tts -> images -> capcut
```

다른 프로젝트 폴더 사용:

```bash
PROJECT=ep3 ./run.sh all
./run.sh all ep3
```

이미지 후보 개수 지정:

```bash
N=3 ./run.sh images
```

### Script-To-Conti 스킬

AI 어시스턴트로 대본을 Weft `CONTI.md`로 변환하고 싶을 때 `.claude/skills/script-to-conti/`를 사용하세요.

이 스킬의 출력:

- 나레이션 비트 행
- 비주얼 샷 지시자: `▶`, `↓`, `▦`, `↺`, `⏸`, `❝`, `⤴`
- 샷 프롬프트
- 모션 메모
- 자막

그다음 실행:

```bash
./run.sh conti <project>
```

### 이미지 스타일

모든 생성 이미지는 다음을 받습니다:

```text
샷별 프롬프트 + 공유 Style 접미사
```

`STYLE.txt` 파일을 작성하면 공유 스타일을 덮어쓸 수 있습니다:

- `<project>/generated_project/STYLE.txt`
- 또는 `<project>/STYLE.txt`

그다음 이미지를 다시 생성:

```bash
./run.sh images <project>
```

템플릿과 예시는 `STYLE_GUIDE.md`를 참고하세요.

### 참고

- `conti`를 다시 실행하면 `generated_project`가 새로 만들어지고 픽이 초기화됩니다.
- picker로 고른 뒤에는, 프로젝트를 의도적으로 다시 만들 게 아니라면 `capcut`만 바로 실행하세요.
- 드래프트를 등록하기 전에 CapCut을 종료하세요.

### 라이선스

MIT © 2026 Minwoo Park. [LICENSE](LICENSE) 참고.

---

<a id="english"></a>

## English

Weft is a **dual-track workflow** for long-form explainer videos.

It separates:

- **narration beats**: script, subtitles, TTS timing
- **visual shots**: generated images, text cards, reuse, montage, motion

This lets one visual cover multiple narration beats, or one narration beat use several visuals. The practical goal is to reduce one-image-per-sentence editing work while still producing an editable CapCut draft.

### Included Tools

- `weft/`: Python CLI and core pipeline
- `.claude/skills/script-to-conti/`: skill for turning a script into a Weft dual-track `CONTI.md`
- `weft/picker/`: local image candidate picker
- `STYLE_GUIDE.md`: image style customization guide
- `WORKFLOW.html`: visual workflow overview
- `dryrun/CONTI.md`, `dryrun/SCRIPT.md`: sample input

Generated media (audio, images, CapCut drafts) is produced locally and is not committed.

### Setup

```bash
./run.sh setup
cp .env.example .env
```

Fill `.env` with your own keys:

- `TYPECAST_API_KEY`, `TYPECAST_VOICE` for TTS
- `OPENAI_API_KEY` for image generation

### Quick Start

```bash
./run.sh conti
./run.sh tts
./run.sh images
./run.sh pick
./run.sh capcut
```

Equivalent direct commands:

```bash
python3 -m unittest discover -s tests
python3 -m weft.cli validate dryrun/CONTI.md
python3 -m weft.cli dryrun dryrun/CONTI.md --out dryrun/generated_project
```

### Project Commands

```bash
./run.sh conti            # CONTI.md -> JSON tracks, picks, SRT, render plan
./run.sh tts              # Typecast narration WAVs
./run.sh images           # OpenAI image candidates
./run.sh pick             # local browser picker
./run.sh capcut           # CapCut draft builder
./run.sh all              # conti -> tts -> images -> capcut
```

Use another project folder:

```bash
PROJECT=ep3 ./run.sh all
./run.sh all ep3
```

Set image candidate count:

```bash
N=3 ./run.sh images
```

### Script-To-Conti Skill

Use `.claude/skills/script-to-conti/` when you want an AI assistant to convert a script into a Weft `CONTI.md`.

The skill outputs:

- narration beat rows
- visual shot directives: `▶`, `↓`, `▦`, `↺`, `⏸`, `❝`, `⤴`
- shot prompts
- motion notes
- subtitles

Then run:

```bash
./run.sh conti <project>
```

### Image Style

Every generated image receives:

```text
shot-specific prompt + shared Style suffix
```

Override the shared style by writing a `STYLE.txt` file:

- `<project>/generated_project/STYLE.txt`
- or `<project>/STYLE.txt`

Then regenerate images:

```bash
./run.sh images <project>
```

See `STYLE_GUIDE.md` for templates and examples.

### Notes

- Re-running `conti` rebuilds `generated_project` and resets picks.
- After using the picker, run `capcut` directly unless you intentionally want to regenerate the project.
- Close CapCut before registering a draft.

### License

MIT © 2026 Minwoo Park. See [LICENSE](LICENSE).
