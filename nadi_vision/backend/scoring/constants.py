"""Named constants for the Nadi Vision scoring and integrity pipeline."""

# Scoring
LOGMAR_CEILING = 1.0
LOGMAR_PER_LETTER = 0.02
VAS_OFFSET = 30
LETTERS_PER_LINE = 5

# Low vision fallback
LOW_VISION_DISTANCE_M = 1.0
LOW_VISION_CATEGORIES = ["CF", "HM", "LP", "NLP"]

# Termination
TERMINATION_ERROR_THRESHOLD = 0.6

# Distance sensor
EMA_ALPHA = 0.7
MEDIAN_WINDOW = 3
SENSOR_MIN_M = 0.04
SENSOR_MAX_M = 3.50
SENSOR_TO_SCREEN_OFFSET_M = 0.0
SENSOR_TO_EYE_OFFSET_M = 0.013

# Vision and attention — MediaPipe FaceDetection (model_selection=0)
# At 30fps camera, FRAME_SKIP=4 → ~7.5Hz inference (avg 13.8ms, p95 18.9ms on Pi 4)
# Can safely lower to 2 (15Hz) if responsiveness needs improve; benchmark headroom exists.
FACE_DETECT_FRAME_SKIP = 4
DETECT_WIDTH = 320
DETECT_HEIGHT = 240

# Debounce thresholds
FACE_LOSS_DEBOUNCE_S = 2.0
GAZE_OFF_DEBOUNCE_S = 1.0
FELLOW_EYE_DEBOUNCE_S = 0.5
GAZE_YAW_THRESHOLD_DEG = 20.0

# Response timing
FAST_ANSWER_THRESHOLD_MS = 300
RESUME_STABILITY_HOLD_S = 1.5
DISTANCE_DRIFT_TOLERANCE_M = 0.03

# Ambient light thresholds
AMBIENT_LIGHT_MIN = 80
AMBIENT_LIGHT_MAX = 220

# Camera defaults
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
CAMERA_FRAMERATE = 30
PREVIEW_QUALITY = 65
PREVIEW_SKIP = 3

# WebSocket defaults
WS_HOST = "0.0.0.0"
WS_PORT = 8765
WS_UPDATE_HZ = 20
