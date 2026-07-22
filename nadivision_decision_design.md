# NadiVision — Vision Decision Engine Design

Replaces the previous EAR-based single-eye check. Distance is now
**ultrasonic-only** — the camera-based iris distance estimate has been
dropped entirely to keep camera load focused on face/hand inference only.

> **Note:** I don't have your `Raspberry_vision` folder in this session
> (nothing's been uploaded here), so the exact hold/pause timing values
> below are reasoned starting points, not pulled from your reference
> file. Where your base version already has tuned constants for
> pause/resume behavior, use those instead of the numbers here — the
> *structure* (hysteresis + two-tier warning) is the part that matters,
> the exact milliseconds should come from what you already validated.

---

## 1. Distance — ultrasonic only

**Why drop the camera-based estimate:** it added inference load for a
signal that's redundant once ultrasonic is reliable, and it introduced
a second failure mode (iris landmark jitter) into a value that only
needs to be "good enough," not frame-perfect. One clean sensor beats
two noisy ones fighting each other.

**Filtering pipeline (this is what kills flicker):**

1. **Range gate** — discard any reading outside a valid physical range
   (e.g. 60–600cm). Anything outside that is a bad ping, not a person.
2. **Outlier rejection (MAD-based)** — before smoothing, compare the
   new raw reading against the median of the last ~10-12 readings. If
   it deviates more than ~3.5x the median absolute deviation, discard
   it and hold the last good smoothed value instead of feeding the
   outlier into the average.
3. **Smoothing (EMA)** — only *after* outlier rejection, apply an
   exponential moving average (alpha ~0.2-0.3). This is the order that
   matters: smoothing raw (unrejected) data just spreads a bad spike
   across several frames instead of removing it.
4. **Feed the filtered value into your existing autoscale formula** —
   port your calibration constants and visual-angle math from
   `Raspberry_vision/` unchanged. The fix for flicker lives entirely in
   steps 1-3, not in the scaling formula itself.

**Sampling rate:** ultrasonic is cheap — poll every frame (or on its
own faster hardware timer independent of camera frame rate) since it
doesn't compete for CPU/camera bandwidth the way vision does.

---

## 2. Face detection — presence & count

- Run **MediaPipe Face Detection (BlazeFace, `model_selection=0`)**
  every frame — confirmed fastest option in your own benchmark (13.8ms
  avg vs YuNet's 33.5ms).
- **0 faces** → "No face detected" prompt.
- **2+ faces** → "Multiple faces detected, please clear the frame."
- **Exactly 1 face** → don't lock immediately. Require the single-face
  state to hold for a short debounce window (~5-8 consecutive frames)
  before promoting to "locked" and enabling the heavier stages (Face
  Mesh, Hands).
- **Losing lock should be slower than gaining it.** Use a longer bad-
  streak requirement to drop an existing lock (~8-10 frames) than to
  acquire one. A single dropped detection mid-test — someone blinking,
  a brief motion blur — should not restart the flow.

---

## 3. Single-eye occlusion — hand overlap + color, no EAR

EAR is removed from the decision path entirely (not tuned, removed —
it was measuring landmark geometry, which MediaPipe keeps producing
even under partial occlusion, and geometry also shifts with blinks and
head tilt that have nothing to do with covering).

**Two independent signals, fused:**

1. **Hand-landmark overlap (primary):** IoU between the Hand landmark
   bounding box and the eye-contour bounding box for the eye that
   should be covered. Ground-truth spatial check — doesn't depend on
   what the face model thinks the eye looks like.
2. **Color/skin-tone score (secondary):** compare the eye ROI's color
   distribution against a per-person skin-tone baseline, sampled once
   from the cheek/forehead at the start of the single-eye phase (not
   resampled continuously — a hand could cross that region mid-test).

**Fusion:** weighted combination (hand overlap weighted higher, e.g.
~65/35) into a rolling confidence score over the last ~15 frames,
smoothed with EMA.

**Hysteresis, not a single threshold** — this is what prevents
flicker right at the boundary:
- Score ≥ ~0.55 → treat as COVERED
- Score ≤ ~0.40 → treat as UNCOVERED
- Between the two → UNCERTAIN (don't act on this state alone, let the
  timers below decide)

---

## 4. Hold/pause behavior — not annoying, still correct

This is the part most likely to frustrate real users if built wrong,
so the goal is explicit: **the system should almost never pause on a
person who is genuinely holding still with the correct eye covered and
the other open.** Pausing should only happen for a *sustained*,
*genuine* deviation — not a blink, not a hand adjusting position, not
a momentary frame drop.

**Two-tier response, both time-gated off the UNCERTAIN state above:**

| Tier | Trigger | UI behavior |
|---|---|---|
| Soft warning | UNCERTAIN state sustained ~200-300ms | Small, non-blocking indicator (icon/border color shift) — test keeps running |
| Hard pause | UNCERTAIN state sustained ~600-800ms+ | Screen pauses/holds, prompts correction |

**Why this avoids the "doesn't detect even when staring correctly"
complaint:** the failure mode you're describing usually comes from one
of two bugs, both addressed above —
- **Threshold sitting too close to typical good-state scores**, so
  normal hand position variance crosses it constantly → the hysteresis
  gap (0.40–0.55, not a single cutoff) directly fixes this.
- **No sustained-duration requirement**, so a single bad frame (motion
  blur, momentary hand shift) triggers a pause instantly → the
  timers fix this; nothing pauses off one frame, ever.

**Recovery should be symmetric but not identical:** once paused, resume
as soon as the state is confirmed COVERED/UNCOVERED correctly for a
short confirmation window (~150-200ms) — faster to resume than to
trigger the pause in the first place, since a false pause costs more
user frustration than a slightly-early resume costs accuracy.

**Tune against real footage, not synthetic testing.** All numeric
values above (thresholds, ms windows, debounce frame counts) need to
be set against actual recorded test sessions on your hardware — if your
`Raspberry_vision` base version already has validated timing constants
for pause/resume, carry those over directly rather than re-deriving
them from scratch.

---

## 5. Staged pipeline summary (Pi 4 budget)

| Stage | Runs | Rate |
|---|---|---|
| Face Detection | Always | Every frame |
| Ultrasonic distance | Always | Every frame (independent of camera) |
| Face Mesh (ROI) | After face locked | Every 2nd-3rd frame |
| Hands (eye-ROI) | Single-eye phase only, after face locked | Every 2nd frame |

Hands never runs during OU (both-eyes) testing — that phase only needs
face gating + distance, which keeps that half of the test cheap.
