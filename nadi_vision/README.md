# NadiVision

Visual acuity screening stack for Raspberry Pi 4.
Current build uses backend-owned scoring/integrity with a split-interpreter camera architecture.

Not a medical device. Results must be reviewed by a qualified clinician.

## Architecture

```
nadivision-camera.service (system Python + Picamera2)
  -> shared memory ring (3 x 320x240 RGB)
  -> Unix domain socket metadata (/run/nadivision/frames.sock)
  -> MJPEG preview HTTP (:8766)

nadivision-app.service (uv Python 3.11)
  -> HC-SR04 distance worker (~17 Hz)
  -> MediaPipe FaceDetection (6 Hz)
  -> MediaPipe Hands (3 Hz, OD/OS test phase only)
  -> IntegrityMonitor (warn + pause tiers)
  -> AcuitySession scoring engine
  -> WebSocket session server (:8765)

frontend (Next.js)
  -> display-only test UI (no scoring logic)
  -> consumes session.state and report.ready
```

## Current Behavior

- Distance is ultrasonic-only.
- Distance filtering is range gate -> MAD outlier rejection -> EMA smoothing.
- Optotype size is fixed per trial using trial-start distance to avoid mid-trial size jitter.
- Fellow-eye enforcement uses fused hand-eye IoU + sclera suppression with hysteresis.
- Soft warning appears before hard hold/pause.

## Quick Start

Full Raspberry Pi setup and services: see [PI_SETUP.md](PI_SETUP.md).

Local checks:

```bash
cd nadi_vision
pytest -q
cd frontend && npx tsc --noEmit
```

## Project Structure

```
backend/
  main.py
  config.py
  integrity/monitor.py
  report/generator.py
  scoring/constants.py
  scoring/engine.py
  sensors/camera.py
  sensors/ultrasonic.py
  server/ws_server.py
  vision/face_detection.py

scripts/
  camera_service.py

frontend/src/
  app/
  components/screens/
  lib/

tests/
```

## Environment Variables (camera service)

| Variable | Default |
|---|---|
| NADI_INFER_W | 320 |
| NADI_INFER_H | 240 |
| NADI_PREVIEW_W | 1280 |
| NADI_PREVIEW_H | 720 |
| NADI_CAM_FPS | 30 |
| NADI_INFER_SKIP | 5 |
| NADI_PREVIEW_SKIP | 4 |
| NADI_UDS_PATH | /run/nadivision/frames.sock |
| NADI_MJPEG_PORT | 8766 |
| NADI_JPEG_QUALITY | 65 |


