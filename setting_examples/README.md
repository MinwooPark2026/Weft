# Weft Setting Examples

`WEFT_SETTINGS.txt`는 프로젝트별 실행 옵션 파일입니다. VASP `INCAR`처럼 `CONTI.md` 옆에 두면, 같은 옵션으로 다시 뽑을 때 이 파일만 복사하면 됩니다.

사용:

```bash
cp setting_examples/standard.WEFT_SETTINGS.txt path/to/project/WEFT_SETTINGS.txt
cd path/to/project
weft all
```

`weft all`은 기본적으로 ffmpeg MP4를 먼저 만듭니다. CapCut/FCPXML은 `EXPORT_CAPCUT=true`, `EXPORT_FCPXML=true` 또는 CLI 옵션으로 추가합니다. CLI로 직접 준 옵션은 `WEFT_SETTINGS.txt`보다 우선합니다. 예를 들어 설정 파일은 그대로 두고 한 번만 저화질로 뽑고 싶으면:

```bash
weft ffmpeg --encoder libx264 --crf 34 --width 1280 --height 720
```

## CLI 옵션과 설정 파일 키

| CLI | WEFT_SETTINGS.txt |
|---|---|
| `weft all` | `EXPORT_FFMPEG=true` |
| `weft all --no-ffmpeg` | `EXPORT_FFMPEG=false` |
| `weft all --capcut` | `EXPORT_CAPCUT=true` |
| `weft all --fcpxml` | `EXPORT_FCPXML=true` |
| `weft all --out generated_project` | `PROJECT_OUT=generated_project` |
| `weft images --n 3` | `IMAGE_CANDIDATES_N=3` |
| `weft images --quality high` | `IMAGE_QUALITY=high` |
| `weft images --size 1536x1024` | `IMAGE_SIZE=1536x1024` |
| `weft ffmpeg --encoder libx264` | `FFMPEG_ENCODER=libx264` |
| `weft ffmpeg --width 3840 --height 2160` | `FFMPEG_WIDTH=3840`, `FFMPEG_HEIGHT=2160` |
| `weft ffmpeg --crf 32` | `FFMPEG_CRF=32` |
| `weft ffmpeg --bitrate 2M` | `FFMPEG_BITRATE=2M` |
| `weft ffmpeg --preset ultrafast` | `FFMPEG_PRESET=ultrafast` |
| `weft ffmpeg --no-subtitles` | `FFMPEG_NO_SUBTITLES=true` |
| `weft ffmpeg --no-motion` | `FFMPEG_NO_MOTION=true` |
| `weft ffmpeg --no-audio` | `FFMPEG_NO_AUDIO=true` |
| `weft capcut --folder my_draft` | `CAPCUT_FOLDER=my_draft` |
| `weft capcut --no-register` | `CAPCUT_NO_REGISTER=true` |

API 키는 이 파일에 넣지 말고 `.env`에 둡니다.

## 품질 프리셋

- `youtube_4k_high.WEFT_SETTINGS.txt`: 3840x2160, YouTube 4K 고화질 업로드용.
- `youtube_high.WEFT_SETTINGS.txt`: 1920x1080, YouTube 1080p 고화질 업로드용.
- `standard.WEFT_SETTINGS.txt`: 1920x1080 일반 품질.
- `low.WEFT_SETTINGS.txt`: 1280x720 저용량 테스트/초안용.

`FFMPEG_ENCODER=auto`는 macOS에서 `h264_videotoolbox`를 먼저 시도합니다. 이 경우 용량은 주로 `FFMPEG_BITRATE`가 좌우합니다. VideoToolbox가 실패해 `libx264`로 fallback되거나 `FFMPEG_ENCODER=libx264`로 고정하면 용량은 주로 `FFMPEG_CRF`가 좌우합니다. CRF는 숫자가 클수록 화질이 낮고 용량이 작습니다.
