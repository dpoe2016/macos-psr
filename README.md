# macOS Problem Steps Recorder (PSR)

A macOS equivalent of Windows' **Problem Steps Recorder** (PSR). Automatically captures screenshots on every mouse click or Enter key press, annotates click positions, and generates HTML and PDF reports.

## Features

- **Automatic screenshot capture** on every mouse click or Enter key press
- **Active window capture** by default (full screen optional)
- **Mouse cursor visible** in every screenshot
- **Click position annotation** with red circle, crosshair, and step number
- **On-screen drawing tools** — rectangle, arrow, freehand, highlight via transparent overlay
- **HTML report** with all steps, timestamps, and window info
- **PDF export** with one page per step
- **AI-powered descriptions** — Claude Vision analyzes each screenshot and describes the user's action in German

## Requirements

- macOS
- Python 3.10+
- macOS **Accessibility** and **Screen Recording** permissions for the terminal running the script

## Installation

```bash
pip3 install --user --break-system-packages pynput pillow fpdf2 anthropic
```

## Usage

```bash
# Basic recording (active window, HTML report)
python3 psr.py

# With PDF export and AI descriptions
export ANTHROPIC_API_KEY="sk-ant-..."
python3 psr.py --pdf

# PDF without AI descriptions
python3 psr.py --pdf --no-ai

# Full screen instead of active window
python3 psr.py --fullscreen

# Custom output directory and capture delay
python3 psr.py -o ~/Desktop/my-report -d 0.5
```

## Options

| Flag | Description |
|------|-------------|
| `--pdf` | Generate PDF report (in addition to HTML) |
| `--no-ai` | Skip AI-generated step descriptions in PDF |
| `--fullscreen` | Capture the full screen instead of active window |
| `-o`, `--output DIR` | Output directory (default: `~/Desktop/PSR_<timestamp>`) |
| `-d`, `--delay SEC` | Minimum delay between captures in seconds (default: 0.3) |

## Controls

### Recording Mode

| Key | Action |
|-----|--------|
| Mouse click | Capture a step |
| Enter | Capture a step |
| ESC | Stop recording and generate report |

### Annotation Tools

Activate with `Ctrl+<number>` or `fn+F<number>`:

| Shortcut | Tool |
|----------|------|
| Ctrl+1 / F1 | Rectangle |
| Ctrl+2 / F2 | Arrow |
| Ctrl+3 / F3 | Freehand |
| Ctrl+4 / F4 | Highlight |
| Ctrl+5 / F5 | Clear all annotations |
| Ctrl+6 / F6 | Undo last annotation |
| Ctrl+7 / F7 | Cycle color (red, blue, green, yellow) |
| Ctrl+8 / F8 | Exit draw mode |
| ESC | Exit draw mode (if active) |

When a drawing tool is active, drag on screen to draw. Annotations stay visible and are included in subsequent screenshots.

## Output

Reports are saved to `~/Desktop/PSR_<timestamp>/` (or custom path via `-o`):

```
PSR_20260316_194500/
  report.html          # Interactive HTML report
  report.pdf           # PDF with AI descriptions (if --pdf)
  screenshots/
    step_0001.png
    step_0002.png
    ...
```

## License

MIT
