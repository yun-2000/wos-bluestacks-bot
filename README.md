# wos-bluestacks-bot

Whiteout Survival automation for **BlueStacks on macOS**, with a visual task builder.

Forked from [austxio/WOS-Bot](https://github.com/austxio/WOS-Bot). Tuned for Mac + BlueStacks window capture, with a built-in **Hold Rally** loop gated by OCR (Marching `1/6`).

## What's included

- **Hold Rally** — Search target → hold rally → select hun → deploy; waits until Marching shows `1/6` before the next cycle
- **Window Capture** — BlueStacks / Quartz on macOS (also Google Play Games on Windows)
- **ADB Support** — Physical Android devices or emulators
- **Task Builder GUI** — Drag-and-drop steps, no coding required
- **Template Matching** — Crop UI elements as OpenCV templates
- **OCR Gate** — `loop_until_text` via Tesseract (e.g. wait for `1/6`)
- **Conditional Logic** — `if_found` / `else`, `loop`, `loop_until_found`, `loop_templates`
- **Live Log** — WebSocket real-time execution log

## Quick Start

```bash
git clone https://github.com/yun-2000/wos-bluestacks-bot.git && cd wos-bluestacks-bot
pip install -r requirements.txt
# OCR (loop_until_text) needs system Tesseract:
#   macOS: brew install tesseract
#   Windows: https://github.com/UB-Mannheim/tesseract/wiki
python app.py
```

Open **http://localhost:8000** in your browser.

1. Open Whiteout Survival in BlueStacks (auto-detected on Mac) or connect via ADB
2. Run the **Hold Rally** task, or build your own in the Task Builder
3. Watch the live execution log

## Bundled tasks

| File | Purpose |
|------|---------|
| `tasks/hold_rally.yaml` | Main Hold Rally loop (OCR gate on Marching `1/6`) |
| `tasks/_select_hun_and_deploy.yaml` | Sub-task: pick hun formation and deploy |

## Task Actions

| Action | Description |
|--------|-------------|
| `find_and_tap` | Find template on screen and tap it |
| `tap` | Tap fixed coordinates |
| `wait` | Wait N seconds |
| `tap_back` | Press back / ESC |
| `tap_dismiss` | Press back to dismiss popups |
| `swipe` | Swipe gesture |
| `if_found` | Branch: if template found → THEN steps, else → ELSE steps |
| `run_task` | Execute another task file as sub-task |
| `loop` | Repeat steps N times or until stopped |
| `loop_until_found` | Keep checking until template appears |
| `loop_until_text` | Keep checking until OCR reads expected text in a region |
| `loop_templates` | Cycle through multiple templates, tap any found |
| `verify` | Assert template is visible |
| `screenshot` | Save debug screenshot |

## Task YAML Format

```yaml
name: Claim Daily Rewards
description: Collects login rewards
icon: "🎁"
steps:
  - action: tap_dismiss

  - action: find_and_tap
    template: daily_reward_popup.png
    optional: true
    ignore_badge: true

  - action: if_found
    template: close_button.png
    then:
      - action: find_and_tap
        template: close_button.png
    else:
      - action: tap_back

  - action: loop_until_text
    text: "1/6"
    region_pct: [0.0, 0.13, 0.40, 0.20]
    max_loops: 0
    loop_delay: 2

  - action: run_task
    task_file: go_home.yaml
```

## Project Structure

```
wos-bluestacks-bot/
├── app.py           # FastAPI server + API routes
├── device.py        # WindowDevice / ADBDevice abstraction
├── engine.py        # Task loader + step executor
├── vision.py        # OpenCV template matching + Tesseract OCR
├── views/           # Jinja2 HTML templates
├── static/          # CSS
├── tasks/           # YAML task definitions (Hold Rally, …)
├── templates/       # Template images for matching
└── screenshots/     # Captured screenshots
```

## Device Modes

### Window mode (default)
Captures the game window via Quartz on macOS (BlueStacks) or Win32 on Windows (Google Play Games). Clicks via `pyautogui`. No ADB required.

### ADB mode
Connects to a physical Android device or emulator via `adb`. Supports remote devices (`adb connect host:port`).

## Tips

- Set `optional: true` on steps that may not always appear (popups, conditional UI)
- Use `ignore_badge: true` for buttons with changing notification numbers
- Use `confidence: 0.7` for fuzzy matches, `0.9` for exact matches
- Crop templates tightly around the target element for best results
- For Hold Rally, tune `region_pct` on the Marching bar if OCR misreads

## Requirements

- Python 3.12+
- macOS (BlueStacks / Quartz) or Windows (Google Play Games)
- Tesseract OCR (`brew install tesseract` on macOS) for `loop_until_text`

## License

MIT

---

Based on [WOS-Bot](https://github.com/austxio/WOS-Bot) by [Augustinus](https://github.com/austxio). Maintained at [yun-2000/wos-bluestacks-bot](https://github.com/yun-2000/wos-bluestacks-bot).
