# WOS Bot

Visual automation bot for **Whiteout Survival** with a task builder GUI.

Build custom automation tasks using a drag-and-drop step editor, template matching with OpenCV, and a live execution log — no coding required.

## Features

- **Window Capture** — Works directly with Google Play Games on PC (no ADB/root needed)
- **ADB Support** — Also works with Android phones over ADB
- **Task Builder GUI** — Create, edit, and reorder automation steps visually
- **Template Matching** — Screenshot cropping tool to capture UI elements as templates
- **Smart Matching** — Ignore notification badges, adjustable confidence threshold
- **Conditional Logic** — `if_found` / `else` branching, `loop`, `loop_until_found`, `loop_templates`
- **Sub-tasks** — Run reusable task files from within other tasks
- **Live Log** — WebSocket-powered real-time execution log

## Quick Start

```bash
git clone https://github.com/austxio/WOS-Bot.git && cd WOS-Bot
pip install -r requirements.txt
pip install pyautogui pygetwindow
python app.py
```

Open **http://localhost:8000** in your browser.

## How It Works

1. **Connect** — Open Whiteout Survival in Google Play Games (auto-detected) or connect an Android device via ADB
2. **Capture Templates** — Go to Templates tab, take a screenshot, crop buttons/icons you want the bot to find
3. **Build Tasks** — Create tasks with steps like `find_and_tap`, `wait`, `if_found`, `loop_templates`
4. **Run** — Hit ▶ Run and watch the execution log

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

  - action: loop_templates
    templates:
      - claim_button.png
      - collect_button.png
    max_loops: 5
    loop_delay: 1

  - action: run_task
    task_file: go_home.yaml
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard |
| `GET` | `/devices` | List connected devices |
| `POST` | `/device` | Set active device |
| `POST` | `/device/connect-gpg` | Connect ADB to GPG emulator |
| `POST` | `/tasks/{file}/run` | Run a task |
| `POST` | `/tasks/stop` | Stop running task |
| `POST` | `/tasks/create` | Create new task |
| `POST` | `/tasks/{file}/save` | Update task |
| `DELETE` | `/tasks/{file}` | Delete task |
| `POST` | `/screenshot` | Capture screenshot (base64) |
| `POST` | `/template/crop` | Crop template from screenshot |
| `POST` | `/template/upload` | Upload template image |
| `DELETE` | `/template/{name}` | Delete template |
| `WS` | `/ws/logs` | Real-time execution logs |

## Project Structure

```
WOS-Bot/
├── app.py           # FastAPI server + API routes
├── device.py        # WindowDevice / ADBDevice abstraction
├── engine.py        # Task loader + step executor
├── vision.py        # OpenCV template matching
├── views/           # Jinja2 HTML templates
├── static/          # CSS
├── tasks/           # YAML task definitions
├── templates/       # Template images for matching
└── screenshots/     # Captured screenshots
```

## Device Modes

### Window mode (default)
Captures the game window directly via Win32 API. Clicks via `pyautogui`. No ADB or root required. Works with Google Play Games on Windows.

### ADB mode
Connects to a physical Android device or emulator via `adb`. Supports remote devices (`adb connect host:port`).

## Tips

- Set `optional: true` on steps that may not always appear (popups, conditional UI)
- Use `ignore_badge: true` for buttons with changing notification numbers
- Use `confidence: 0.7` for fuzzy matches, `0.9` for exact matches
- Crop templates tightly around the target element for best results
- Use `loop_templates` to scan and tap multiple buttons in one pass

## Requirements

- Python 3.12+
- Windows (for Google Play Games window capture)
- Google Play Games or Android device with ADB

## License

MIT

---

Made by [Augustinus](https://github.com/austxio)
