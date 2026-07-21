# Nadi Vision Rebuild

This repository contains the clean-room rebuild of the Nadi Vision system using the implementation plan in `../IMPLEMENTATION_PLAN.md`.

## Current Status
- Phase 1-7 implementation baseline completed
- **Vision model decision: MediaPipe** (avg 13.8ms vs YuNet 33.5ms on Pi 4 Cortex-A72)
- Backend orchestration implemented (`ultrasonic`, `camera`, `face_detection`, `ws_server`, `main`)
- Scoring, integrity, and report modules implemented with test coverage
- Frontend scaffold populated and production build verified
- Benchmark script uses real inference (YuNet + MediaPipe)

## Run

### Backend
```bash
cd backend
python3 main.py
```

### Backend (Split Interpreter on Pi 4)

Use this mode when MediaPipe must run on Python 3.11 venv, while camera capture
must run on system Python 3.13 with Picamera2.

Terminal 1 (system Python 3.13 camera bridge producer):
```bash
cd scripts
/usr/bin/python3 camera_bridge_producer.py --out /tmp/nadi_bridge/latest.jpg --width 1280 --height 720 --fps 30
```

Terminal 2 (Python 3.11 backend + MediaPipe):
```bash
cd backend
export NADI_CAMERA_MODE=bridge
export NADI_BRIDGE_FRAME_PATH=/tmp/nadi_bridge/latest.jpg
python main.py
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### Benchmark + Performance Gate
```bash
cd scripts
python3 benchmark_face_models.py --frames 120 --out real_benchmark_results.json
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
