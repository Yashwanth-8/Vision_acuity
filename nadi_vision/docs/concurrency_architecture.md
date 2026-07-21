# Concurrency Architecture (Phase 1)

## Objective
Design a Raspberry Pi 4 (4GB) safe concurrency model for Nadi Vision where no subsystem can starve another, memory growth is bounded, and shutdown is deterministic.

## Subsystem Inventory

| Subsystem | Execution Unit | Produces | Consumes |
|---|---|---|---|
| `backend/sensors/ultrasonic.py` | Dedicated OS thread | `distance_queue` | GPIO reads only |
| `backend/sensors/camera.py` | Dedicated OS thread | `frame_queue` | camera hardware only |
| `backend/vision/face_detection.py` | Dedicated process (`multiprocessing`) | `attention_state_queue` | `frame_queue` |
| `backend/integrity/monitor.py` | Async task | `integrity_flag_queue` | distance + attention + frontend events |
| `backend/server/ws_server.py` | Async event loop | WebSocket outbound stream | queues + backend state |
| `backend/scoring/engine.py` | Synchronous pure module | result objects | frontend responses |

## Why Separate Process for Vision
- Face inference is CPU-heavy and can contend under the Python GIL.
- A separate process prevents inference stalls from delaying sensor and websocket loops.
- The memory cost is accepted because queue bounds protect total footprint.

## Queue Topology and Bounds

| Queue | Producer | Consumer | maxsize | Full Policy |
|---|---|---|---|---|
| `frame_queue` | camera thread | vision process | 2 | drop-oldest |
| `distance_queue` | ultrasonic thread | ws/integrity | 3 | drop-oldest |
| `attention_state_queue` | vision process | ws/integrity | 3 | drop-oldest |
| `integrity_flag_queue` | integrity task | ws/report | 64 | drop-oldest (oldest warning events) |

### Rationale
- Live sensor data should prefer freshness over completeness.
- Unbounded queues are disallowed on Pi 4 because they can cause OOM during long sessions.

## Timing Targets
- Camera capture: ~30fps
- Inference: 1 in `FACE_DETECT_FRAME_SKIP` frames (start with 1-in-4)
- Distance polling: ~17Hz
- WebSocket state updates: 15-20Hz
- JPEG preview stream: low-res, ~10fps

## Clean Shutdown Contract
`backend/main.py` owns all startup/shutdown.

Shutdown sequence:
1. Set global stop event.
2. Stop accepting new websocket clients.
3. Signal worker threads/processes with stop event and/or sentinel queue item.
4. Join each worker with timeout and log status.
5. Force-terminate only non-responsive process workers.

## Pi 4 Budget Targets (to validate in Phase 8)
- OS + services: ~350-450MB
- Chromium kiosk: ~500-800MB
- Next.js prod frontend: ~150-300MB
- Python backend: ~400-700MB
- GPU split: 128-256MB
- Safety headroom: 800MB-1GB

## Vision Model Decision (Phase 1.2 — COMPLETE)

Benchmark run 2026-07-21 on Pi 4 (4 GB, Cortex-A72), 120 frames at 320×240:

| Model | avg_ms | p95_ms | max_ms | rss_mb |
|---|---|---|---|---|
| YuNet (opencv_zoo 2023mar) | 33.5 | 52.0 | 72.3 | 77.4 |
| **MediaPipe FaceDetection 0.10 (short-range)** | **13.8** | **18.9** | **29.5** | **134.0** |

**Decision: MediaPipe.**

Rationale:
- 2.4× lower average latency due to TFLite NEON SIMD on Cortex-A72
- 2.75× lower p95 — critical for consistent attention polling
- YuNet 72ms spikes would cause visible integrity-monitor lag
- +57 MB RAM cost is acceptable (134 MB ≈ 3.3% of 4 GB)
- `FACE_DETECT_FRAME_SKIP=4` at 30fps → 7.5Hz inference; headroom allows lowering to 2 if needed

YuNet path removed from `backend/vision/face_detection.py`.
