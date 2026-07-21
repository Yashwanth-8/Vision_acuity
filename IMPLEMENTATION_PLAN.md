# NadiVision Rebuild — Detailed Implementation Plan

> **Document Purpose:** This plan breaks down the entire rebuild into assignable tasks with clear dependencies, acceptance criteria, and estimated effort. Use it to divide work among team members and track progress.

---

## Executive Summary

**What we're building:** A clinical-grade visual acuity screening device running on Raspberry Pi 4 (4GB RAM) that:
- Measures how well a patient can see using a Tumbling E test
- Auto-scales the optotype based on real-time distance measurement (HC-SR04 ultrasonic sensor)
- Monitors test integrity via camera-based attention tracking
- Generates clinically structured reports with proper disclaimers

**Key changes from the prototype (`Raspberry_Vision`):**
| Aspect | Prototype | Rebuild |
|--------|-----------|---------|
| Scoring | Pass/fail 3/5 gate per line | Continuous ETDRS (0.02 logMAR/letter) |
| LOGMAR_CEILING | 1.3 | **1.0** (20/200 Snellen equivalent) |
| Attention flags | 2 (face_loss, multiple_faces) | **6 real-time + 4 post-hoc** |
| Pause/resume | None | Full state machine with debounce |
| Eye testing | Single combined label | **OD/OS/OU separate sessions** |
| UCVA/BCVA | Single field | **Distinct report rows** |
| Low-vision | None (no result for severe cases) | **CF/HM/LP/NLP fallback** |
| Concurrency | Single asyncio loop | **Dedicated threads/processes per subsystem** |
| Report | Basic results JSON | **Full clinical format with regulatory disclaimer** |

---

## Build Order (Strict — Do Not Reorder)

```
Phase 1: Architecture & Scaffolding ──┬── Step 1.1: Concurrency Design (doc)
                                      ├── Step 1.2: YuNet vs MediaPipe Benchmark
                                      └── Step 1.3: Project Scaffolding
                                              │
Phase 2: Scoring Engine ──────────────────────┼── Step 2.1-2.4: AcuitySession + Tests
                                              │
Phase 3: Integrity Monitor ───────────────────┼── Step 3.1-3.4: TestIntegrityMonitor + Tests
                                              │
Phase 4: Hardware Threads ────────────────────┼── Step 4.1-4.5: Sensors, Vision, WS, Main
                                              │
Phase 5: Report Generator ────────────────────┼── Step 5.1-5.6: Full Report + Tests
                                              │
Phase 6: Documentation ───────────────────────┼── Step 6.1-6.3: Regulatory, Scoring, Architecture
                                              │
Phase 7: Frontend ────────────────────────────┼── Step 7.1-7.4: Landing → Setup → Test → Results
                                              │
Phase 8: Performance Validation ──────────────┴── Step 8.1-8.3: Measure, Tune, Verify Budget
```

## Execution Snapshot (2026-07-21)

- Phase 1: Completed (architecture doc, benchmark decision, scaffolding).
- Phase 2: Completed (`AcuitySession` implemented, tests passing).
- Phase 3: Completed (`IntegrityMonitor` implemented, tests passing).
- Phase 4: Completed baseline runtime (`ultrasonic`, `camera`, `face_detection`, `ws_server`, `main`).
- Phase 5: Completed (`ReportGenerator` implemented, report tests passing).
- Phase 6: Completed (`regulatory_status.md`, `scoring_methodology.md`, concurrency doc updated).
- Phase 7: Completed baseline Next.js app scaffold and build validation.
- Phase 8: Validation tooling implemented (`scripts/perf_validate.py`); final full-device run must be executed on target Pi hardware.

---

## Phase 1: Architecture & Scaffolding

### Task 1.1: Concurrency Architecture Design
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 4-6 hours |
| **Dependencies** | None |
| **Output** | `docs/concurrency_architecture.md` |
| **Blocking** | All Phase 4 tasks |

#### What to do:
Design the thread/process architecture for the Pi 4's limited resources. Document these decisions:

**Thread/Process Inventory:**
| Subsystem | Execution Unit | Queue | Justification |
|-----------|---------------|-------|---------------|
| `sensors/ultrasonic.py` | Dedicated OS thread | `distance_queue` (maxsize=3) | GPIO polling at ~17Hz, independent of camera |
| `sensors/camera.py` | Dedicated OS thread | `frame_queue` (maxsize=2) | 30fps capture, XRGB8888→BGR conversion |
| `vision/face_detection.py` | **Separate process** (multiprocessing) | `attention_state_queue` (maxsize=3) | GIL avoidance for CPU-heavy inference |
| `integrity/monitor.py` | Asyncio task | `integrity_flag_queue` | Consumes multiple queues, non-blocking |
| `server/ws_server.py` | Asyncio event loop | WebSocket messages | 15-20Hz updates to frontend |
| `scoring/engine.py` | Pure functions (no thread) | Called synchronously | Stateless, one `AcuitySession` per eye |

**Queue Design Rules:**
- All queues **bounded** with explicit `maxsize`
- **Drop-oldest policy** for sensor data (never block sensor thread)
- Frame queue maxsize=2 (only current + in-flight frame)
- Distance queue maxsize=3 (median filter needs 3 samples)

**Decisions to justify explicitly:**
1. Why separate process for vision inference (not just thread)?
   - Python's GIL means CPU-heavy inference on a thread can starve other threads
   - OpenCV's DNN calls may or may not release GIL (must verify on Pi 4)
   - Separate process sidesteps GIL entirely, worth the memory duplication

2. How does WebSocket hit ~15-20Hz without blocking scoring?
   - Asyncio non-blocking sends
   - Consume from queues with `get_nowait()` + fallback to last known value

3. Clean shutdown sequence?
   - `main.py` owns all stop-events
   - Signal all threads/processes → join with timeout → log status
   - No daemon threads killed ungracefully

#### Acceptance Criteria:
- [x] Document explains every thread/process and why
- [x] All queues have documented `maxsize` and full-queue policy
- [x] Shutdown sequence documented step-by-step
- [x] Memory budget per consumer estimated (per Part 12)

---

### Task 1.2: YuNet vs MediaPipe Benchmark — **COMPLETE**
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 3-4 hours |
| **Dependencies** | Pi 4 hardware access |
| **Output** | `docs/concurrency_architecture.md` (updated with results) |
| **Blocking** | Task 4.3 (Face Detection Process) |

#### Results (Pi 4, Cortex-A72, 120 frames at 320×240, 2026-07-21):

| Model | avg_ms | p95_ms | max_ms | rss_mb |
|---|---|---|---|---|
| YuNet (opencv_zoo 2023mar) | 33.5 | 52.0 | 72.3 | 77.4 |
| **MediaPipe FaceDetection 0.10** | **13.8** | **18.9** | **29.5** | **134.0** |

**Decision: MediaPipe** — 2.4× faster avg, 2.75× better p95. YuNet 72ms spikes are unacceptable for integrity polling. The +57 MB RAM is affordable on 4 GB.

**Accepted trade-off:** `FACE_DETECT_FRAME_SKIP=4` at 30fps → 7.5Hz inference. Can lower to 2 (15Hz) later; benchmark headroom exists.

#### Acceptance Criteria:
- [x] Benchmark script runs on Pi 4 (not laptop)
- [x] Both YuNet and MediaPipe tested with real 320×240 frames
- [x] Latency and memory numbers documented
- [x] Decision recorded with clear reasoning

---

### Task 1.3: Project Scaffolding
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 2-3 hours |
| **Dependencies** | None (can parallel with 1.1, 1.2) |
| **Output** | Directory structure + stub files |
| **Blocking** | All implementation tasks |

#### What to do:
Create the directory structure and stub files:

```
nadi_vision/
├── backend/
│   ├── __init__.py
│   ├── config.py                    # All named constants in one place
│   ├── main.py                      # SOLE entry point, owns all threads/processes
│   ├── sensors/
│   │   ├── __init__.py
│   │   ├── ultrasonic.py            # HC-SR04 thread
│   │   └── camera.py                # Pi Camera thread
│   ├── vision/
│   │   ├── __init__.py
│   │   └── face_detection.py        # YuNet/MediaPipe process
│   ├── scoring/
│   │   ├── __init__.py
│   │   ├── constants.py             # LOGMAR_CEILING, VAS_OFFSET, etc.
│   │   └── engine.py                # AcuitySession class
│   ├── integrity/
│   │   ├── __init__.py
│   │   └── monitor.py               # TestIntegrityMonitor
│   ├── report/
│   │   ├── __init__.py
│   │   └── generator.py             # Report builder
│   └── server/
│       ├── __init__.py
│       └── ws_server.py             # Asyncio WebSocket server
├── frontend/                        # Next.js app (scaffold later in Phase 7)
│   └── ...
├── tests/
│   ├── __init__.py
│   ├── test_scoring_engine.py
│   ├── test_integrity_monitor.py
│   └── test_report_generator.py
├── docs/
│   ├── concurrency_architecture.md
│   ├── regulatory_status.md
│   └── scoring_methodology.md
├── requirements.txt
└── README.md
```

**Create `scoring/constants.py` with all named constants:**
```python
"""
Named constants for the Nadi Vision scoring engine.
All magic numbers live here — never hardcode values elsewhere.
"""

# === Scoring ===
LOGMAR_CEILING = 1.0  # Maximum logMAR (20/200 Snellen), NOT 1.3
LOGMAR_PER_LETTER = 0.02  # Credit per correct letter (ETDRS standard)
VAS_OFFSET = 30  # Visual Acuity Score = letters_correct + 30 (ETDRS convention)
LETTERS_PER_LINE = 5  # Standard ETDRS chart has 5 letters per line

# === Low Vision Fallback ===
LOW_VISION_DISTANCE_M = 1.0  # Try at 1m if patient can't read at full distance
LOW_VISION_CATEGORIES = ["CF", "HM", "LP", "NLP"]  # Counting Fingers, Hand Motion, Light Perception, No LP

# === Termination ===
TERMINATION_ERROR_THRESHOLD = 0.6  # Stop when >60% of line is wrong

# === Distance Sensor ===
EMA_ALPHA = 0.7  # Exponential moving average smoothing factor
MEDIAN_WINDOW = 3  # Median filter window size
SENSOR_MIN_M = 0.04  # 4cm minimum valid reading
SENSOR_MAX_M = 3.50  # 3.5m maximum valid reading
SENSOR_TO_SCREEN_OFFSET_M = 0.0  # MUST MEASURE on actual enclosure before validation
SENSOR_TO_EYE_OFFSET_M = 0.013  # 13mm anatomical estimate

# === Vision/Attention ===
FACE_DETECT_FRAME_SKIP = 4  # Run inference every N frames (~6-8Hz at 30fps)
DETECT_WIDTH = 320  # Fixed canvas width for YuNet/MediaPipe
DETECT_HEIGHT = 240  # Fixed canvas height

# === Attention Debounce Windows (seconds) ===
FACE_LOSS_DEBOUNCE_S = 2.0  # No face for >2s triggers pause
GAZE_OFF_DEBOUNCE_S = 1.0  # Looking away for >1s triggers pause
FELLOW_EYE_DEBOUNCE_S = 0.5  # Non-tested eye open for >0.5s triggers pause
GAZE_YAW_THRESHOLD_DEG = 20.0  # Head yaw >20° = not looking at screen

# === Response Timing ===
FAST_ANSWER_THRESHOLD_MS = 300  # Response <300ms flagged as implausibly fast
RESUME_STABILITY_HOLD_S = 1.5  # All conditions must be OK for 1.5s before resuming

# === Ambient Light ===
AMBIENT_LIGHT_MIN = 80  # Below this: "Check lighting"
AMBIENT_LIGHT_MAX = 220  # Above this: "Check lighting" (too bright/washed out)

# === Camera ===
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
CAMERA_FRAMERATE = 30
PREVIEW_QUALITY = 65  # JPEG quality for WebSocket preview
PREVIEW_SKIP = 3  # Send preview every N frames (~10fps)

# === WebSocket ===
WS_HOST = "0.0.0.0"
WS_PORT = 8765
WS_UPDATE_HZ = 20  # Target update rate for distance/attention state
```

#### Acceptance Criteria:
- [x] All directories created with `__init__.py`
- [x] `scoring/constants.py` has all named constants with comments
- [x] Each module has a stub with docstring explaining its purpose
- [x] `requirements.txt` lists dependencies (opencv-python-headless, websockets, etc.)
- [x] Tests directory prepared and populated for Phase 2-5

---

## Phase 2: Scoring Engine (Highest-Risk Correctness Area)

### Task 2.1: AcuitySession Class — Core Implementation
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 8-10 hours |
| **Dependencies** | Task 1.3 (scaffolding complete) |
| **Output** | `scoring/engine.py` |
| **Blocking** | Tasks 2.2, 2.3, 5.x (Report) |

#### What to do:
Implement the per-eye scoring session with continuous ETDRS-equivalent scoring.

**Class Interface:**
```python
from dataclasses import dataclass
from typing import Literal, List, Optional
from enum import Enum

class LowVisionCategory(Enum):
    CF = "Counting Fingers"
    HM = "Hand Motion"
    LP = "Light Perception"
    NLP = "No Light Perception"

@dataclass
class TrialResult:
    """Result of a single letter presentation."""
    level_logmar: float       # LogMAR of the line being tested
    presented: str            # Direction shown: "up", "down", "left", "right"
    answered: str             # Patient's response
    correct: bool
    distance_m: float         # Actual distance at time of trial
    response_time_ms: float   # Time to respond
    timestamp: float          # Unix timestamp
    invalidated: bool = False # True if mid-trial distance drift detected
    invalidation_reason: Optional[str] = None

@dataclass
class SessionResult:
    """Final result of a complete eye test session."""
    eye: Literal["OD", "OS"]
    correction: Literal["UCVA", "BCVA"]
    
    # Primary outputs
    logmar: float                          # Continuous decimal, clamped to ceiling
    snellen_feet: str                      # "20/40"
    snellen_metric: str                    # "6/12"
    decimal_va: float                      # 20 / snellen_denominator
    etdrs_letter_score: int                # Total correct letters
    vas: int                               # Visual Acuity Score = letters + 30
    who_classification: str                # "Normal", "Mild", "Moderate", "Severe", "Blind"
    
    # Low vision (if applicable)
    low_vision_category: Optional[LowVisionCategory] = None
    
    # Statistics
    total_trials: int
    correct_trials: int
    avg_distance_m: float
    confidence_interval_95: tuple[float, float]  # (lower, upper) logMAR
    
    # Raw data
    trials: List[TrialResult]

class AcuitySession:
    """
    Manages a single eye's acuity test session.
    
    One instance per eye tested. For OU (both eyes), create two separate
    instances and run them sequentially (OD first by convention).
    Never average or merge results from separate sessions.
    """
    
    def __init__(
        self, 
        eye: Literal["OD", "OS"], 
        correction: Literal["UCVA", "BCVA"],
        start_logmar: float = 1.0  # Start at ceiling (20/200)
    ):
        """Initialize a new acuity session for one eye."""
        ...
    
    def record_response(
        self,
        presented: str,           # "up", "down", "left", "right"
        answered: str,            # Patient's response
        distance_m: float,        # Current distance from sensor
        response_time_ms: float,  # Time to respond
    ) -> TrialResult:
        """
        Record a single trial response and advance state.
        
        Returns the trial result (may be invalidated if distance drifted).
        Call should_terminate() after each response to check if test is done.
        """
        ...
    
    def invalidate_last_trial(self, reason: str) -> None:
        """
        Mark the most recent trial as invalidated (e.g., distance drift).
        Invalidated trials don't count toward scoring but are logged.
        """
        ...
    
    def should_terminate(self) -> bool:
        """
        Check if test should end based on error rate.
        
        Returns True when majority of current line is wrong,
        indicating patient has reached their acuity limit.
        """
        ...
    
    def get_current_logmar(self) -> float:
        """Get the logMAR level currently being tested."""
        ...
    
    def get_result(self) -> SessionResult:
        """
        Compute and return final session result.
        
        Must be called only after should_terminate() returns True
        or low-vision fallback is complete.
        """
        ...
```

**Scoring Formula (ETDRS-equivalent):**
```python
# Per-letter credit: 0.02 logMAR per correct letter
# Formula: LogMAR = StartLineLogMAR - (0.02 × total_correct)
# Clamp to ceiling: max(computed_logmar, LOGMAR_CEILING)

def compute_logmar(start_logmar: float, correct_count: int) -> float:
    computed = start_logmar - (LOGMAR_PER_LETTER * correct_count)
    return max(computed, -0.3)  # Don't go below -0.3 (20/10, exceptional vision)
```

**WHO Classification:**
```python
def classify_who(logmar: float) -> str:
    if logmar <= 0.0:
        return "Normal"
    elif logmar <= 0.5:
        return "Mild"
    elif logmar <= 1.0:
        return "Moderate"
    elif logmar <= 1.3:
        return "Severe"
    else:
        return "Blind"
```

**Snellen Conversion:**
```python
def logmar_to_snellen_feet(logmar: float) -> str:
    denominator = round(20 * (10 ** logmar))
    return f"20/{denominator}"

def logmar_to_snellen_metric(logmar: float) -> str:
    denominator = round(6 * (10 ** logmar))
    return f"6/{denominator}"

def logmar_to_decimal_va(logmar: float) -> float:
    return 10 ** (-logmar)
```

**Confidence Interval (Binomial):**
```python
import math

def compute_95_ci(correct: int, total: int) -> tuple[float, float]:
    """
    Compute 95% CI on logMAR using binomial variance.
    
    This is SCREENING-TIER precision. Diagnostic-grade requires
    CI ≤ 0.14 logMAR (±7 letters), which a single screening test
    does not achieve by design.
    """
    if total == 0:
        return (LOGMAR_CEILING, LOGMAR_CEILING)
    
    p = correct / total
    # Standard error of proportion
    se = math.sqrt(p * (1 - p) / total) if 0 < p < 1 else 0
    # Convert to logMAR units (each letter = 0.02 logMAR)
    se_logmar = se * total * LOGMAR_PER_LETTER
    
    # 95% CI = ±1.96 SE
    margin = 1.96 * se_logmar
    center_logmar = LOGMAR_CEILING - (correct * LOGMAR_PER_LETTER)
    
    return (center_logmar - margin, center_logmar + margin)
```

#### Acceptance Criteria:
- [ ] `AcuitySession` class with full interface implemented
- [ ] LogMAR ceiling enforced (never returns value > 1.0 for numeric results)
- [ ] VAS computed as `letters_correct + 30`
- [ ] Decimal VA computed correctly
- [ ] WHO classification matches thresholds
- [ ] Snellen conversion (feet + metric) accurate
- [ ] 95% CI computed and labeled as screening-tier
- [ ] Per-trial distance recorded (not assumed fixed)
- [ ] Invalidated trials tracked but not scored

---

### Task 2.2: Low-Vision Fallback
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 3-4 hours |
| **Dependencies** | Task 2.1 |
| **Output** | Extended `scoring/engine.py` |
| **Blocking** | Task 5.1 (Report must handle low-vision) |

#### What to do:
Add state machine for patients who can't read the largest optotype:

**State Machine:**
```
┌─────────────────┐
│ Try at normal   │
│ distance        │
└────────┬────────┘
         │ Can't read largest E
         ▼
┌─────────────────┐
│ Try at 1m       │
│ (LOW_VISION_    │
│  DISTANCE_M)    │
└────────┬────────┘
         │ Still can't read
         ▼
┌─────────────────┐
│ Record category │
│ CF/HM/LP/NLP    │
└─────────────────┘
```

**Implementation:**
```python
class LowVisionState(Enum):
    NORMAL_DISTANCE = "normal"
    REDUCED_DISTANCE = "reduced"  # Testing at 1m
    CATEGORICAL = "categorical"   # Using CF/HM/LP/NLP

class AcuitySession:
    def __init__(self, ...):
        ...
        self._low_vision_state = LowVisionState.NORMAL_DISTANCE
        self._low_vision_category: Optional[LowVisionCategory] = None
    
    def enter_reduced_distance_mode(self) -> None:
        """
        Called when patient can't read largest E at normal distance.
        Switches to 1m testing distance.
        """
        self._low_vision_state = LowVisionState.REDUCED_DISTANCE
    
    def record_low_vision_category(self, category: LowVisionCategory) -> None:
        """
        Called when patient can't read even at 1m.
        Records non-numeric category (CF, HM, LP, NLP).
        Test is now complete.
        """
        self._low_vision_state = LowVisionState.CATEGORICAL
        self._low_vision_category = category
    
    def get_result(self) -> SessionResult:
        # If categorical, return non-numeric result
        if self._low_vision_state == LowVisionState.CATEGORICAL:
            return SessionResult(
                eye=self._eye,
                correction=self._correction,
                logmar=float('inf'),  # No numeric score
                snellen_feet="CF" if self._low_vision_category == LowVisionCategory.CF else ...,
                low_vision_category=self._low_vision_category,
                ...
            )
        ...
```

**Important:** The test must NEVER end with no result. Either:
1. Numeric logMAR score, OR
2. Low-vision category (CF/HM/LP/NLP)

#### Acceptance Criteria:
- [ ] State machine transitions correctly between modes
- [ ] Reduced-distance mode uses `LOW_VISION_DISTANCE_M` constant
- [ ] All four categories (CF, HM, LP, NLP) supported
- [ ] `SessionResult` includes `low_vision_category` field
- [ ] Test cannot complete with no result

---

### Task 2.3: Distance Correction Documentation
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 2 hours |
| **Dependencies** | Task 2.1 |
| **Output** | Code comments + `docs/scoring_methodology.md` section |
| **Blocking** | Task 6.2 (docs) |

#### What to do:
Document that distance correction is a "functionally equivalent alternative" to ETDRS:

**In code comments:**
```python
# Distance Correction Approach
# ============================
# Traditional ETDRS tests at a fixed distance (4m/6m/20ft) and applies a 
# distance-correction formula after the fact.
#
# Nadi Vision instead measures actual distance continuously and re-scales
# the optotype in real-time so its angular size matches the target logMAR line.
# This is FUNCTIONALLY EQUIVALENT but not LITERALLY the ETDRS formula.
#
# We record actual_distance_m per trial so the report can state exactly
# what distance was used, rather than assuming a fixed distance.
#
# Error relationship: ~0.004 logMAR shift per cm error at 1m
#                     ~0.01 logMAR shift per cm error at 40cm
```

**In docs:** Add section to `docs/scoring_methodology.md` explaining:
1. Why we don't use the literal ETDRS distance-correction formula
2. How angular subtension achieves the same clinical goal
3. The distance-error-to-logMAR-shift relationship

#### Acceptance Criteria:
- [ ] Code comments explain the approach
- [ ] Never claims "ETDRS compliance" — says "functionally equivalent alternative"
- [ ] Error relationship documented (0.004 logMAR/cm at 1m)

---

### Task 2.4: Scoring Engine Tests
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 4-5 hours |
| **Dependencies** | Tasks 2.1, 2.2 |
| **Output** | `tests/test_scoring_engine.py` |
| **Blocking** | None (but must pass before Phase 3) |

#### What to do:
Write comprehensive unit tests:

```python
import pytest
from scoring.engine import AcuitySession, LowVisionCategory
from scoring.constants import LOGMAR_CEILING, VAS_OFFSET, LOGMAR_PER_LETTER

class TestLogMARCeiling:
    """Verify logMAR ceiling is enforced."""
    
    def test_ceiling_is_1_0_not_1_3(self):
        """Part 4: LOGMAR_CEILING = 1.0, not 1.3."""
        assert LOGMAR_CEILING == 1.0
    
    def test_no_result_exceeds_ceiling(self):
        """No scoring path can return logMAR > 1.0 for numeric results."""
        session = AcuitySession(eye="OD", correction="UCVA")
        # Patient gets everything wrong
        for _ in range(25):
            session.record_response("up", "down", 0.5, 1000)
        result = session.get_result()
        assert result.logmar <= LOGMAR_CEILING

class TestVASOffset:
    """Verify VAS is computed as letters_correct + 30."""
    
    def test_vas_offset_is_30(self):
        assert VAS_OFFSET == 30
    
    def test_vas_equals_letters_plus_30(self):
        session = AcuitySession(eye="OD", correction="UCVA")
        # Patient gets 20 letters correct
        for i in range(20):
            session.record_response("up", "up", 0.5, 500)
        for i in range(5):  # Then fails 5
            session.record_response("up", "down", 0.5, 500)
        result = session.get_result()
        assert result.vas == result.etdrs_letter_score + VAS_OFFSET

class TestDecimalVA:
    """Verify decimal VA calculation."""
    
    def test_decimal_va_20_20(self):
        # logMAR 0.0 → decimal VA 1.0
        session = AcuitySession(eye="OD", correction="UCVA", start_logmar=0.0)
        result = session.get_result()
        assert abs(result.decimal_va - 1.0) < 0.01
    
    def test_decimal_va_20_40(self):
        # logMAR 0.3 → decimal VA 0.5
        session = AcuitySession(eye="OD", correction="UCVA", start_logmar=0.3)
        result = session.get_result()
        assert abs(result.decimal_va - 0.5) < 0.01

class TestLowVisionFallback:
    """Verify low-vision fallback paths."""
    
    def test_cf_category_recorded(self):
        session = AcuitySession(eye="OD", correction="UCVA")
        session.enter_reduced_distance_mode()
        session.record_low_vision_category(LowVisionCategory.CF)
        result = session.get_result()
        assert result.low_vision_category == LowVisionCategory.CF
    
    def test_all_categories_supported(self):
        for cat in LowVisionCategory:
            session = AcuitySession(eye="OD", correction="UCVA")
            session.record_low_vision_category(cat)
            result = session.get_result()
            assert result.low_vision_category == cat

class TestPerEyeIsolation:
    """Verify OD and OS sessions are independent."""
    
    def test_od_os_never_merge(self):
        od_session = AcuitySession(eye="OD", correction="UCVA")
        os_session = AcuitySession(eye="OS", correction="UCVA")
        
        # OD gets 10 correct
        for _ in range(10):
            od_session.record_response("up", "up", 0.5, 500)
        
        # OS gets 5 correct
        for _ in range(5):
            os_session.record_response("up", "up", 0.5, 500)
        
        od_result = od_session.get_result()
        os_result = os_session.get_result()
        
        assert od_result.etdrs_letter_score == 10
        assert os_result.etdrs_letter_score == 5
        assert od_result.eye == "OD"
        assert os_result.eye == "OS"

class TestDistanceRecording:
    """Verify distance is recorded per trial."""
    
    def test_distance_stored_per_trial(self):
        session = AcuitySession(eye="OD", correction="UCVA")
        session.record_response("up", "up", 0.45, 500)
        session.record_response("up", "up", 0.52, 500)
        
        result = session.get_result()
        assert result.trials[0].distance_m == 0.45
        assert result.trials[1].distance_m == 0.52

class TestTermination:
    """Verify termination rule based on error rate."""
    
    def test_terminates_on_majority_wrong(self):
        session = AcuitySession(eye="OD", correction="UCVA")
        # Get 3/5 wrong on current line (60% error rate)
        session.record_response("up", "up", 0.5, 500)    # correct
        session.record_response("down", "up", 0.5, 500)  # wrong
        session.record_response("left", "up", 0.5, 500)  # wrong
        session.record_response("right", "up", 0.5, 500) # wrong
        session.record_response("up", "up", 0.5, 500)    # correct
        
        # Should terminate (60% > TERMINATION_ERROR_THRESHOLD)
        assert session.should_terminate()
```

#### Acceptance Criteria:
- [ ] All tests pass
- [ ] Ceiling test explicitly asserts `LOGMAR_CEILING == 1.0`
- [ ] VAS test verifies `+30` offset
- [ ] Low-vision tests cover all 4 categories
- [ ] Per-eye isolation tested
- [ ] Distance recording per trial tested
- [ ] Termination rule tested

---

## Phase 3: Integrity Monitor

### Task 3.1: TestIntegrityMonitor Class
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 6-8 hours |
| **Dependencies** | Phase 2 complete |
| **Output** | `integrity/monitor.py` |
| **Blocking** | Tasks 4.4 (WS server), 7.3 (Test screen) |

#### What to do:
Implement the pause/resume state machine with six distinct flags.

**Real-Time Pause Flags (immediate pause):**
| Flag | Trigger | Debounce | Message |
|------|---------|----------|---------|
| `face_loss` | No face detected | >2 seconds | "No face detected — please face the screen" |
| `multiple_faces` | >1 face in frame | Immediate | "Multiple faces detected — only the patient should be in frame" |
| `gaze_off_screen` | Head yaw >20° | >1 second | "Please look at the screen" |
| `fellow_eye_open` | Non-tested eye open | >0.5 second | "Please keep your [left/right] eye closed" |
| `fullscreen_exit` | Window not fullscreen | Immediate | "Please return to fullscreen" |

**Report-Only Flags (no pause, logged for report):**
| Flag | Trigger |
|------|---------|
| `fast_answer` | Response <300ms |
| `answer_pattern_suspicious` | Non-random response pattern (same direction streak, rotating pattern) |
| `distance_face_mismatch` | Ultrasonic distance vs face box size mismatch |
| `scripted_timing_suspected` | Near-identical response intervals |
| `distance_drift_mid_trial` | Distance changed significantly during trial → invalidate letter |

**Class Interface:**
```python
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Callable
import time

class IntegrityFlag(Enum):
    FACE_LOSS = "face_loss"
    MULTIPLE_FACES = "multiple_faces"
    GAZE_OFF_SCREEN = "gaze_off_screen"
    FELLOW_EYE_OPEN = "fellow_eye_open"
    FULLSCREEN_EXIT = "fullscreen_exit"
    FAST_ANSWER = "fast_answer"
    ANSWER_PATTERN_SUSPICIOUS = "answer_pattern_suspicious"
    DISTANCE_FACE_MISMATCH = "distance_face_mismatch"
    SCRIPTED_TIMING_SUSPECTED = "scripted_timing_suspected"
    DISTANCE_DRIFT_MID_TRIAL = "distance_drift_mid_trial"

@dataclass
class PauseEvent:
    """Record of a pause occurrence."""
    flag: IntegrityFlag
    start_time: float
    end_time: Optional[float]  # None if still paused
    duration_s: Optional[float]

@dataclass
class AttentionState:
    """Current attention state from vision subsystem."""
    face_detected: bool
    face_count: int
    head_yaw_deg: float
    left_eye_open: bool
    right_eye_open: bool
    face_box_area_px: int  # For distance/face mismatch check

class TestIntegrityMonitor:
    """
    Owns all pause/resume logic for test integrity.
    
    Single-severity model: any pause-worthy flag immediately pauses.
    Resume requires all conditions OK continuously for RESUME_STABILITY_HOLD_S.
    """
    
    def __init__(
        self,
        tested_eye: Literal["OD", "OS"],
        on_pause: Callable[[IntegrityFlag, str], None],
        on_resume: Callable[[], None],
    ):
        """
        Initialize monitor.
        
        Args:
            tested_eye: Which eye is being tested (to check fellow eye)
            on_pause: Callback when pause triggered (flag, message)
            on_resume: Callback when conditions stabilize and test resumes
        """
        ...
    
    def update_attention(self, state: AttentionState) -> None:
        """
        Process new attention state from vision subsystem.
        Called at ~6-8Hz (after each face detection inference).
        """
        ...
    
    def update_distance(self, distance_m: float) -> None:
        """
        Process new distance reading.
        Called at ~17Hz from ultrasonic sensor.
        """
        ...
    
    def record_response(
        self, 
        response_time_ms: float,
        direction: str,
        trial_start_distance_m: float,
        trial_end_distance_m: float,
    ) -> List[IntegrityFlag]:
        """
        Check response for integrity issues.
        
        Returns list of flags triggered (may be empty).
        `fast_answer` and `distance_drift_mid_trial` are checked here.
        """
        ...
    
    def check_fullscreen(self, is_fullscreen: bool) -> None:
        """Update fullscreen state from frontend."""
        ...
    
    def is_paused(self) -> bool:
        """Check if test is currently paused."""
        ...
    
    def get_pause_events(self) -> List[PauseEvent]:
        """Get all pause events for inclusion in report."""
        ...
    
    def get_post_hoc_flags(self) -> List[IntegrityFlag]:
        """Get flags that don't cause pause but should appear in report."""
        ...
```

#### Acceptance Criteria:
- [ ] All 6 pause-worthy flags implemented with correct debounce
- [ ] All 5 report-only flags implemented
- [ ] Pause triggers specific message per flag
- [ ] Resume requires `RESUME_STABILITY_HOLD_S` of all-clear
- [ ] No false pause for natural blink (<500ms)
- [ ] Pause events logged with timestamp/duration for report

---

### Task 3.2: Debounce Logic
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 2-3 hours |
| **Dependencies** | Task 3.1 |
| **Output** | Within `integrity/monitor.py` |
| **Blocking** | Task 3.4 (tests) |

#### What to do:
Implement debounce windows to avoid false positives:

```python
class DebounceTimer:
    """
    Tracks how long a condition has been in violation state.
    Only triggers when violation sustained past threshold.
    """
    
    def __init__(self, threshold_s: float):
        self.threshold_s = threshold_s
        self._violation_start: Optional[float] = None
    
    def update(self, in_violation: bool) -> bool:
        """
        Update state and return whether threshold exceeded.
        
        Args:
            in_violation: True if condition is currently violated
            
        Returns:
            True if violation sustained past threshold (should trigger)
        """
        now = time.monotonic()
        
        if in_violation:
            if self._violation_start is None:
                self._violation_start = now
            return (now - self._violation_start) >= self.threshold_s
        else:
            self._violation_start = None
            return False
    
    def reset(self) -> None:
        """Reset timer (e.g., when condition clears)."""
        self._violation_start = None

# Usage in monitor:
class TestIntegrityMonitor:
    def __init__(self, ...):
        self._face_loss_debounce = DebounceTimer(FACE_LOSS_DEBOUNCE_S)
        self._gaze_debounce = DebounceTimer(GAZE_OFF_DEBOUNCE_S)
        self._fellow_eye_debounce = DebounceTimer(FELLOW_EYE_DEBOUNCE_S)
        self._resume_debounce = DebounceTimer(RESUME_STABILITY_HOLD_S)
```

**Key rules:**
1. Blink (<500ms eye closure) should NOT trigger `fellow_eye_open`
2. Brief glance away (<1s) should NOT trigger `gaze_off_screen`
3. Momentary face detection drop (<2s) should NOT trigger `face_loss`
4. Resume requires ALL conditions OK for `RESUME_STABILITY_HOLD_S`

#### Acceptance Criteria:
- [ ] Each flag has appropriate debounce window
- [ ] Natural blink does not trigger pause
- [ ] Brief glance does not trigger pause
- [ ] Resume stability hold works correctly

---

### Task 3.3: Post-Hoc Pattern Detection
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 3-4 hours |
| **Dependencies** | Task 3.1 |
| **Output** | Within `integrity/monitor.py` |
| **Blocking** | Task 5.3 (report flags section) |

#### What to do:
Implement pattern detection that runs at end of test:

**`answer_pattern_suspicious`:**
```python
def check_answer_pattern(responses: List[str]) -> bool:
    """
    Check for suspicious response patterns.
    
    Flags:
    - Same direction streak ≥5 (e.g., "right,right,right,right,right")
    - Rotating pattern (e.g., "up,right,down,left,up,right,...")
    - Entropy too low (not enough randomness)
    """
    if len(responses) < 10:
        return False  # Not enough data
    
    # Check for same-direction streak
    max_streak = 1
    current_streak = 1
    for i in range(1, len(responses)):
        if responses[i] == responses[i-1]:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 1
    
    if max_streak >= 5:
        return True
    
    # Check entropy (should be close to 2.0 for 4 directions)
    from collections import Counter
    import math
    counts = Counter(responses)
    total = len(responses)
    entropy = -sum((c/total) * math.log2(c/total) for c in counts.values() if c > 0)
    
    if entropy < 1.0:  # Suspiciously low
        return True
    
    return False
```

**`scripted_timing_suspected`:**
```python
def check_scripted_timing(response_times_ms: List[float]) -> bool:
    """
    Check for suspiciously uniform response timing.
    
    Human responses have natural variance. Near-identical timing
    (e.g., every response at ~450ms ± 10ms) suggests automation.
    """
    if len(response_times_ms) < 10:
        return False
    
    import statistics
    
    # Filter to reasonable range (exclude very fast/slow outliers)
    valid_times = [t for t in response_times_ms if 300 < t < 3000]
    if len(valid_times) < 10:
        return False
    
    stdev = statistics.stdev(valid_times)
    mean = statistics.mean(valid_times)
    
    # Coefficient of variation < 5% is suspicious
    cv = stdev / mean if mean > 0 else 0
    return cv < 0.05
```

**`distance_face_mismatch`:**
```python
def check_distance_face_mismatch(
    ultrasonic_distance_m: float,
    face_box_area_px: int,
    expected_area_at_distance: Callable[[float], int],
) -> bool:
    """
    Check if ultrasonic distance matches face size.
    
    A photo held up to the screen would show a face at a fixed size
    while ultrasonic reports the person's actual distance.
    """
    expected_area = expected_area_at_distance(ultrasonic_distance_m)
    ratio = face_box_area_px / expected_area if expected_area > 0 else 1.0
    
    # More than 50% mismatch is suspicious
    return abs(ratio - 1.0) > 0.5
```

#### Acceptance Criteria:
- [ ] Same-direction streak ≥5 flagged
- [ ] Low entropy responses flagged
- [ ] Uniform timing (CV < 5%) flagged
- [ ] Distance/face mismatch flagged
- [ ] All patterns compute at test end, not mid-test

---

### Task 3.4: Integrity Monitor Tests
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 4-5 hours |
| **Dependencies** | Tasks 3.1-3.3 |
| **Output** | `tests/test_integrity_monitor.py` |
| **Blocking** | None |

```python
import pytest
from integrity.monitor import (
    TestIntegrityMonitor, 
    IntegrityFlag, 
    AttentionState,
)
from scoring.constants import (
    FACE_LOSS_DEBOUNCE_S,
    GAZE_OFF_DEBOUNCE_S,
    FELLOW_EYE_DEBOUNCE_S,
    FAST_ANSWER_THRESHOLD_MS,
)

class TestPauseTriggers:
    def test_face_loss_after_debounce(self):
        """face_loss triggers after 2+ seconds."""
        paused_flags = []
        monitor = TestIntegrityMonitor(
            tested_eye="OD",
            on_pause=lambda flag, msg: paused_flags.append(flag),
            on_resume=lambda: None,
        )
        
        # Simulate no face for 2.5 seconds
        state = AttentionState(face_detected=False, face_count=0, ...)
        for _ in range(int(2.5 / 0.1)):  # ~25 updates at 10Hz
            monitor.update_attention(state)
            time.sleep(0.1)
        
        assert IntegrityFlag.FACE_LOSS in paused_flags
    
    def test_no_false_pause_for_blink(self):
        """Natural blink (<500ms) should NOT trigger pause."""
        paused_flags = []
        monitor = TestIntegrityMonitor(
            tested_eye="OD",
            on_pause=lambda flag, msg: paused_flags.append(flag),
            on_resume=lambda: None,
        )
        
        # Simulate 300ms eye closure (normal blink)
        state_open = AttentionState(..., left_eye_open=True)
        state_closed = AttentionState(..., left_eye_open=False)
        
        monitor.update_attention(state_open)
        monitor.update_attention(state_closed)
        time.sleep(0.3)  # 300ms blink
        monitor.update_attention(state_open)
        
        assert IntegrityFlag.FELLOW_EYE_OPEN not in paused_flags

class TestResumeConditions:
    def test_resume_requires_stability_hold(self):
        """Resume needs all conditions OK for RESUME_STABILITY_HOLD_S."""
        resumed = []
        monitor = TestIntegrityMonitor(
            tested_eye="OD",
            on_pause=lambda f, m: None,
            on_resume=lambda: resumed.append(True),
        )
        
        # Trigger pause
        monitor._trigger_pause(IntegrityFlag.FACE_LOSS)
        
        # Fix condition but not for long enough
        good_state = AttentionState(face_detected=True, face_count=1, ...)
        monitor.update_attention(good_state)
        time.sleep(0.5)  # Less than RESUME_STABILITY_HOLD_S
        
        assert len(resumed) == 0  # Should NOT have resumed yet
        
        # Now wait full stability hold
        time.sleep(RESUME_STABILITY_HOLD_S)
        monitor.update_attention(good_state)
        
        assert len(resumed) == 1

class TestFastAnswer:
    def test_fast_answer_flagged(self):
        monitor = TestIntegrityMonitor(...)
        flags = monitor.record_response(
            response_time_ms=200,  # < 300ms threshold
            direction="up",
            trial_start_distance_m=0.5,
            trial_end_distance_m=0.5,
        )
        assert IntegrityFlag.FAST_ANSWER in flags
    
    def test_normal_response_not_flagged(self):
        monitor = TestIntegrityMonitor(...)
        flags = monitor.record_response(
            response_time_ms=600,  # Normal timing
            ...
        )
        assert IntegrityFlag.FAST_ANSWER not in flags
```

#### Acceptance Criteria:
- [ ] All pause triggers tested with correct debounce
- [ ] No false pause for natural blink tested
- [ ] Resume stability hold tested
- [ ] Fast answer threshold tested
- [ ] Post-hoc pattern detection tested

---

## Phase 4: Hardware Threads

*(Tasks 4.1-4.5 detailed with similar depth...)*

### Task 4.1: Ultrasonic Sensor Thread
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 4-5 hours |
| **Dependencies** | Task 1.1 (architecture doc) |
| **Output** | `sensors/ultrasonic.py` |

**Key implementation points:**
- Dedicated OS thread, started from `main.py`
- Median(3) → EMA(0.7) filter pipeline
- Skip readings outside 4cm-3.5m
- Apply systematic offsets
- Feed bounded `distance_queue` (maxsize=3, drop-oldest)
- Clean shutdown via `threading.Event`

---

### Task 4.2: Camera Thread
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 4-5 hours |
| **Dependencies** | Task 1.1 |
| **Output** | `sensors/camera.py` |

**Key implementation points:**
- Dedicated OS thread, started from `main.py`
- XRGB8888 format, drop X channel → BGR
- 30fps capture, produce 720p + 320×240 detection canvas
- Feed bounded `frame_queue` (maxsize=2)
- Never retain frame history

---

### Task 4.3: Face Detection Process
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 6-8 hours |
| **Dependencies** | Task 1.2 (benchmark decision — **COMPLETE: MediaPipe selected**) |
| **Output** | `vision/face_detection.py` |

**Key implementation points:**
- Separate `multiprocessing.Process` (GIL avoidance)
- **MediaPipe FaceDetection** (model_selection=0, short-range ≤2m)
- Run inference every `FACE_DETECT_FRAME_SKIP` frames (default 4, ~7.5Hz at 30fps)
- Output `AttentionState` to `attention_state_queue`
- Compute: face presence, count, multiple-faces flag, bounding box
- `FaceInferenceWorker` class implemented in `backend/vision/face_detection.py`

---

### Task 4.4: WebSocket Server
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 5-6 hours |
| **Dependencies** | Tasks 4.1-4.3 |
| **Output** | `server/ws_server.py` |

**Key implementation points:**
- Asyncio event loop
- Consume from `distance_queue`, `attention_state_queue`
- Send JSON updates at 15-20Hz
- Always-on camera preview (low-res JPEG ~10fps)
- Never block on queue reads

---

### Task 4.5: Main Entry Point
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 4-5 hours |
| **Dependencies** | Tasks 4.1-4.4 |
| **Output** | `backend/main.py` |

**Key implementation points:**
- SOLE place threads/processes created
- Wire queues between subsystems
- Handle SIGINT/SIGTERM → clean shutdown
- Log which threads stopped cleanly vs force-terminated

---

## Phase 5: Report Generator

### Task 5.1-5.6: Report Implementation
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 8-10 hours total |
| **Dependencies** | Phase 2, Phase 3 |
| **Output** | `report/generator.py`, `tests/test_report_generator.py` |

**Required fields (per Part 9):**
- LogMAR (continuous decimal)
- Snellen (feet + metric)
- Decimal VA
- ETDRS letter score
- VAS (letters + 30)
- WHO classification
- 95% CI (labeled screening-tier)
- Ambient light estimate
- Distance-scaling statement (verbatim)
- All integrity flags
- Report ID (`NV-YYYYMMDD-XXXX`)
- Regulatory disclaimer (verbatim)

**Report structure:**
- OD/OS as distinct rows
- UCVA/BCVA as distinct rows
- Up to 4 rows for full OU test

---

## Phase 6: Documentation

### Task 6.1: `docs/regulatory_status.md`
Track (not claim):
- MDR 2017 Test Licence: not yet filed
- IEC approval: not yet obtained
- Telemedicine Guidelines: RMP-assistive framing
- DPDPA 2023: consent flow documented
- RTO thresholds: target use case only

### Task 6.2: `docs/scoring_methodology.md`
- Clinical-standard vs Nadi-Vision comparison
- Distance-error-to-logMAR relationship
- Sensor offsets as systematic bias

### Task 6.3: `docs/concurrency_architecture.md`
- Thread/process inventory
- Queue designs
- Benchmark results
- Memory/CPU measurements

---

## Phase 7: Frontend

### Task 7.1-7.4: Frontend Implementation
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 16-20 hours total |
| **Dependencies** | Phases 2-5 complete |
| **Output** | Next.js app in `frontend/` |

**Screens:**
1. **Landing** — OD/OS/OU selection, UCVA/BCVA, prescription metadata, consent
2. **Camera Setup** — Distance stability lock, camera preview
3. **Test** — Optotype, always-on preview, pause/resume UI
4. **Results** — Full report display, PDF export

---

## Phase 8: Performance Validation

### Task 8.1-8.3: Measure and Tune
| | |
|---|---|
| **Assignee** | _______________ |
| **Estimated Effort** | 4-6 hours |
| **Dependencies** | All other phases |
| **Output** | Measurements in `docs/concurrency_architecture.md` |

**Memory Budget (4GB total):**
| Consumer | Budget | Actual |
|----------|--------|--------|
| OS + services | ~400MB | TBD |
| Chromium kiosk | ~600MB | TBD |
| Next.js | ~200MB | TBD |
| Python backend | ~600MB | TBD |
| GPU | ~200MB | TBD |
| Headroom | ~1GB | TBD |

**If over budget (BLOCKING):**
- Reduce queue sizes
- Increase `FACE_DETECT_FRAME_SKIP`
- Use lighter kiosk config
- Never rely on swap

**Validation workflow implemented:**
1. Run benchmark and save results JSON.
2. Run `python scripts/perf_validate.py --benchmark-json <path-to-results.json>`.
3. For full-system validation, run backend + frontend on Pi and capture memory/CPU with `psutil`/`top` while running a complete OD/OS workflow.

---

## Part 13 Checklist (Track Per Phase)

Use this to verify completeness at each phase:

### After Phase 2 (Scoring):
- [x] `LOGMAR_CEILING = 1.0` with test
- [x] VAS = letters + 30
- [x] Decimal VA calculation
- [x] Low-vision fallback (CF/HM/LP/NLP)
- [x] Per-eye isolation

### After Phase 3 (Integrity):
- [x] 6 pause-worthy flags with debounce
- [x] 5 post-hoc flags
- [x] Pause events logged for report

### After Phase 5 (Report):
- [x] OD/OS/OU structured table
- [x] UCVA/BCVA as distinct rows
- [x] All Part 9 fields
- [x] Disclaimer verbatim
- [x] CI labeled screening-tier

### After Phase 6 (Docs):
- [x] Regulatory status tracked (not claimed)
- [x] Scoring methodology documented
- [x] Concurrency architecture documented

### After Phase 7 (Frontend):
- [x] Consent capture
- [x] Prescription metadata fields
- [x] OD/OS/OU selection

### After Phase 8 (Validation):
- [ ] Memory within budget (requires full Pi runtime measurement)
- [ ] CPU within budget (requires full Pi runtime measurement)
- [ ] No OOM during full test (requires full Pi runtime measurement)

---

## Task Assignment Template

| Task ID | Task Name | Assignee | Status | Est. Hours | Actual Hours |
|---------|-----------|----------|--------|------------|--------------|
| 1.1 | Concurrency Architecture | | Not Started | 4-6 | |
| 1.2 | YuNet vs MediaPipe Benchmark | | Not Started | 3-4 | |
| 1.3 | Project Scaffolding | | Not Started | 2-3 | |
| 2.1 | AcuitySession Class | | Not Started | 8-10 | |
| 2.2 | Low-Vision Fallback | | Not Started | 3-4 | |
| 2.3 | Distance Correction Docs | | Not Started | 2 | |
| 2.4 | Scoring Engine Tests | | Not Started | 4-5 | |
| 3.1 | TestIntegrityMonitor Class | | Not Started | 6-8 | |
| 3.2 | Debounce Logic | | Not Started | 2-3 | |
| 3.3 | Post-Hoc Pattern Detection | | Not Started | 3-4 | |
| 3.4 | Integrity Monitor Tests | | Not Started | 4-5 | |
| 4.1 | Ultrasonic Sensor Thread | | Not Started | 4-5 | |
| 4.2 | Camera Thread | | Not Started | 4-5 | |
| 4.3 | Face Detection Process | | Not Started | 6-8 | |
| 4.4 | WebSocket Server | | Not Started | 5-6 | |
| 4.5 | Main Entry Point | | Not Started | 4-5 | |
| 5.1-5.6 | Report Generator | | Not Started | 8-10 | |
| 6.1 | Regulatory Status Doc | | Not Started | 2 | |
| 6.2 | Scoring Methodology Doc | | Not Started | 3 | |
| 6.3 | Concurrency Architecture Doc | | Not Started | 2 | |
| 7.1-7.4 | Frontend | | Not Started | 16-20 | |
| 8.1-8.3 | Performance Validation | | Not Started | 4-6 | |

**Total Estimated: ~95-120 hours**

---

## Critical Path

The following tasks are on the critical path (blocking multiple downstream tasks):

1. **Task 1.1 (Concurrency Architecture)** → blocks all Phase 4
2. **Task 1.2 (Benchmark)** → blocks Task 4.3
3. **Task 2.1 (AcuitySession)** → blocks Phase 3, 5
4. **Task 3.1 (IntegrityMonitor)** → blocks Task 4.4, Phase 7
5. **Phase 8 (Validation)** → must pass before release

Prioritize these tasks and ensure no blockers.
