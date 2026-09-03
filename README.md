# Tapo Studio

A lightweight, low-latency WebRTC streaming and PTZ motor control dashboard for TP-Link Tapo pan/tilt cameras (such as the Tapo C200, C206, and C210).

Tapo Studio bypasses cloud services to provide direct local streaming, real-time motorized directional control, instant frame captures, and client-side computer vision detection without taxing the host CPU.

---

## Architecture Overview

1. **Streaming Backend (go2rtc)**
   - Connects directly to the local RTSP feed of the camera.
   - Forwards raw H.264 video streams directly to browser WebRTC without CPU transcoding.
   - Achieves sub-200ms glass-to-glass latency with minimal memory footprint (under 30 MB RAM).

2. **Control & Telemetry Daemon (FastAPI)**
   - Interfaces with the camera over the ONVIF protocol (Profile S on port 2020).
   - Exposes REST endpoints for relative pan/tilt translation, 180-degree flips, absolute position queries, and device telemetry.
   - Manages snapshot persistence and local file serving.

3. **Web Frontend (Vanilla JS & WebGL)**
   - Responsive single-page application optimized for desktop and mobile browsers.
   - Interactive directional D-pad, keyboard arrow bindings, and sensitivity sliders.
   - In-browser object and person detection powered by TensorFlow.js and MobileNet-v2 running on the client GPU via WebGL.
   - Closed-loop visual auto-tracking servo that automatically centers the primary detected person in the frame.

---

## Features

- **Sub-Second WebRTC Streaming**: Instant live feed with audio toggle, fullscreen support, and HD (1080p/2K) versus SD (360p) stream switching.
- **PTZ Directional Control**: On-screen directional buttons, smooth step-size adjustments, and keyboard arrow key navigation.
- **180-Degree Quick Flip**: Instant one-click motor inversion to check behind the camera.
- **Client-Side AI Object Detection**: Real-time bounding boxes and confidence readouts for 80 COCO classes, executed entirely on the client browser GPU.
- **Visual Auto-Tracking Servo**: Automatically detects the closest person in the frame and drives PTZ motor steps to keep the subject centered.
- **Snapshot Capture & Gallery**: Instant capture from the RTSP stream saved to disk with thumbnail previews, full-size viewing, and deletion management.
- **Hardware Telemetry**: Displays camera model, firmware version, and real-time pan/tilt vector coordinates.
- **Low Overhead**: Operates entirely within ~100 MB of system RAM on low-power host devices (including ARM single-board computers and Android chroot environments).

---

## Prerequisites

- Linux host (x86_64 or aarch64/ARM64)
- Python 3.10+
- Network connectivity to the Tapo camera on your local subnet
- Local camera account enabled via the official Tapo mobile application (`Device Settings -> Advanced Settings -> Camera Account`)

---

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/Hy4ri/tapo-web.git
   cd tapo-web
   ```

2. Run the setup script:
   ```bash
   ./setup.sh
   ```

3. Configure your camera credentials:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and set your camera IP and local credentials:
   ```ini
   CAM_IP=192.168.1.53
   CAM_PORT=2020
   CAM_USER=your_camera_user
   CAM_PASS=your_camera_password
   PORT=8555
   GO2RTC_API=http://127.0.0.1:1984
   ```

4. Configure streaming endpoints in `config/go2rtc.yaml`:
   ```yaml
   streams:
     tapo_hd:
       - rtsp://your_camera_user:your_camera_password@192.168.1.53:554/stream1
     tapo_sd:
       - rtsp://your_camera_user:your_camera_password@192.168.1.53:554/stream2

   api:
     listen: "127.0.0.1:1984"

   webrtc:
     listen: ":8556"
   ```

---

## Running the Application

### Method 1: Foreground Execution

Start the streaming backend:
```bash
./bin/go2rtc -config config/go2rtc.yaml &
```

Start the web application:
```bash
./start.sh
```

Navigate to `http://<host-ip>:8555` in any modern web browser.

### Process Management (Unified PM2 Service)

Tapo Studio manages the `go2rtc` streaming daemon automatically as an internal child process. You only need to run a single PM2 process:

```bash
# Start the unified Tapo Studio service
pm2 start ./start.sh --name tapo-studio

# Save PM2 state across reboots
pm2 save
```

---

## On-Device Hardware Tracking

The Tapo C206 camera includes a dedicated internal digital signal processor (DSP) capable of autonomous motion tracking at 30 FPS without host or browser CPU usage.

To enable autonomous tracking directly on the camera hardware:
1. Open the official Tapo mobile application.
2. Navigate to `Device Settings (gear icon) -> Detection & Alerts`.
3. Enable `Person Detection` and `Motion Tracking`.

Once enabled, the camera's internal microcontroller will autonomously steer the physical pan/tilt motors to follow moving subjects in real time. The live WebRTC stream will reflect the camera movements with zero browser latency.

---

## API Reference

### Camera Telemetry

- **GET `/api/status`**
  Returns online status, camera model, manufacturer, firmware version, and current pan/tilt coordinates.

### PTZ Motor Control

- **POST `/api/ptz/move`**
  Translates the camera position by a relative step.
  ```json
  {
    "direction": "up" | "down" | "left" | "right",
    "step": 0.2
  }
  ```

- **POST `/api/ptz/flip180`**
  Rotates the pan axis 180 degrees based on the current position limit.

- **POST `/api/ptz/absolute`**
  Moves the motor to normalized absolute coordinates.
  ```json
  {
    "pan": 0.0,
    "tilt": 0.0
  }
  ```

### Night Vision and Lighting

- **POST `/api/light/night_mode`**
  Switches the infrared night vision mode.
  ```json
  {
    "mode": "AUTO" | "ON" | "OFF"
  }
  ```

- **POST `/api/light/spotlight`**
  Toggles the physical white LED spotlight on or off.
  ```json
  {
    "state": true | false
  }
  ```

### Snapshots

- **POST `/api/snapshot?src=tapo_hd`**
  Grabs an uncompressed frame from the stream and saves it to the local snapshot gallery.

- **GET `/api/snapshots`**
  Returns a list of saved snapshot metadata (filenames, timestamps, and sizes).

- **DELETE `/api/snapshots/{filename}`**
  Removes a snapshot from the server.

### WebRTC Proxy

- **POST `/api/webrtc?src=tapo_hd`**
  Proxies SDP offer/answer exchanges to the underlying streaming daemon.

- **GET `/api/frame.jpeg?src=tapo_hd`**
  Returns the current frame directly as an image response.

---

## Keyboard Controls

| Key | Action |
| --- | --- |
| Arrow Up | Tilt camera up |
| Arrow Down | Tilt camera down |
| Arrow Left | Pan camera left |
| Arrow Right | Pan camera right |
| Spacebar | Capture high-resolution snapshot |

---

## License

MIT License. See `LICENSE` for details.
