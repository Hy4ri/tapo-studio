import os
import time
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Body, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from onvif import ONVIFCamera

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
SNAPSHOTS_DIR = BASE_DIR / "snapshots"
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Load .env if present
env_file = BASE_DIR / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

GO2RTC_API = os.getenv("GO2RTC_API", "http://127.0.0.1:1984")

# Camera Settings
CAM_IP = os.getenv("CAM_IP", "192.168.1.53")
CAM_PORT = int(os.getenv("CAM_PORT", "2020"))
CAM_USER = os.getenv("CAM_USER", "")
CAM_PASS = os.getenv("CAM_PASS", "")

app = FastAPI(title="Tapo Web View", description="Lightweight WebRTC & PTZ Controller for Tapo C206")

# Global ONVIF cache
_cam = None
_ptz = None
_media = None
_token = None
_cam_lock = asyncio.Lock()

def get_ptz_service():
    global _cam, _ptz, _media, _token
    if _ptz is None:
        _cam = ONVIFCamera(CAM_IP, CAM_PORT, CAM_USER, CAM_PASS)
        _ptz = _cam.create_ptz_service()
        _media = _cam.create_media_service()
        _token = _media.GetProfiles()[0].token
    return _ptz, _token

def get_dev_info():
    global _cam
    if _cam is None:
        get_ptz_service()
    return _cam.devicemgmt.GetDeviceInformation()

class MoveRequest(BaseModel):
    direction: str  # up, down, left, right, home
    step: Optional[float] = 0.2

@app.get("/api/status")
async def get_status():
    async with _cam_lock:
        try:
            ptz, token = await asyncio.to_thread(get_ptz_service)
            status = await asyncio.to_thread(ptz.GetStatus, {'ProfileToken': token})
            dev = await asyncio.to_thread(get_dev_info)
            pos = status.Position.PanTilt if status and status.Position else None
            return {
                "online": True,
                "model": getattr(dev, "Model", "Tapo C206"),
                "manufacturer": getattr(dev, "Manufacturer", "TP-Link"),
                "firmware": getattr(dev, "FirmwareVersion", "Unknown"),
                "ip": CAM_IP,
                "position": {
                    "pan": round(pos.x, 3) if pos else 0.0,
                    "tilt": round(pos.y, 3) if pos else 0.0
                }
            }
        except Exception as e:
            return {"online": False, "error": str(e)}

@app.post("/api/ptz/move")
async def ptz_move(req: MoveRequest):
    async with _cam_lock:
        try:
            ptz, token = await asyncio.to_thread(get_ptz_service)
            dx, dy = 0.0, 0.0
            s = max(0.05, min(1.0, req.step or 0.2))

            if req.direction == "left":
                dx = s
            elif req.direction == "right":
                dx = -s
            elif req.direction == "up":
                dy = s
            elif req.direction == "down":
                dy = -s
            else:
                raise HTTPException(status_code=400, detail="Invalid direction")

            def do_move():
                m_req = ptz.create_type('RelativeMove')
                m_req.ProfileToken = token
                m_req.Translation = {'PanTilt': {'x': dx, 'y': dy}}
                ptz.RelativeMove(m_req)

            await asyncio.to_thread(do_move)
            return {"success": True, "moved": req.direction, "step": s}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ptz/flip180")
async def ptz_flip180():
    async with _cam_lock:
        try:
            ptz, token = await asyncio.to_thread(get_ptz_service)
            def do_flip():
                status = ptz.GetStatus({'ProfileToken': token})
                cur_x = status.Position.PanTilt.x if status.Position and status.Position.PanTilt else 0.0
                # Pan range is [-1.0, 1.0]. If cur_x > 0 flip back (-1.0), else flip forward (+1.0)
                flip_delta = -1.0 if cur_x > 0 else 1.0

                m_req = ptz.create_type('RelativeMove')
                m_req.ProfileToken = token
                m_req.Translation = {'PanTilt': {'x': flip_delta, 'y': 0.0}}
                ptz.RelativeMove(m_req)

            await asyncio.to_thread(do_flip)
            return {"success": True, "action": "flip180"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ptz/absolute")
async def ptz_absolute(pan: float = Body(..., embed=True), tilt: float = Body(..., embed=True)):
    async with _cam_lock:
        try:
            ptz, token = await asyncio.to_thread(get_ptz_service)
            def do_abs():
                m_req = ptz.create_type('AbsoluteMove')
                m_req.ProfileToken = token
                m_req.Position = {'PanTilt': {'x': max(-1.0, min(1.0, pan)), 'y': max(-1.0, min(1.0, tilt))}}
                ptz.AbsoluteMove(m_req)

            await asyncio.to_thread(do_abs)
            return {"success": True, "pan": pan, "tilt": tilt}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/light/night_mode")
async def set_night_mode(mode: str = Body(..., embed=True)):
    mode_upper = mode.upper()
    if mode_upper not in ["AUTO", "ON", "OFF"]:
        raise HTTPException(status_code=400, detail="Mode must be AUTO, ON, or OFF")
    async with _cam_lock:
        try:
            if _cam is None:
                get_ptz_service()
            imaging = _cam.create_imaging_service()
            media = _cam.create_media_service()
            token = media.GetProfiles()[0].VideoSourceConfiguration.SourceToken
            def do_set():
                req = imaging.create_type('SetImagingSettings')
                req.VideoSourceToken = token
                req.ImagingSettings = {'IrCutFilter': mode_upper}
                req.ForcePersistence = True
                imaging.SetImagingSettings(req)
            await asyncio.to_thread(do_set)
            return {"success": True, "night_mode": mode_upper}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/light/spotlight")
async def set_spotlight(state: bool = Body(..., embed=True)):
    # Flashlight control via TP-Link API
    async with _cam_lock:
        try:
            import pytapo
            cloud_user = os.getenv("CLOUD_USER", CAM_USER)
            cloud_pass = os.getenv("CLOUD_PASS", CAM_PASS)
            def do_spotlight():
                t = pytapo.Tapo(CAM_IP, cloud_user, cloud_pass)
                return t.setForceWhitelampState(state)
            res = await asyncio.to_thread(do_spotlight)
            return {"success": True, "spotlight": state, "result": res}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Spotlight API: {e}")

# --- SNAPSHOTS ---
@app.post("/api/snapshot")
async def take_snapshot(src: str = Query("tapo_hd")):
    try:
        url = f"{GO2RTC_API}/api/frame.jpeg?src={src}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Failed to capture frame from streaming backend")
            
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"snap_{ts}.jpg"
            file_path = SNAPSHOTS_DIR / filename
            file_path.write_bytes(resp.content)

            return {
                "success": True,
                "filename": filename,
                "url": f"/snapshots/{filename}",
                "timestamp": ts,
                "size": len(resp.content)
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/snapshots")
async def list_snapshots():
    items = []
    for p in sorted(SNAPSHOTS_DIR.glob("*.jpg"), key=os.path.getmtime, reverse=True):
        items.append({
            "filename": p.name,
            "url": f"/snapshots/{p.name}",
            "size": p.stat().st_size,
            "created": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        })
    return {"snapshots": items}

@app.delete("/api/snapshots/{filename}")
async def delete_snapshot(filename: str):
    file_path = SNAPSHOTS_DIR / filename
    if file_path.exists() and file_path.is_file():
        file_path.unlink()
        return {"success": True, "deleted": filename}
    raise HTTPException(status_code=404, detail="Snapshot not found")

# --- PROXY TO GO2RTC WEBRTC / STREAMS ---
@app.post("/api/webrtc")
async def proxy_webrtc(src: str = Query("tapo_hd"), offer: str = Body(..., media_type="text/plain")):
    try:
        url = f"{GO2RTC_API}/api/webrtc?src={src}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, content=offer, headers={"Content-Type": "text/plain"})
            return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type", "text/plain"))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WebRTC proxy error: {e}")

@app.get("/api/frame.jpeg")
async def proxy_frame(src: str = Query("tapo_hd")):
    try:
        url = f"{GO2RTC_API}/api/frame.jpeg?src={src}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            return Response(content=resp.content, status_code=resp.status_code, media_type="image/jpeg")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

# Mount static files and snapshot storage
app.mount("/snapshots", StaticFiles(directory=str(SNAPSHOTS_DIR)), name="snapshots")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
async def root():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Tapo Web API online. Add index.html to /opt/tapo-web/static/"}
