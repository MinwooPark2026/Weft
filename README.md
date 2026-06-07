# Weft

Weft is a dual-track workflow for long-form explainer videos.

It separates:

- **narration beats**: script, subtitles, TTS timing
- **visual shots**: generated images, text cards, reuse, montage, motion

This lets one visual cover multiple narration beats, or one narration beat use several visuals. The practical goal is to reduce one-image-per-sentence editing work while still producing an editable CapCut draft.

## Included Tools

- `weft/`: Python CLI and core pipeline
- `.claude/skills/script-to-conti/`: skill for turning a script into a Weft dual-track `CONTI.md`
- `weft/picker/`: local image candidate picker
- `STYLE_GUIDE.md`: image style customization guide
- `WORKFLOW.html`: visual workflow overview
- `dryrun/CONTI.md`, `dryrun/SCRIPT.md`: sample input

Generated media (audio, images, CapCut drafts) is produced locally and is not committed.

## Setup

```bash
./run.sh setup
cp .env.example .env
```

Fill `.env` with your own keys:

- `TYPECAST_API_KEY`, `TYPECAST_VOICE` for TTS
- `OPENAI_API_KEY` for image generation

## Quick Start

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

## Project Commands

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

## Script-To-Conti Skill

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

## Image Style

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

## Notes

- Re-running `conti` rebuilds `generated_project` and resets picks.
- After using the picker, run `capcut` directly unless you intentionally want to regenerate the project.
- Close CapCut before registering a draft.

## License

MIT © 2026 Minwoo Park. See [LICENSE](LICENSE).
