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
EMA_ALPHA = 0.7
MEDIAN_WINDOW = 3
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

# Distance stability
# Widened to ±10 cm based on clinical survey (doctors accept up to 10 cm error).
# Hold time reduced to 1.5 s — achievable in real-world conditions.
DISTANCE_STABILITY_WINDOW_M = 0.10   # ±10 cm window for stable-hold timer
DISTANCE_STABILITY_HOLD_S = 1.5      # seconds of stable distance required to unlock

# Debounce thresholds
FACE_LOSS_DEBOUNCE_S = 2.0
# Gaze: raised to 2.0 s and threshold to 35°.
# The coarse bounding-box yaw estimate has ±20°+ noise at 320×240;
# 35° requires the patient to be clearly looking sideways, not just
# a slight head shift or detection jitter.
GAZE_OFF_DEBOUNCE_S = 2.0
# Fellow-eye hold uses a 2.0 s debounce to avoid transient false holds.
# Raised from 1.0 s to reduce false holds caused by hand edge cases and glasses.
FELLOW_EYE_DEBOUNCE_S = 2.0
GAZE_YAW_THRESHOLD_DEG = 35.0

# Response timing
FAST_ANSWER_THRESHOLD_MS = 300
RESUME_STABILITY_HOLD_S = 1.5
# Mid-trial drift tolerance matches the stability window (±10 cm).
DISTANCE_DRIFT_TOLERANCE_M = 0.10

# WebSocket defaults
WS_HOST = "0.0.0.0"
WS_PORT = 8765
WS_UPDATE_HZ = 20
