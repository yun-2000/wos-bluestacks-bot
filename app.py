import asyncio
import json
import time
import base64
import threading
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from device import DeviceManager
from vision import list_templates, save_template, crop_from_screenshot
import json as jsonlib
from engine import load_all_tasks, load_task, save_task, delete_task, run_task, TASKS_DIR

ROOT = Path(__file__).parent
dm = DeviceManager()

ws_clients: list[WebSocket] = []
task_status: dict[str, dict] = {}
running_task: str | None = None
stop_event = threading.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop
    loop = asyncio.get_running_loop()
    TASKS_DIR.mkdir(exist_ok=True)
    (ROOT / "templates").mkdir(exist_ok=True)
    (ROOT / "screenshots").mkdir(exist_ok=True)
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.mount("/template_images", StaticFiles(directory=ROOT / "templates"), name="template_images")
app.mount("/screenshots", StaticFiles(directory=ROOT / "screenshots"), name="screenshots")
views = Jinja2Templates(directory=ROOT / "views")


def broadcast_log(level: str, msg: str):
    ts = time.strftime("%H:%M:%S")
    data = json.dumps({"time": ts, "level": level, "msg": msg})
    for ws in ws_clients[:]:
        try:
            asyncio.run_coroutine_threadsafe(ws.send_text(data), loop)
        except Exception:
            pass


loop: asyncio.AbstractEventLoop = None


@app.head("/")
async def health():
    return Response(status_code=200)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    tasks = load_all_tasks()
    devices = dm.list_devices()
    current = dm.get_current()
    current_name = current.info().name if current else "No device"
    return views.TemplateResponse("dashboard.html", {
        "request": request,
        "tasks": tasks,
        "devices": devices,
        "current_device": current_name,
        "current_device_connected": current is not None,
        "task_status": task_status,
        "running_task": running_task,
        "templates": list_templates(),
    })


@app.post("/device")
async def set_device(device_id: str = Form(...)):
    dm.set_current(device_id)
    return RedirectResponse("/", status_code=303)


@app.post("/device/connect-gpg")
async def connect_gpg():
    try:
        result = dm.connect_adb()
        return JSONResponse({"status": "ok", "message": result})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/devices")
async def list_devices_endpoint():
    devices = dm.list_devices()
    return JSONResponse([{"id": d.id, "name": d.name, "type": d.type} for d in devices])


@app.post("/tasks/{filename}/run")
async def run_task_endpoint(filename: str):
    global running_task
    if running_task:
        return JSONResponse({"status": "error", "message": f"Task already running: {running_task}"}, status_code=409)

    dev = dm.get_current()
    if not dev:
        return JSONResponse({"status": "error", "message": "No device connected"}, status_code=400)

    task = load_task(TASKS_DIR / filename)
    running_task = filename
    stop_event.clear()
    task_status[filename] = {"status": "running", "started": time.strftime("%H:%M:%S")}

    def worker():
        global running_task
        try:
            run_task(task, dev, log=broadcast_log, stop_flag=stop_event.is_set)
            task_status[filename] = {"status": "done", "finished": time.strftime("%H:%M:%S")}
        except Exception as e:
            broadcast_log("error", str(e))
            task_status[filename] = {"status": "error", "error": str(e)}
        finally:
            running_task = None

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse({"status": "ok"})


@app.post("/tasks/stop")
async def stop_task():
    stop_event.set()
    return JSONResponse({"status": "ok"})


@app.get("/tasks/{filename}/edit", response_class=HTMLResponse)
async def edit_task(request: Request, filename: str):
    task = load_task(TASKS_DIR / filename)
    steps_json = jsonlib.dumps([s.to_dict() for s in task.steps])
    return views.TemplateResponse("task_editor.html", {
        "request": request,
        "task": task,
        "filename": filename,
        "templates": list_templates(),
        "task_files": [p.name for p in sorted(TASKS_DIR.glob("*.yaml"))],
        "is_new": False,
        "steps_json": steps_json,
    })


@app.get("/tasks/new", response_class=HTMLResponse)
async def new_task(request: Request):
    return views.TemplateResponse("task_editor.html", {
        "request": request,
        "task": None,
        "filename": "",
        "templates": list_templates(),
        "task_files": [p.name for p in sorted(TASKS_DIR.glob("*.yaml"))],
        "is_new": True,
    })


@app.post("/tasks/{filename}/save")
async def save_task_endpoint(filename: str, request: Request):
    data = await request.json()
    save_task(data, filename)
    return JSONResponse({"status": "ok"})


@app.post("/tasks/create")
async def create_task_endpoint(request: Request):
    data = await request.json()
    filename = data.get("filename", "").strip()
    if not filename:
        filename = data.get("name", "task").lower().replace(" ", "_") + ".yaml"
    if not filename.endswith(".yaml"):
        filename += ".yaml"
    save_task(data, filename)
    return JSONResponse({"status": "ok", "filename": filename})


@app.delete("/tasks/{filename}")
async def delete_task_endpoint(filename: str):
    delete_task(filename)
    return JSONResponse({"status": "ok"})


@app.get("/template-manager", response_class=HTMLResponse)
async def template_manager(request: Request):
    current = dm.get_current()
    return views.TemplateResponse("template_mgr.html", {
        "request": request,
        "templates": list_templates(),
        "current_device": current.info().name if current else "No device",
    })


@app.post("/screenshot")
async def take_screenshot():
    dev = dm.get_current()
    if not dev:
        return JSONResponse({"status": "error", "message": "No device"}, status_code=400)
    try:
        png = dev.screencap_png()
        b64 = base64.b64encode(png).decode()
        return JSONResponse({"status": "ok", "image": b64, "size": len(png)})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/template/crop")
async def crop_template(request: Request):
    data = await request.json()
    png_b64 = data["image"]
    png_bytes = base64.b64decode(png_b64)
    x, y, w, h = int(data["x"]), int(data["y"]), int(data["w"]), int(data["h"])
    name = data["name"].strip()
    if not name.endswith(".png"):
        name += ".png"
    cropped = crop_from_screenshot(png_bytes, x, y, w, h)
    save_template(name, cropped)
    return JSONResponse({"status": "ok", "name": name})


@app.post("/template/upload")
async def upload_template(file: UploadFile = File(...)):
    content = await file.read()
    name = file.filename or "uploaded.png"
    save_template(name, content)
    return JSONResponse({"status": "ok", "name": name})


@app.delete("/template/{name}")
async def delete_template(name: str):
    p = ROOT / "templates" / name
    if p.exists():
        p.unlink()
    return JSONResponse({"status": "ok"})


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.remove(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000)
