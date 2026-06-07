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

- `install.sh`: 설치 스크립트 (venv 생성, `weft` 명령 설치, `.env` 시드)
- `uninstall.sh`: 제거 스크립트 (`weft` 명령·venv 제거, `.env`는 보존)
- `weft/`: 파이썬 CLI와 핵심 파이프라인
- `.claude/skills/script-to-conti/`: 대본을 Weft 이중 트랙 `CONTI.md`로 바꾸는 스킬
- `.agents/skills/script-to-conti/`: Codex에서도 같은 스킬을 쓰기 위한 미러
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

산문 대본(`SCRIPT.md` 등)이 있으면 먼저 AI 어시스턴트에게 **`script-to-conti` 스킬을 사용해서 Weft `CONTI.md`로 변환**하라고 요청하세요. 이 스킬은 한 문장마다 이미지를 하나씩 만들지 않고, `▶` 새 그림·`↓` 홀드·`▦` 몽타주·`↺` 재사용 같은 이중 트랙 지시를 넣어 이미지 수를 줄이는 콘티를 만듭니다.

- Claude용: `.claude/skills/script-to-conti/`
- Codex용: `.agents/skills/script-to-conti/`

예시 요청:

```text
이 대본을 script-to-conti 스킬로 Weft CONTI.md로 바꿔줘.
검증은 weft conti까지 돌려서 validation_errors=0이 되게 해줘.
```

### 빠른 시작

프로젝트는 `CONTI.md`가 들어 있는 폴더입니다. 그 폴더로 `cd`한 뒤, 현재 디렉터리를 대상으로 동작하는 하위 명령을 실행하세요(경로 인자 불필요):

```bash
cd my-video       # CONTI.md가 있는 폴더
weft conti        # ./CONTI.md -> ./generated_project/ (파싱 + 검증 + 컴파일)
weft tts          # ./generated_project (Typecast 나레이션; API 비용)
weft images       # ./generated_project (OpenAI gpt-image-1; API 비용)
weft pick         # ./generated_project (로컬 브라우저 picker, 왼손 단축키)
weft capcut       # ./generated_project -> CapCut 드래프트 (CapCut 종료 후 실행)
```

한 번에:

```bash
weft all          # conti -> tts -> images -> capcut
```

### 프로젝트 명령

`CONTI.md`가 있는 폴더 안에서 실행하세요:

```bash
weft conti            # ./CONTI.md -> JSON 트랙·픽·SRT·렌더 플랜
weft tts              # Typecast 나레이션 WAV
weft images           # OpenAI 이미지 후보
weft pick             # 로컬 브라우저 picker
weft capcut           # CapCut 드래프트 빌더
weft all              # conti -> tts -> images -> capcut
```

conti의 입력은 기본값이 `./CONTI.md`, tts/images/pick/capcut의 프로젝트 디렉터리는 기본값이 `./generated_project`입니다. 파워 유저는 경로를 명시적으로 넘길 수도 있습니다.

`weft capcut`와 `weft all`은 `--no-register`로 CapCut 목록 등록을 건너뛸 수 있습니다(드래프트 파일만 생성).

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

AI 어시스턴트로 대본을 Weft `CONTI.md`로 변환할 때는 `script-to-conti` 스킬을 사용하세요. Claude에서는 `.claude/skills/script-to-conti/`, Codex에서는 `.agents/skills/script-to-conti/`가 같은 내용을 제공합니다.

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

프로젝트 폴더(`CONTI.md` 옆)에 `STYLE.txt` 파일을 두면 공유 스타일을 덮어쓸 수 있습니다:

- `STYLE.txt` (프로젝트 폴더, `CONTI.md` 옆)
- 또는 `generated_project/STYLE.txt`

기본값은 `weft/assets.py`의 `DEFAULT_STYLE`입니다. 그다음 프로젝트 폴더 안에서 이미지를 다시 생성:

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

Weft is a **dual-track workflow** for long-form explainer videos.

It separates:

- **narration beats**: script, subtitles, TTS timing
- **visual shots**: generated images, text cards, reuse, montage, motion

This lets one visual cover multiple narration beats, or one narration beat use several visuals. The practical goal is to reduce one-image-per-sentence editing work while still producing an editable CapCut draft.

### Included Tools

- `install.sh`: installer (creates venv, installs the `weft` command, seeds `.env`)
- `uninstall.sh`: uninstaller (removes the `weft` command and venv; keeps `.env`)
- `weft/`: Python CLI and core pipeline
- `.claude/skills/script-to-conti/`: skill for turning a script into a Weft dual-track `CONTI.md`
- `.agents/skills/script-to-conti/`: Codex mirror of the same skill
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

If you already have prose script input (`SCRIPT.md`, etc.), first ask the AI assistant to **use the `script-to-conti` skill and convert it into a Weft `CONTI.md`**. The skill writes dual-track visual directives such as `▶` new image, `↓` hold, `▦` montage, and `↺` reuse, so the result does not default to one image per sentence.

- Claude: `.claude/skills/script-to-conti/`
- Codex: `.agents/skills/script-to-conti/`

Example prompt:

```text
Use the script-to-conti skill to convert this script into a Weft CONTI.md.
Run weft conti and iterate until validation_errors=0.
```

### Quick Start

A project is a folder containing `CONTI.md`. `cd` into it and run subcommands that operate on the current directory (no path args needed):

```bash
cd my-video       # folder containing CONTI.md
weft conti        # ./CONTI.md -> ./generated_project/ (parse + validate + compile)
weft tts          # ./generated_project (Typecast narration; API cost)
weft images       # ./generated_project (OpenAI gpt-image-1; API cost)
weft pick         # ./generated_project (local browser picker, left-hand shortcuts)
weft capcut       # ./generated_project -> CapCut draft (run with CapCut closed)
```

One shot:

```bash
weft all          # conti -> tts -> images -> capcut
```

### Project Commands

Run these inside the folder that contains `CONTI.md`:

```bash
weft conti            # ./CONTI.md -> JSON tracks, picks, SRT, render plan
weft tts              # Typecast narration WAVs
weft images           # OpenAI image candidates
weft pick             # local browser picker
weft capcut           # CapCut draft builder
weft all              # conti -> tts -> images -> capcut
```

conti's CONTI source defaults to `./CONTI.md`; tts/images/pick/capcut default their project dir to `./generated_project`. Power users can still pass paths explicitly.

`weft capcut` and `weft all` accept `--no-register` to skip registering the draft in CapCut's list (the draft files are still written).

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

Use the `script-to-conti` skill when you want an AI assistant to convert a script into a Weft `CONTI.md`. Claude reads `.claude/skills/script-to-conti/`; Codex reads `.agents/skills/script-to-conti/`.

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

Override the shared style by placing a `STYLE.txt` file in the project folder (next to `CONTI.md`):

- `STYLE.txt` (in the project folder, next to `CONTI.md`)
- or `generated_project/STYLE.txt`

The built-in default is `DEFAULT_STYLE` in `weft/assets.py`. Then, from inside the project folder, regenerate images:

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
