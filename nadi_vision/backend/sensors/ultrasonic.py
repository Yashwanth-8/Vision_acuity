"""HC-SR04 ultrasonic sensor reader with Median(3) + EMA(alpha=0.7) filtering."""

from __future__ import annotations

from collections import deque
from queue import Empty, Full, Queue
import threading
import time
from typing import Callable, Optional

from backend.config import DISTANCE_QUEUE_MAXSIZE
from backend.scoring.constants import (
    EMA_ALPHA,
    MEDIAN_WINDOW,
    SENSOR_MAX_M,
    SENSOR_MIN_M,
    SENSOR_TO_EYE_OFFSET_M,
    SENSOR_TO_SCREEN_OFFSET_M,
)

try:
    from gpiozero import DistanceSensor
except Exception:  # pragma: no cover
    DistanceSensor = None


class UltrasonicWorker:
    """Background distance polling worker.

    Raw pipeline:
    raw reading -> validate range -> median(3) -> EMA(0.7) -> corrected distance.
    """

    def __init__(
        self,
        distance_queue: Queue,
        *,
        trigger_pin: int = 17,
        echo_pin: int = 27,
        poll_hz: float = 17.0,
        read_distance_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        self._distance_queue = distance_queue
        self._trigger_pin = trigger_pin
        self._echo_pin = echo_pin
        self._period_s = 1.0 / poll_hz
        self._read_distance_fn = read_distance_fn

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sensor = None

        self._raw_window: deque[float] = deque(maxlen=MEDIAN_WINDOW)
        self._ema_value: Optional[float] = None
        self.latest_distance_m: Optional[float] = None

    def _ensure_sensor(self) -> None:
        if self._read_distance_fn is not None:
            return
        if DistanceSensor is None:  # pragma: no cover
            raise RuntimeError("gpiozero DistanceSensor unavailable; provide read_distance_fn")
        self._sensor = DistanceSensor(echo=self._echo_pin, trigger=self._trigger_pin)
        self._read_distance_fn = lambda: float(self._sensor.distance)

    def _push_distance(self, value: float) -> None:
        try:
            self._distance_queue.put_nowait(value)
        except Full:
            try:
                self._distance_queue.get_nowait()
            except Empty:
                pass
            try:
                self._distance_queue.put_nowait(value)
            except Full:
                pass

    def _filter(self, raw_distance: float) -> Optional[float]:
        if raw_distance < SENSOR_MIN_M or raw_distance > SENSOR_MAX_M:
            return None

        self._raw_window.append(raw_distance)
        if len(self._raw_window) < MEDIAN_WINDOW:
            return None

        ordered = sorted(self._raw_window)
        median_distance = ordered[len(ordered) // 2]
        if self._ema_value is None:
            self._ema_value = median_distance
        else:
            self._ema_value = (EMA_ALPHA * median_distance) + ((1.0 - EMA_ALPHA) * self._ema_value)

        corrected = self._ema_value + SENSOR_TO_SCREEN_OFFSET_M + SENSOR_TO_EYE_OFFSET_M
        return max(SENSOR_MIN_M, min(SENSOR_MAX_M, corrected))

    def _run(self) -> None:
        self._ensure_sensor()
        while not self._stop_event.is_set():
            start = time.monotonic()
            try:
                assert self._read_distance_fn is not None
                raw = self._read_distance_fn()
                filtered = self._filter(raw)
                if filtered is not None:
                    self.latest_distance_m = filtered
                    self._push_distance(filtered)
            except Exception:
                # Keep worker alive through transient GPIO failures.
                pass

            elapsed = time.monotonic() - start
            remaining = self._period_s - elapsed
            if remaining > 0:
                self._stop_event.wait(remaining)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if self._distance_queue.maxsize <= 0:
            self._distance_queue = Queue(maxsize=DISTANCE_QUEUE_MAXSIZE)
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="nadi-ultrasonic", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._sensor is not None:
            try:
                self._sensor.close()
            except Exception:
                pass
