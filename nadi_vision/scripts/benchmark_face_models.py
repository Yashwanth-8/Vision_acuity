"""Benchmark YuNet vs MediaPipe on Raspberry Pi 4 using real inference.

Usage:
    python scripts/benchmark_face_models.py --frames 200 --width 320 --height 240
    python scripts/benchmark_face_models.py --frames-dir benchmark_frames --out results.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List

import cv2
import numpy as np

try:
    import psutil
except Exception as exc:  # pragma: no cover
    raise SystemExit("psutil is required for memory measurement") from exc


@dataclass
class BenchResult:
    model: str
    avg_ms: float
    p95_ms: float
    max_ms: float
    rss_mb: float


def _p95(values: List[float]) -> float:
    if len(values) >= 20:
        return statistics.quantiles(values, n=20)[18]
    return max(values)


def benchmark_model(name: str, infer_fn: Callable[[np.ndarray], None], frames: List[np.ndarray]) -> BenchResult:
    process = psutil.Process()
    latencies: List[float] = []

    for frame in frames:
        t0 = time.monotonic()
        infer_fn(frame)
        latencies.append((time.monotonic() - t0) * 1000.0)

    rss_mb = process.memory_info().rss / (1024 * 1024)
    return BenchResult(
        model=name,
        avg_ms=statistics.mean(latencies),
        p95_ms=_p95(latencies),
        max_ms=max(latencies),
        rss_mb=rss_mb,
    )


def _load_frames_from_dir(frames_dir: Path, width: int, height: int) -> List[np.ndarray]:
    frames: List[np.ndarray] = []
    for path in sorted(frames_dir.glob("*.jpg")) + sorted(frames_dir.glob("*.png")):
        frame = cv2.imread(str(path))
        if frame is None:
            continue
        frames.append(cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA))
    if not frames:
        raise SystemExit(f"No readable images found in {frames_dir}")
    return frames


def _synthetic_frames(count: int, width: int, height: int) -> List[np.ndarray]:
    frames: List[np.ndarray] = []
    for _ in range(count):
        frames.append(np.zeros((height, width, 3), dtype=np.uint8))
    return frames


def _repeat_to_count(frames: List[np.ndarray], target_count: int) -> List[np.ndarray]:
    if len(frames) >= target_count:
        return frames[:target_count]
    repeated: List[np.ndarray] = []
    idx = 0
    while len(repeated) < target_count:
        repeated.append(frames[idx % len(frames)])
        idx += 1
    return repeated


def _ensure_yunet_model(model_path: Path) -> Path:
    if model_path.exists():
        return model_path
    model_path.parent.mkdir(parents=True, exist_ok=True)
    url = (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx"
    )
    urllib.request.urlretrieve(url, str(model_path))
    return model_path


def _build_yunet_infer(width: int, height: int, model_path: Path) -> Callable[[np.ndarray], None]:
    detector = cv2.FaceDetectorYN.create(str(model_path), "", (width, height))

    def infer(frame: np.ndarray) -> None:
        detector.detect(frame)

    return infer


def _build_mediapipe_infer() -> Callable[[np.ndarray], None]:
    try:
        import mediapipe as mp
    except Exception as exc:
        raise SystemExit("mediapipe is required. Install with: pip install mediapipe>=0.10") from exc

    detector = mp.solutions.face_detection.FaceDetection(
        model_selection=0,
        min_detection_confidence=0.5,
    )

    def infer(frame: np.ndarray) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        detector.process(rgb)

    return infer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=240)
    parser.add_argument("--frames-dir", default="")
    parser.add_argument("--yunet-model", default="models/face_detection_yunet_2023mar.onnx")
    parser.add_argument("--out", default="benchmark_face_models.json")
    args = parser.parse_args()

    if args.frames_dir:
        base_frames = _load_frames_from_dir(Path(args.frames_dir), args.width, args.height)
    else:
        base_frames = _synthetic_frames(max(1, args.frames), args.width, args.height)

    frames = _repeat_to_count(base_frames, max(1, args.frames))

    yunet_model = _ensure_yunet_model(Path(args.yunet_model))
    yunet_infer = _build_yunet_infer(args.width, args.height, yunet_model)
    mediapipe_infer = _build_mediapipe_infer()

    results = [
        benchmark_model("YuNet", yunet_infer, frames),
        benchmark_model("MediaPipe", mediapipe_infer, frames),
    ]

    payload = [r.__dict__ for r in results]
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)

    for row in results:
        print(
            f"{row.model}: avg={row.avg_ms:.2f}ms p95={row.p95_ms:.2f}ms "
            f"max={row.max_ms:.2f}ms rss={row.rss_mb:.1f}MB"
        )


if __name__ == "__main__":
    main()
