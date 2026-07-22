# NadiVision

Clinical-grade visual acuity screening device running on Raspberry Pi 4.  
Tumbling E optotype · ETDRS 14-line scoring · HC-SR04 distance · MediaPipe face/eye detection.

> **Not a medical device.** Results require review by a Registered Medical Practitioner before any clinical use.

---

## Architecture

```
nadivision-camera.service   (system Python + Picamera2)
  → shared memory ring (3 × 320×240 RGB slots)
  → Unix-domain socket  →  nadivision-app.service  (uv Python 3.11 + MediaPipe)
  → HTTP MJPEG :8766    →  kiosk frontend

nadivision-app.service
  → WebSocket :8765  ↔  kiosk frontend
  → HC-SR04 @ 17 Hz
  → IntegrityMonitor (face loss · fellow-eye · distance · gaze)
  → AcuitySession (scoring · report)
```

## Quick Start on Pi

See [PI_SETUP.md](PI_SETUP.md) for full installation and configuration steps.

## Project Structure

```
backend/
  main.py                 App service entry point
  config.py               Queue sizes + WS constants
  scoring/
    constants.py          All tunable constants (logMAR, EAR, debounce…)
    engine.py             AcuitySession — 14-line ETDRS scoring
  integrity/
    monitor.py            IntegrityMonitor state machine
  sensors/
    camera.py             SharedMemoryCameraConsumer (no OpenCV)
    ultrasonic.py         HC-SR04 Median(3)+EMA(0.7) worker
  vision/
    face_detection.py     FaceDetection 6 Hz + FaceMesh EAR 3 Hz
  server/
    ws_server.py          Session protocol WebSocket server
  report/
    generator.py          Report builder with per-level scores

scripts/
  camera_service.py       System-Python camera service (Picamera2 + shared mem)
  benchmark_face_models.py
  perf_validate.py

frontend/src/
  app/page.tsx            Screen router (landing → camera-setup → test → results)
  components/screens/     LandingScreen · CameraSetupScreen · TestScreen · ResultsScreen
  lib/
    hardware-ws.ts        Session-protocol WebSocket client
    store.ts              Zustand state (calibration, session, result)
    types.ts              Shared TypeScript types
    optotype.ts           E-height math (mm → px)
    screen-calibration.ts Auto-detect display PPI

tests/                    29 pytest tests (scoring · integrity · report)
```

## Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Face detector | MediaPipe FaceDetection | 13.8 ms avg vs YuNet 33.5 ms on Pi 4 |
| Eye-open detection | MediaPipe FaceMesh EAR | Eyelid landmarks, no extra model |
| Camera ↔ app IPC | POSIX shared memory + Unix socket | Zero pixel-copy, cross-interpreter |
| Distance source | HC-SR04 only | No browser estimates ever reach Pi session |
| Scoring owner | Backend (AcuitySession) | Frontend display-only, no answer key |
| Optotype | Tumbling E | Sole clinical optotype for this build |

## Tests

```bash
cd nadi_vision
python3 -m pytest tests/ -v
```

29 tests cover: scoring termination · 14-line protocol · fellow-eye holds ·
distance holds · integrity debounce · report generation.

## Environment Variables (camera_service.py)

| Variable | Default | Description |
|---|---|---|
| `NADI_INFER_W` | `320` | Inference frame width |
| `NADI_INFER_H` | `240` | Inference frame height |
| `NADI_PREVIEW_W` | `1280` | Preview frame width |
| `NADI_PREVIEW_H` | `720` | Preview frame height |
| `NADI_CAM_FPS` | `30` | Camera capture FPS |
| `NADI_INFER_SKIP` | `5` | Publish every Nth frame (30/5 = 6 Hz) |
| `NADI_PREVIEW_SKIP` | `4` | Preview JPEG every Nth frame (~7.5 fps) |
| `NADI_UDS_PATH` | `/run/nadivision/frames.sock` | Unix socket path |
| `NADI_MJPEG_PORT` | `8766` | MJPEG HTTP server port |
| `NADI_JPEG_QUALITY` | `65` | Preview JPEG quality |

python3 perf_validate.py --benchmark-json real_benchmark_results.json
```

## Remaining Verification on Target Pi
- Run full OD/OS flow with backend + frontend together.
- Capture CPU/memory headroom during a full session.
- Confirm no OOM and stable frame processing under kiosk load.

## Pi 4 Quick Verification

1. Verify Python environments:
```bash
/usr/bin/python3 -c "import libcamera, picamera2; print('system camera stack OK')"
python -c "import mediapipe, cv2; print('venv mediapipe stack OK')"
```

2. Verify backend websocket is live:
```bash
python - <<'PY'
import asyncio, json, websockets

async def main():
	async with websockets.connect('ws://127.0.0.1:8765') as ws:
		msg = await ws.recv()
		payload = json.loads(msg)
		print('WS OK', sorted(payload.keys()))

asyncio.run(main())
PY
```
