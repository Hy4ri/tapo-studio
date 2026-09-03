import os
import time
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Body, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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

app = FastAPI(title="Tapo Studio", description="Full-feature WebRTC, PTZ & Camera Hardware Controller for Tapo C206")

# Global ONVIF cache
_cam = None
_ptz = None
_media = None
_token = None
_tapo = None
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

def get_tapo_client():
    global _tapo
    if _tapo is None:
        import pytapo
        _tapo = pytapo.Tapo(CAM_IP, CAM_USER, CAM_PASS)
    return _tapo


class MoveRequest(BaseModel):
    direction: str  # up, down, left, right
    step: Optional[float] = 0.2


# --- TELEMETRY & STATUS ---
@app.get("/api/status")
async def get_status():
    async with _cam_lock:
        try:
            ptz, token = await asyncio.to_thread(get_ptz_service)
            status = await asyncio.to_thread(ptz.GetStatus, {'ProfileToken': token})
            dev = await asyncio.to_thread(get_dev_info)
            pos = status.Position.PanTilt if status and status.Position else None

            # Fetch Tapo hardware features
            def fetch_tapo_features():
                t = get_tapo_client()
                feat = {}
                try:
                    at = t.getAutoTrackTarget()
                    feat["auto_track"] = (at.get("enabled") == "on")
                except Exception:
                    feat["auto_track"] = False

                try:
                    pm = t.getPrivacyMode()
                    feat["privacy_mode"] = (pm.get("enabled") == "on")
                except Exception:
                    feat["privacy_mode"] = False

                try:
                    led = t.getLED()
                    feat["led"] = (led.get("enabled") == "on")
                except Exception:
                    feat["led"] = True

                try:
                    md = t.getMotionDetection()
                    feat["motion_detection"] = (md.get("enabled") == "on")
                except Exception:
                    feat["motion_detection"] = False

                try:
                    pd = t.getPersonDetection()
                    feat["person_detection"] = (pd.get("enabled") == "on")
                except Exception:
                    feat["person_detection"] = False

                try:
                    nv = t.getNightVisionModeConfig()
                    mode = nv.get("image", {}).get("switch", {}).get("night_vision_mode", "inf_night_vision")
                    feat["night_vision_mode"] = mode
                    feat["spotlight"] = (mode == "wtl_night_vision")
                except Exception:
                    feat["night_vision_mode"] = "inf_night_vision"
                    feat["spotlight"] = False

                try:
                    wl = t.getWhitelampConfig()
                    feat["spotlight_intensity"] = int(wl.get("wtl_intensity_level", 3))
                except Exception:
                    feat["spotlight_intensity"] = 3

                return feat

            features = await asyncio.to_thread(fetch_tapo_features)

            return {
                "online": True,
                "model": getattr(dev, "Model", "Tapo C206"),
                "manufacturer": getattr(dev, "Manufacturer", "TP-Link"),
                "firmware": getattr(dev, "FirmwareVersion", "Unknown"),
                "ip": CAM_IP,
                "position": {
                    "pan": round(pos.x, 3) if pos else 0.0,
                    "tilt": round(pos.y, 3) if pos else 0.0
                },
                "features": features
            }
        except Exception as e:
            return {"online": False, "error": str(e)}


# --- PTZ CONTROLS ---
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


# --- HARDWARE FEATURES (AUTOTRACK, SPOTLIGHT, NIGHT VISION, PRIVACY, LED) ---

@app.post("/api/features/auto_track")
async def toggle_auto_track(enabled: bool = Body(..., embed=True)):
    try:
        t = get_tapo_client()
        res = await asyncio.to_thread(t.setAutoTrackTarget, enabled)
        return {"success": True, "auto_track": enabled, "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AutoTrack error: {e}")


@app.post("/api/features/night_vision")
async def set_night_vision_mode(mode: str = Body(..., embed=True)):
    # mode: 'infrared' -> inf_night_vision, 'spotlight' -> wtl_night_vision, 'smart' -> md_night_vision
    mode_map = {
        "infrared": "inf_night_vision",
        "spotlight": "wtl_night_vision",
        "smart": "md_night_vision",
        "inf_night_vision": "inf_night_vision",
        "wtl_night_vision": "wtl_night_vision",
        "md_night_vision": "md_night_vision"
    }
    target = mode_map.get(mode.lower())
    if not target:
        raise HTTPException(status_code=400, detail="Invalid mode. Choose infrared, spotlight, or smart.")

    try:
        t = get_tapo_client()
        res = await asyncio.to_thread(t.setNightVisionModeConfig, target)
        return {"success": True, "night_vision_mode": target, "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Night vision error: {e}")


@app.post("/api/features/spotlight")
async def toggle_spotlight(enabled: bool = Body(..., embed=True)):
    try:
        t = get_tapo_client()
        target = "wtl_night_vision" if enabled else "inf_night_vision"
        res = await asyncio.to_thread(t.setNightVisionModeConfig, target)
        return {"success": True, "spotlight": enabled, "night_vision_mode": target, "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Spotlight error: {e}")


@app.post("/api/features/spotlight/intensity")
async def set_spotlight_intensity(level: int = Body(..., embed=True)):
    level = max(1, min(5, level))
    try:
        t = get_tapo_client()
        res = await asyncio.to_thread(t.setWhitelampConfig, intensityLevel=level)
        return {"success": True, "intensity": level, "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Spotlight intensity error: {e}")


@app.post("/api/features/privacy")
async def toggle_privacy(enabled: bool = Body(..., embed=True)):
    try:
        t = get_tapo_client()
        res = await asyncio.to_thread(t.setPrivacyMode, enabled)
        return {"success": True, "privacy_mode": enabled, "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Privacy mode error: {e}")


@app.post("/api/features/led")
async def toggle_led(enabled: bool = Body(..., embed=True)):
    try:
        t = get_tapo_client()
        res = await asyncio.to_thread(t.setLEDEnabled, enabled)
        return {"success": True, "led": enabled, "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LED error: {e}")


@app.post("/api/features/person_detection")
async def toggle_person_detection(enabled: bool = Body(..., embed=True)):
    try:
        t = get_tapo_client()
        res = await asyncio.to_thread(t.setPersonDetection, enabled)
        return {"success": True, "person_detection": enabled, "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Person detection error: {e}")


@app.post("/api/features/motion_detection")
async def toggle_motion_detection(enabled: bool = Body(..., embed=True)):
    try:
        t = get_tapo_client()
        res = await asyncio.to_thread(t.setMotionDetection, enabled)
        return {"success": True, "motion_detection": enabled, "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Motion detection error: {e}")


@app.post("/api/features/calibrate")
async def calibrate_motor():
    try:
        t = get_tapo_client()
        res = await asyncio.to_thread(t.calibrateMotor)
        return {"success": True, "action": "calibrate", "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Calibration error: {e}")


@app.post("/api/features/reboot")
async def reboot_camera():
    try:
        t = get_tapo_client()
        res = await asyncio.to_thread(t.reboot)
        return {"success": True, "action": "reboot", "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reboot error: {e}")


# Legacy backwards compatibility
@app.post("/api/light/night_mode")
async def legacy_night_mode(mode: str = Body(..., embed=True)):
    m = mode.lower()
    if m in ["auto", "smart"]:
        return await set_night_vision_mode("smart")
    elif m in ["on", "infrared", "ir"]:
        return await set_night_vision_mode("infrared")
    else:
        return await set_night_vision_mode("spotlight")

@app.post("/api/light/spotlight")
async def legacy_spotlight(state: bool = Body(..., embed=True)):
    return await toggle_spotlight(state)


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
