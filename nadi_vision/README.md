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
