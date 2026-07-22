"""Named constants for the Nadi Vision scoring and integrity pipeline."""

# Scoring
LOGMAR_CEILING = 1.0
LOGMAR_PER_LETTER = 0.02
VAS_OFFSET = 30
LETTERS_PER_LINE = 5

# Low vision fallback
LOW_VISION_DISTANCE_M = 1.0
LOW_VISION_CATEGORIES = ["CF", "HM", "LP", "NLP"]

# Termination — third wrong answer on a line ends the session immediately
TERMINATION_WRONG_COUNT = 3

# Distance sensor
EMA_ALPHA = 0.25
MAD_WINDOW = 12
MAD_OUTLIER_K = 3.5
SENSOR_MIN_M = 0.04
SENSOR_MAX_M = 3.50
SENSOR_TO_SCREEN_OFFSET_M = 0.0
SENSOR_TO_EYE_OFFSET_M = 0.013

# Vision and attention — MediaPipe FaceDetection (model_selection=0)
# Camera service delivers frames at 6 Hz (every 5th of 30 fps captured frames).
# The inference loop processes every received frame, so FRAME_SKIP = 1.
FACE_DETECT_FRAME_SKIP = 1

# Inference frame dimensions (must match camera_service.py NADI_INFER_W/H defaults)
INFER_WIDTH = 320
INFER_HEIGHT = 240

# Debounce thresholds
FACE_LOSS_DEBOUNCE_S = 2.0
FACE_LOSS_WARN_S = 0.5
# Gaze: raised to 2.0 s and threshold to 35°.
# The coarse bounding-box yaw estimate has ±20°+ noise at 320×240;
# 35° requires the patient to be clearly looking sideways, not just
# a slight head shift or detection jitter.
GAZE_OFF_DEBOUNCE_S = 2.0
GAZE_WARN_S = 0.5
# Fellow-eye hold uses a 1.5 s debounce with a 0.5 s soft warning tier.
FELLOW_EYE_DEBOUNCE_S = 1.5
FELLOW_EYE_WARN_S = 0.5
GAZE_YAW_THRESHOLD_DEG = 35.0
FELLOW_EYE_COVERED_THRESH = 0.55
FELLOW_EYE_UNCOVERED_THRESH = 0.40
HAND_EYE_INFER_SKIP = 2

# Response timing
FAST_ANSWER_THRESHOLD_MS = 300
RESUME_STABILITY_HOLD_S = 1.5
# Mid-trial drift tolerance (±10 cm).
DISTANCE_DRIFT_TOLERANCE_M = 0.10

# WebSocket defaults
WS_HOST = "0.0.0.0"
WS_PORT = 8765
WS_UPDATE_HZ = 20
