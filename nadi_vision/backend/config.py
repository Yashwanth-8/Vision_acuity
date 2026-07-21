"""Backend runtime configuration shared across subsystems."""

from backend.scoring.constants import (  # re-export shared constants
    AMBIENT_LIGHT_MAX,
    AMBIENT_LIGHT_MIN,
    CAMERA_FRAMERATE,
    CAMERA_HEIGHT,
    CAMERA_WIDTH,
    DETECT_HEIGHT,
    DETECT_WIDTH,
    FACE_DETECT_FRAME_SKIP,
    PREVIEW_QUALITY,
    PREVIEW_SKIP,
    SENSOR_MAX_M,
    SENSOR_MIN_M,
    WS_HOST,
    WS_PORT,
    WS_UPDATE_HZ,
)

# Queue sizing (bounded queues required on Pi 4)
FRAME_QUEUE_MAXSIZE = 2
DISTANCE_QUEUE_MAXSIZE = 3
ATTENTION_QUEUE_MAXSIZE = 3
INTEGRITY_QUEUE_MAXSIZE = 64
