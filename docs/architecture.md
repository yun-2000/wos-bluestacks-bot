# WOS Bot Architecture

## System Overview

```mermaid
graph TB
    subgraph "Browser UI"
        Dashboard["Dashboard<br/>Task list, logs, device selector"]
        Editor["Task Editor<br/>Step builder"]
        TplMgr["Template Manager<br/>Screenshot cropper"]
    end

    subgraph "FastAPI Server (app.py)"
        API["REST API<br/>Task CRUD, device control"]
        WS["WebSocket /ws/logs<br/>Real-time broadcast"]
        Static["Static files<br/>CSS, template images"]
    end

    subgraph "Core Engine"
        Engine["Task Runner (engine.py)<br/>Step executor, retry logic"]
        Vision["Vision (vision.py)<br/>OpenCV template matching"]
        TaskYAML["YAML Tasks<br/>tasks/*.yaml"]
    end

    subgraph "Device Layer (device.py)"
        DM["DeviceManager<br/>Device enumeration"]
        WinDev["WindowDevice<br/>Win32 GDI capture<br/>pyautogui clicks"]
        ADBDev["ADBDevice<br/>adb shell commands"]
    end

    subgraph "Targets"
        GPG["Google Play Games<br/>Windows emulator"]
        Phone["Android Phone<br/>USB / WiFi ADB"]
    end

    Dashboard --> API
    Editor --> API
    TplMgr --> API
    Dashboard -.->|WebSocket| WS

    API --> Engine
    API --> Vision
    API --> DM

    Engine --> Vision
    Engine --> TaskYAML
    Engine -.->|log callbacks| WS

    DM --> WinDev
    DM --> ADBDev

    WinDev -->|Win32 API| GPG
    ADBDev -->|adb protocol| Phone
```

## Task Execution Flow

```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant A as FastAPI
    participant E as Engine
    participant V as Vision
    participant D as Device

    U->>A: POST /tasks/{file}/run
    A->>A: Spawn worker thread
    A-->>U: 200 OK

    loop Each Step
        E->>D: screencap()
        D-->>E: PNG bytes

        alt find_and_tap
            E->>V: find_template(png, template)
            V-->>E: MatchResult (x, y)
            E->>D: tap(x, y)
        else tap
            E->>D: tap(x, y)
        else wait
            E->>E: sleep(seconds)
        else tap_back
            E->>D: press_back()
        end

        E-->>A: broadcast_log(level, msg)
        A-->>U: WebSocket push
    end

    E-->>A: Task complete
    A-->>U: WebSocket: "Task completed"
```

## Device Abstraction

```mermaid
classDiagram
    class BaseDevice {
        <<abstract>>
        +screencap() bytes
        +tap(x, y)
        +swipe(x1, y1, x2, y2, duration_ms)
        +press_back()
        +info() DeviceInfo
    }

    class WindowDevice {
        -title_sub: str
        -_hwnd: int
        -_find_window() int
        -_get_client_rect() tuple
        +screencap() bytes
        +tap(x, y)
        +swipe(x1, y1, x2, y2, duration_ms)
        +press_back()
    }

    class ADBDevice {
        -serial: str
        -adb: str
        -_run(args) str
        +screencap() bytes
        +tap(x, y)
        +swipe(x1, y1, x2, y2, duration_ms)
        +press_back()
    }

    class DeviceManager {
        -adb_path: str
        -_current: BaseDevice
        +list_devices() list
        +get_device(id) BaseDevice
        +get_current() BaseDevice
        +set_current(id)
        +connect_adb(host, port)
    }

    class DeviceInfo {
        +id: str
        +name: str
        +type: str
        +connected: bool
    }

    BaseDevice <|-- WindowDevice
    BaseDevice <|-- ADBDevice
    DeviceManager --> BaseDevice
    BaseDevice --> DeviceInfo
```

## Template Matching Pipeline

```mermaid
flowchart LR
    A[Screenshot PNG] --> B[Decode to CV2 BGR]
    C[Template PNG] --> D[Load from templates/]
    B --> E[cv2.matchTemplate<br/>TM_CCOEFF_NORMED]
    D --> E
    E --> F{confidence >= threshold?}
    F -->|Yes| G[Return MatchResult<br/>x, y, w, h, confidence]
    F -->|No| H[Return None]
    H --> I{retries left?}
    I -->|Yes| A
    I -->|No| J[Step fails or skipped]
```

## Data Storage

```
WOS/
├── tasks/              YAML task definitions (user-created)
│   ├── daily_rewards.yaml
│   ├── exploration.yaml
│   └── mail_reward.yaml
├── templates/          PNG images for template matching
│   ├── claim-all_button.png
│   ├── mail_button.png
│   └── tab-exit_button.png
└── screenshots/        Debug captures (auto-generated)
```

No database — all state is file-based. Task status lives in memory during server runtime.
