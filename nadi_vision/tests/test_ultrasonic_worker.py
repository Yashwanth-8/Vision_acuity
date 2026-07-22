"""Ultrasonic worker filtering tests."""

from queue import Queue

from backend.sensors.ultrasonic import UltrasonicWorker


def test_mad_filter_rejects_large_spike() -> None:
    worker = UltrasonicWorker(distance_queue=Queue(maxsize=3), read_distance_fn=lambda: 0.6)

    last = None
    for _ in range(12):
        last = worker._filter(0.60)

    assert last is not None

    spike = worker._filter(2.0)
    assert spike is not None

    # Spike should be rejected and output should stay near previous smoothed value.
    assert abs(spike - last) < 0.02
