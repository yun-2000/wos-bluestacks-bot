# WOS Bot

Personal automation bot for Whiteout Survival. Runs on Windows with Google Play Games or on Android devices via ADB.

## Features

- **Visual automation** — OpenCV template matching to find and tap UI elements
- **YAML task scripting** — Define automation sequences as simple YAML files
- **Web dashboard** — Create, edit, run, and monitor tasks from a browser
- **Template manager** — Capture screenshots and crop UI elements for matching
- **Dual device support** — Window capture (Google Play Games) or ADB (Android phone)
- **Real-time logs** — WebSocket-powered execution log with color-coded levels

## Requirements

- Python 3.11+
- Google Play Games (Windows) with Whiteout Survival running, **or** an Android device with ADB
- OpenCV (`opencv-python`)

## Setup

```bash
git clone <repo-url> && cd WOS
pip install -r requirements.txt
pip install pyautogui pygetwindow
```

## Usage

### 1. Start the server

```bash
python app.py
```

Open **http://localhost:8000** in a browser.

### 2. Connect a device

The bot auto-detects a visible "Whiteout Survival" window. For ADB devices, click **Connect GPG** or plug in a phone with USB debugging enabled.

### 3. Create templates

Go to **Templates** tab → **Capture Screenshot** → draw a box around the UI element → name it → **Save Crop**.

### 4. Create / run tasks

Go to **Tasks** tab → **+ New Task** → add steps using the visual editor → **Save**. Hit **Run** on the dashboard.

## Task YAML Format

```yaml
name: Claim Daily Rewards
description: Collects login rewards
icon: "🎁"
steps:
  - action: find_and_tap
    template: daily_reward_popup.png
    optional: true
    confidence: 0.8
    retries: 3

  - action: wait
    seconds: 2

  - action: tap
    x: 400
    y: 600

  - action: swipe
    x1: 300
    y1: 800
    x2: 300
    y2: 400
    duration_ms: 500

  - action: tap_back

  - action: verify
    template: home_screen.png
    timeout: 10

  - action: screenshot
    filename: debug.png

  - action: repeat
    count: 2
    times: 3
```

### Step actions

| Action | Description | Key fields |
|--------|-------------|------------|
| `find_and_tap` | Find template on screen, tap its center | `template`, `confidence`, `optional`, `retries` |
| `tap` | Tap fixed coordinates | `x`, `y` |
| `swipe` | Swipe between two points | `x1`, `y1`, `x2`, `y2`, `duration_ms` |
| `wait` | Pause execution | `seconds` |
| `tap_back` | Press back / ESC | — |
| `screenshot` | Save current screen | `filename` |
| `verify` | Assert template is visible | `template`, `timeout` |
| `repeat` | Repeat previous N steps | `count`, `times` |

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
WOS/
├── app.py           # FastAPI server + API routes
├── device.py        # WindowDevice / ADBDevice abstraction
├── engine.py        # Task loader + step executor
├── vision.py        # OpenCV template matching
├── adb.py           # Legacy ADB wrapper
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
- Use `confidence: 0.7` for fuzzy matches, `0.9` for exact matches
- Crop templates tightly around the target element for best results
- The bot waits 0.3s between steps automatically
