# Nadi Vision — Rebuild Brief (Read This Like You're Joining Fresh)

## How to use this document

Read this the way a new engineer would read a project brief on day one —
assume you know nothing about the project going in. Every section explains
**why** a piece exists before it tells you **what to build**, so you're never
implementing a requirement you don't understand the reasoning behind.

**Ground rule on the old repo:** there is an existing prototype
(`Raspberry_Vision` Folder). You may open it and read it — the way a new hire is
allowed to read a previous team's codebase for context on what was tried and
what was learned. What you may **not** do is start from it, copy its files,
extend its modules, or inherit its structure. Every file in this rebuild gets
written from a blank file, using your own judgment about the best way to meet
the requirements below. If something in the old repo turns out to be a good
idea, re-derive it and implement it your way, in the new structure — don't
port it over. Three specific facts from the old build are treated as already
proven and are called out explicitly below (HC-SR04 as the sensor, YuNet as
the face detector, Tumbling E as the optotype) — everything else about *how*
those were implemented is up for you to design correctly this time.

Where a requirement below is ambiguous or you're not sure what "correct"
means, stop and ask. This is a clinical-adjacent tool — a wrong guess here
isn't just a bug, it's a device that could mis-measure someone's eyesight.
Silent assumptions are not acceptable anywhere in this project.

---

## Part 1 — What this device actually does (read before touching code)

### The clinical problem

"Visual acuity" is the standard measure of how sharply someone can see — it's
the number an eye doctor gets from the classic "read the smallest line you
can" chart test. Globally, well over 2 billion people have some degree of
vision impairment, and a large share of that is from causes that are
preventable or correctable if caught early — mainly refractive error (needs
glasses) and cataracts. The bottleneck isn't treatment, it's **screening**:
trained optometrists and standardized charts aren't available at the scale
needed for rural clinics, school health camps, or community health worker
visits. Nadi Vision exists to make the *screening* step — not the diagnosis,
not the treatment — automatable, cheap, and self-administered, so a
community health worker with no optometry training can run a clinically
structured test and hand a doctor a real, structured result to review.

### The metric: logMAR

Visual acuity is conventionally reported as a Snellen fraction (the familiar
"20/20", "20/40" etc.), but the number that's actually useful for
computation and statistics is **logMAR** — log of the Minimum Angle of
Resolution. Smaller logMAR = better vision (0.0 is roughly "normal", higher
numbers are worse). It's on a continuous scale, which is exactly what lets a
device credit *partial* performance instead of a binary pass/fail per line —
that continuous, letter-by-letter scoring is the core of what makes this
device clinically credible instead of a toy. Section 4 below is where the
exact scoring formula lives; this paragraph is just so the term isn't opaque
when it shows up everywhere.

### The optotype: why a tumbling "E" and not letters

A standard eye chart uses letters (Snellen chart) or a mix of shapes. Nadi
Vision uses a single symbol — a capital "E" — shown at one of four rotations
(pointing up, down, left, right), and the patient indicates which way it's
pointing. This is the **Tumbling E** test, and it's used specifically because
it doesn't depend on the patient being able to read or recognize letters
(useful across literacy levels and for children), and scoring is
unambiguous — there's no "is that a C or an O" judgment call, only "which of
4 directions." This choice is already validated from the prototype and is
not up for reconsideration (see Part 8).

### What the device physically measures, and why a distance sensor is the key trick

A line on a printed eye chart works because it's a *fixed* size at a *fixed*
distance (traditionally 6 meters / 20 feet) — the chart's angular size on
your retina is what actually determines whether you can resolve it, not its
physical size in centimeters. A screen-based test has no fixed distance: the
patient could be sitting 40cm or 2m from the screen. So the device has to
know, continuously, exactly how far the patient's eyes are from the screen,
and re-scale the "E" on screen in real time so that its **angular size** (not
pixel size) always corresponds to the logMAR line currently being tested.
This real-time distance-driven auto-scaling is the genuinely novel piece of
this project — everything in Part 3 exists to make that measurement accurate
and that scaling correct.

### The rest of the pipeline, in one paragraph

Once you can show the right-sized "E" at the right distance, the rest of the
system exists to make sure the result you get is *trustworthy*: a camera
watches to confirm the right person is actually looking at the screen
(Part 5), a scoring engine turns raw answers into a clinically meaningful
number (Part 4), and a report generator packages that number with everything
a reviewing doctor needs to sanity-check it (Part 7). None of these are
optional add-ons — a visual acuity number with no confidence you were
actually testing the right person, correctly, is not a usable screening
result.

---

## Part 2 — High-level flow (what the patient/operator actually experiences)

```
1. Landing screen
   Operator enters patient info, picks which eye(s) to test (OD/OS/OU),
   whether the patient is wearing correction (glasses/contacts) during the
   test, how the non-tested eye is occluded (if single-eye), any optional
   prescription info they already have on hand, and captures consent.

2. Camera / distance setup screen
   The device shows the live distance reading from its sensor and waits
   until that reading is stable before it will let the test start — this
   avoids starting a test on a patient who's still settling into position.

3. Test screen
   The tumbling E is shown, continuously re-sized based on live distance.
   The patient answers (which direction the E points) via button/click.
   In the background, the attention/integrity system is watching the whole
   time — not just at setup — and will pause the test if something makes
   the current answer untrustworthy (patient looked away, second person
   entered frame, etc.).

4. Results / report screen
   A structured clinical report is generated: LogMAR, Snellen, WHO
   classification, confidence interval, every integrity flag that fired
   during the test, and a disclaimer that this is a screening tool, not a
   diagnosis, pending a doctor's review.
```

Everything below is the detailed "how" for each of these four stages, plus
the software engineering discipline needed to make it all run reliably on
the actual target hardware (a Raspberry Pi 4, not a laptop).

---

## Part 3 — Distance sensing and autoscaling (build this first, conceptually)

### Why this needs its own dedicated sensor

The earlier prototype first tried estimating distance *from the camera* —
using the size of the patient's face or the distance between their eyes
(interpupillary distance) in the video frame, and inferring distance from
that. This turned out to be inaccurate (roughly ±10–20cm) and computationally
expensive (tens of milliseconds of extra processing per frame, competing
with the face-detection work the camera is already doing). Switching to a
dedicated ultrasonic distance sensor (the **HC-SR04**) fixed both problems:
it measures distance directly by timing an ultrasonic pulse's echo, giving
roughly ±3mm accuracy, independent of and much cheaper than any camera-based
estimate.

**This choice is confirmed — build the ultrasonic distance path from scratch
using the HC-SR04, don't re-derive whether to use it.**

### Wiring

```
VCC  → Pin 2  (5V)
GND  → Pin 6  (GND)
TRIG → Pin 16 (GPIO 23)          direct
ECHO → 1kΩ → Pin 18 (GPIO 24) → 2.2kΩ → GND   (voltage divider: 5V → 3.3V)
```
(The voltage divider matters: the sensor's echo pin outputs 5V, but the Pi's
GPIO pins are only safe up to 3.3V — without the divider you risk damaging
the Pi.)

### Why the raw reading needs filtering, and why *this* filter

An ultrasonic sensor's raw readings aren't smooth — they occasionally spike
wildly wrong (a stray reflection off a wall or the edge of the screen bounces
the pulse back late, reporting a much larger distance than reality). This is
**spike noise**, not the smooth, bell-curve-shaped ("Gaussian") noise that a
Kalman filter is designed for — an earlier version of this project used a
Kalman filter and found it was the wrong tool for this noise shape. Two
simpler stages fix it:

1. **Median filter over the last 3 readings** — a spike that's wildly off
   from its neighbors gets thrown out because the median of 3 values isn't
   pulled by one outlier the way a mean would be.
2. **Exponential moving average (EMA) with α = 0.7** — smooths the residual
   small jitter (±1–2cm) down to about ±0.9cm, without lagging behind real
   patient movement the way a slower-moving average would.

Build the pipeline as: raw reading (skip if `None` or outside the sensor's
valid range of 4cm–3.5m) → Median(3) → EMA(α=0.7) → this filtered value is
what everything downstream uses.

### Turning distance into the correct "E" size (the actual autoscaling math)

The whole point of measuring distance is to compute, for the logMAR line
currently under test, exactly how many pixels tall the "E" needs to be *on
this screen, at this distance, for this patient* so that its **angular
size** on the retina matches the clinical standard for that line — this is
the "angular subtension" concept mentioned earlier. In practice this means:
knowing the physical pixel density of the display (PPI — auto-detect this at
landing), knowing the patient's live distance, and knowing the angular size
in arcminutes that the current logMAR line requires, then solving for pixel
height. **Report the actual distance used for every single trial** in the
scoring/report data — this is what lets the report state truthfully how the
test was actually conducted (see Part 4's distance-correction note), instead
of silently assuming a fixed distance the way a printed chart does.

### Correcting for where the sensor actually sits

The sensor doesn't sit exactly where the patient's eye is — it's mounted on
or near the screen, and the patient's eye sits some distance in front of the
screen. Two systematic (not random — this is a *consistent* offset, not
noise) corrections apply:

```
corrected_distance = raw − SENSOR_TO_SCREEN_OFFSET_M − SENSOR_TO_EYE_OFFSET_M
```

- `SENSOR_TO_SCREEN_OFFSET_M` — depends on physical mounting; default `0.0`,
  but **must be physically measured on the actual enclosure** before any
  real validation run, and the code must never silently treat the unmeasured
  default as final. Comment this clearly in the code.
- `SENSOR_TO_EYE_OFFSET_M` — accounts for the eye sitting slightly in front
  of the screen plane; default `0.013` (13mm, a reasonable anatomical
  estimate).

Document both as **named constants**, and document in
`docs/scoring_methodology.md` that this is a systematic bias, distinct from
the random noise the Median/EMA filter already handles — mixing the two up
in documentation would misrepresent where the device's error actually comes
from.

---

## Part 4 — The scoring engine (the clinical core of the device)

### Why not just "pass/fail per line"

A simple approach — "read at least 3 of 5 letters on a line correctly to
pass, then move to the next line" — is how many basic digital eye charts
work, and it's what the earlier prototype did. The problem: it throws away
information. A patient who gets 4/5 right on one line and 1/5 right on the
next is meaningfully different from one who gets 3/5 and 0/5, but a binary
gate can't represent that. The clinical standard (ETDRS-style testing) uses
**continuous, letter-by-letter credit** instead — every single correct
letter, across every line attempted, contributes a small fixed amount to the
final score. This rebuild replaces the old gate entirely with that model.

### The exact formula to implement

- **Per-letter credit:** 0.02 logMAR per correctly identified letter.
- **Formula:**
  `LogMAR = StartLineLogMAR − (0.02 × total letters correctly identified)`,
  tallied continuously across every line attempted — the patient is never
  gated at "3 of 5" before moving to the next line.
- **Chart size:** fit as many logMAR lines as the target display can
  reasonably render well. The clinical standard is 14 lines; if the device
  ends up with fewer, that deviation must be stated explicitly in
  `docs/scoring_methodology.md`, not silently absorbed.
- **Termination rule:** stop the test based on the patient's *error rate* on
  the current line (e.g., once the majority of letters on a line are wrong,
  they've reached the edge of their vision) — not a hard pass/fail gate.
- **LogMAR ceiling fixed at 1.0** (equivalent to the 20/200 line on a
  Snellen chart), not 1.3 as in some other implementations. This must be a
  named constant `LOGMAR_CEILING = 1.0` in `scoring/constants.py`, with a
  dedicated unit test asserting no scoring path can ever return a value
  below it.

### The low-vision fallback (this was entirely missing before, and is a real gap)

Some patients can't read even the largest optotype at the normal starting
distance — a naive implementation just has no result for them, which is
unacceptable for a clinical tool. Build this as an explicit part of the
scoring state machine, not a bolt-on special case:

1. If the patient can't reliably read the largest optotype at full test
   distance, move them to a defined shorter fixed distance (e.g. 1 meter)
   and try again.
2. If they still can't read it there, record one of the standard non-numeric
   clinical categories instead of a numeric logMAR: **Counting Fingers
   (CF)**, **Hand Motion (HM)**, or **Light Perception (LP) / No Light
   Perception (NLP)**. The test must never end with literally no result.

### Distance correction — say plainly what it is and isn't

Because the "E" is continuously re-scaled to actual measured distance
(Part 3), this device is doing something functionally similar to — but not
identical to — the standard ETDRS approach of testing at one fixed distance
and applying a distance-correction formula after the fact. Report the actual
distance used per trial, and state explicitly in the report/docs that this
is a *functionally equivalent alternative*, never described as literal
ETDRS-formula compliance. This distinction matters for anyone reviewing the
device's clinical validity later.

### One session per eye — never a shared or averaged score

`scoring/engine.py` should expose a session class, e.g. `AcuitySession`, and
create **one instance per eye tested**. A full "both eyes" (OU) test is two
sequential, fully independent `AcuitySession` instances (right eye first, by
convention) — never one shared/global scoring state, and their results are
never averaged or merged into a single number. (Part 6 covers why this
matters clinically, not just architecturally.)

---

## Part 5 — Camera, face detection, and attention/integrity monitoring

### Why a camera is needed at all, beyond distance

The ultrasonic sensor tells you *how far away* something is, but not *what*
it is or *whether it's looking at the screen*. Without a camera, the device
has no way to know if the patient wandered off, if someone else stepped in
to answer for them, or if the patient is looking at their phone instead of
the optotype — any of which would make the resulting score meaningless. The
camera's job is entirely about **trustworthiness of the result**, not about
measuring the eyes themselves.

### Face detection: YuNet, and two implementation details that matter

**YuNet** (a lightweight, fast face-detection model) is the confirmed choice
— it's fast enough to run repeatedly on a Raspberry Pi 4. Two details from
the prototype are worth carrying forward as known pitfalls, not just
stylistic choices:

- **Run inference on a fixed 320×240 canvas**, always downscaled to that
  size before YuNet sees the frame. This avoids a real bug in OpenCV's
  `setInputSize` coordinate-scaling on the OpenCV builds shipped with
  Raspberry Pi OS Bookworm, and also keeps inference fast (~8ms on a Pi 4).
- **Capture frames as XRGB8888, then drop the X channel.** The alternative
  formats (BGR888/RGB888) have inconsistent byte ordering across Camera
  Module 2 vs 3 and across different libcamera versions — XRGB8888 is the
  one format that reliably gives you the right byte order (as BGRX) to work
  with once you discard the unused X channel.

### Open decision: YuNet vs. MediaPipe — resolve by benchmarking, not by assumption

YuNet is confirmed as *a* working option (it's what the prototype validated),
but it is **not locked in** as the only option for this rebuild — treat the
detector/landmark model as a decision to make deliberately, the same way
threads-vs-process for inference (Part 10) is a decision to make
deliberately rather than default into.

The real tradeoff:

- **YuNet** gives a bounding box + 5 keypoints (eye centers, nose, mouth
  corners). That's enough for face presence and multiple-face detection, but
  it's coarse for `gaze_off_screen` (head-pose) and `fellow_eye_open`
  (eye-aspect-ratio) — both end up as rough heuristics built on 5 points.
  Already measured at ~8ms/inference on a Pi 4 at the fixed 320×240 canvas.
- **MediaPipe** (Face Landmarker) gives ~468 landmarks, including actual
  eyelid contours and iris points, plus a usable head-pose estimate as a
  built-in output rather than a hand-rolled heuristic — a real quality
  upgrade for exactly the two checks YuNet is weakest at. The cost is
  unknown on this specific hardware: it is a meaningfully heavier model than
  YuNet, and there's no trustworthy Pi-4-specific latency number to plan
  around without measuring it, plus it pulls in a heavier runtime
  dependency (TFLite) against the backend's ~400–700MB memory budget
  (Part 12) and needs to be confirmed to install cleanly on Bookworm aarch64
  without building from source.

**Before committing to either, benchmark both on the actual Pi 4:** load
each model once, feed it real 320×240 frames, and measure per-inference
latency and RSS memory. Use the result to decide:

- If MediaPipe's latency is in a similar range to YuNet's, prefer it — the
  landmark quality directly improves two of the six attention checks in this
  part.
- If it's meaningfully heavier but still reasonable (e.g. 25–40ms), it's
  likely still viable — compensate by raising `FACE_DETECT_FRAME_SKIP`
  further (e.g. 1-in-8 or 1-in-10 instead of 1-in-4/5); attention state
  doesn't need sub-second resolution, so a lower inference rate is an
  acceptable trade.
- If it's too heavy to fit the CPU/memory budget even after tuning the
  frame-skip, fall back to YuNet plus explicitly hand-rolled heuristics for
  gaze and eye-state, and document why in `docs/concurrency_architecture.md`
  so the tradeoff isn't silently lost.
- Whichever model is heavier tips the threads-vs-process call in Part 10
  further toward a separate process — a slower inference call sitting on a
  plain thread has more opportunity to starve the sensor/WebSocket threads
  via the GIL.

Record the benchmark numbers and the final decision in
`docs/concurrency_architecture.md` — this is a "measure, then decide" item,
not a "pick whichever sounds better" item.

### What "attention monitoring" actually needs to check

It's tempting to reduce this to "is a face visible" — that's not enough.
Build these as **six distinct, independently-flagged checks**, because each
represents a different way the test could be compromised:

1. **Face presence** (`face_loss`) — no face visible for more than 2
   seconds.
2. **Multiple faces** (`multiple_faces`) — a second person has entered the
   frame (could be someone coaching the patient).
3. **Gaze/head-pose on screen** (`gaze_off_screen`) — a face can be present
   but not actually looking at the screen. Use YuNet's landmark output (eye
   and nose coordinates) to estimate head yaw/pitch, or an eye-landmark
   symmetry heuristic, and flag when head yaw exceeds a threshold (e.g.
   >20°) for longer than a short debounce window (e.g. 1 second). This is
   deliberately a *separate* flag from `face_loss`, because "visible but not
   looking" and "not visible" call for different messages and represent
   different failure modes.
4. **Fellow-eye state during single-eye testing** (`fellow_eye_open`) — see
   Part 6 for why this needs camera verification at all.
5. **Fast-answer flag** (`fast_answer`) — a response faster than 300ms is
   implausibly quick for genuinely reading and identifying the letter's
   direction; likely a guess or a pre-known answer.
6. **Fullscreen exit** (`fullscreen_exit`) — the test window losing
   fullscreen, which could indicate the patient tabbed away to look
   something up.

### The pause/resume state machine

Build one component, `TestIntegrityMonitor`, that owns all pause/resume
logic — don't scatter this logic across the UI and the scoring engine.

- **Single-severity model — no "soft warning" tier.** Any of `face_loss`,
  `multiple_faces`, `gaze_off_screen`, or `fellow_eye_open` immediately
  pauses the active trial: freeze the timer, don't score or advance the
  current letter, and show a specific on-screen message naming exactly what
  went wrong (e.g. "No face detected — please face the screen," "Multiple
  faces detected — only the patient should be in frame," "Please look at
  the screen," "Please keep your other eye closed"). A vague generic
  "please wait" message isn't good enough — the patient/operator needs to
  know exactly what to fix.
- **Resume only once every condition is correct again, continuously** — not
  just once at the instant it's fixed. Add a short re-stabilization hold
  (all conditions correct continuously for ~1–2 seconds) before resuming, to
  avoid the test flickering between paused and resumed on a borderline
  reading — reuse the same stability-lock pattern from the setup screen
  (Part 2, stage 2) rather than inventing a second version of it.
- **Log every pause event** — which flag(s) triggered it, timestamp,
  duration — and include this in the report's integrity-flags section. A
  pause is clinical audit data, not just a transient UI event.
- **Default state is "running."** The monitor must not introduce false
  pauses for normal blinking or a brief natural glance away — only sustained
  violations past their debounce windows should ever trigger a pause. An
  overly twitchy monitor makes the device unusable, not more rigorous.

### Camera preview during the test screen, not just at setup

The earlier prototype showed a live camera preview during setup only, and
Part 2's flow doesn't currently say what happens to it once the test itself
starts. Resolve it explicitly, this way: **keep a small, constant preview
visible throughout the test screen — e.g. a small feed in the bottom-left
corner — not just during setup, and not toggled on only when a pause fires.**

The reasoning: toggling the preview stream on and off around pause events
means the WebSocket/JPEG-encoding cost becomes *variable* — a burst of
extra bandwidth and encode work exactly when the system is already reacting
to a problem (e.g. multiple faces, which is also a higher-motion moment). A
small always-on preview instead makes that cost **constant and small** —
easier to size and budget for up front (Part 12), and it never has to spin
up under exactly the conditions most likely to already be stressing the
pipeline. It also means the patient/operator can see the reason for a pause
immediately, without waiting on a stream to establish.

Concretely:
- Stream the preview at a low resolution/quality and low frame rate — this
  is for reassurance and pause-context, not diagnostic-quality video. It
  does not need to match the resolution used for YuNet/MediaPipe inference
  or the ambient-light calculation.
- Size and rate are tunable constants (alongside `FACE_DETECT_FRAME_SKIP` in
  Part 11), not hardcoded — measure their actual CPU/bandwidth cost on the
  Pi 4 and adjust rather than assuming a number is fine.
- The preview is cosmetic only — it must never be a data source for scoring,
  timing, or integrity decisions in the frontend; those all come from the
  backend's own state (Part 4's "backend is the source of truth" rule
  applies here too).

---

## Part 6 — Testing one eye at a time: OD / OS / OU, and why occlusion needs verifying

### Clinical convention, briefly

Eye doctors record acuity per eye using **OD** (oculus dexter, right eye),
**OS** (oculus sinister, left eye), and **OU** (oculus uterque, both eyes).
The earlier prototype stored "eye tested" as a single combined label — that
loses clinically important information, since the two eyes can genuinely
differ, and a doctor reviewing the report needs to see that difference, not
a blended number.

### What to build

- Landing screen offers exactly three modes: **OD**, **OS**, **OU**.
- **OU mode is two independent `AcuitySession` instances run back to back**
  (right eye first, by convention) — each producing its own LogMAR,
  confidence interval, letter count, and integrity-flag set. Never average
  or merge them (Part 4 already establishes this at the scoring-engine
  level; this section is the clinical reasoning for why).
- **Correction status** (uncorrected = UCVA, best-corrected/with
  glasses-or-contacts = BCVA) is captured at landing and rendered as **two
  distinct rows per eye tested** — up to four result rows for a full OU test
  with both correction states recorded (OD UCVA, OD BCVA, OS UCVA, OS BCVA).
  Only populate the rows that were actually run.
- **OD and OS must always render as two distinct rows**, in both
  `report/generator.py` and the results screen — never merged into one
  combined "vision score," even as a convenience shortcut.

### Verifying the eye that's supposed to be covered is actually covered

When testing one eye, something has to be blocking the other eye's vision,
and the device should know — and honestly report — how confident it can be
that this actually happened:

- **Physical occlusion (patch or occluder):** the camera can't see behind a
  patch, so this is inherently **operator-attested only**. State that
  plainly in the report — never imply camera verification that didn't
  happen.
- **Voluntary eye closure (no patch):** this *can* be camera-verified. Use
  YuNet's landmark output (or an eye-aspect-ratio heuristic derived from the
  eye landmarks) to confirm the non-tested eye stays closed throughout the
  test. If it's detected open past a short debounce window, raise
  `fellow_eye_open` and pause (Part 5).
- Report explicitly, per test, which eye was covered and by which method
  (patch / hand / voluntary closure), and whether that was camera-verified
  or operator-attested — this level of detail was previously missing
  entirely.

---

## Part 7 — Making sure the result can't be gamed

Real-time pausing (Part 5) catches problems as they happen. Some patterns of
misuse are only visible statistically, after the fact — pausing mid-test on
a *pattern* rather than a clear violation is often the wrong call, so these
are built as **additive flags** feeding the same `TestIntegrityMonitor`, but
routed to the report's integrity section rather than an immediate pause.
Decide, per flag, whether it's real-time-pause-worthy or post-hoc-only, and
document that decision — don't reflexively pause on all of them.

1. **Guessing pattern detection** (`answer_pattern_suspicious`) — repeated
   same-direction mashing, or an obviously non-random rotating pattern
   regardless of what's actually shown. Track response-direction entropy or
   repeat-streak length. **Post-hoc report flag.**
2. **Distance/face-size mismatch** (`distance_face_mismatch`) — the
   ultrasonic sensor reports a stable, plausible distance while the
   camera's face bounding-box size implies the patient is actually much
   closer or farther than that — a proxy for someone photographing the
   screen and reading from a second device held up close. **Post-hoc report
   flag.**
3. **Voice coaching** — genuinely out of scope for a camera-only,
   button/click-input system with no microphone. Document this as a known
   residual risk in `docs/regulatory_status.md` rather than implying full
   coverage; if a microphone is ever added later, this becomes detectable.
4. **Rapid identical inter-letter timing** (`scripted_timing_suspected`) —
   near-identical response intervals across many consecutive trials (e.g.
   every answer landing at ~450ms ± a few ms) is the signature of an
   automated script or a pre-timed cheat sheet, and is distinct from the
   simpler <300ms `fast_answer` flag. **Post-hoc report flag.**
5. **Browser/devtools tampering** — extend the fullscreen-exit flag to cover
   devtools opening where feasible. More fundamentally: **all scoring state
   must live server-side on the Pi**, never trusted from anything the
   frontend reports — the backend, not the browser, owns what letter was
   shown and what the patient answered, so client-side manipulation
   structurally cannot alter the score, regardless of what flags catch.
6. **Mid-line distance drift** (`distance_drift_mid_trial`) — compare the
   autoscale distance at the start of a trial vs. its end; if it drifted
   beyond a small tolerance mid-trial (patient leaning in to make the letter
   effectively larger), flag it and **invalidate that specific letter's
   credit**, not the whole test. This is real-time detection, but the
   response is narrow — invalidate the letter, don't pause the session.

---

## Part 8 — Optional prescription metadata: record only, never infer

Nadi Vision measures acuity — it does not, and must never claim to, derive a
prescription (spherical/cylindrical correction, axis, etc.) from acuity
results or camera data. That would be an unsupported diagnostic claim. What
the landing screen and report *can* do is let the operator **record**
prescription details if they already have them from an existing
prescription, purely as context for the doctor reviewing the report later:

| Field | Meaning | Nadi Vision's role |
|---|---|---|
| OD/OS | Right/left eye | Structural (Part 6) |
| SPH | Spherical power | Record only, optional |
| CYL / AXIS | Astigmatism correction + angle | Record only, optional; AXIS shown only if CYL present |
| ADD | Reading addition (presbyopia) | Record only, optional |
| PRISM | Alignment correction | Record only, optional |
| PD | Pupillary distance (mm) | Record only, optional |

No logic anywhere may infer or estimate any of these fields from acuity or
camera data — enforce this as a hard rule during code review, not just a
documentation note.

---

## Part 9 — The report: what a reviewing doctor actually needs to see

The report is the artifact a doctor ultimately signs off on, so it needs to
be self-explanatory to someone who never watched the test happen. Every
completed test must produce:

- **LogMAR** (continuous decimal) — the primary clinical number (Part 4).
- **Snellen fraction**, both feet-based (`20/40`) and metric (`6/12`) —
  feet-based is the familiar US/UK convention, metric is common elsewhere.
- **Decimal VA** (`20 / SnellenDenominator`) — the convention used in Indian
  clinical practice, which matters given the deployment context.
- **ETDRS letter score** — the raw count of correctly identified letters,
  which is the number the logMAR formula is actually built from.
- **VAS (Visual Acuity Score):** `letters correct + 30`, out of 100. The
  `+30` is a standard offset from the ETDRS scoring convention — implement
  it as a named constant with a code comment explaining what it is and
  where it comes from, never as a bare unexplained literal.
- **WHO visual impairment classification** — the standard categorical bucket
  (normal / mild / moderate / severe / blind) that the LogMAR value maps to.
- **95% confidence interval on the logMAR score**, computed from the actual
  trial counts (binomial variance) — and labeled explicitly as
  **screening-tier**, never diagnostic-tier. Diagnostic-grade precision
  requires a CI of ≤0.14 logMAR (±7 letters), which a single short screening
  test does not meet, as a matter of statistical principle — not because the
  device is faulty. Say this explicitly in the report or docs.
- **Ambient light estimate** — average frame luminance from the existing
  camera feed, flagged **"Adequate"** vs **"Check lighting"** against an
  80–220 threshold band (0–255 scale). Poor lighting is a real, common
  confound in vision testing, and this is a cheap way to catch it.
- **The distance-scaling statement, verbatim in spirit:**
  > "Reported LogMAR is auto-scaled from an actual test distance of X.XXm
  > using angular subtension (ISO 8596-informed); this is not the standard
  > 4m/6m/20ft chart distance."
- **Every integrity flag that fired** (Parts 5 and 7) — fast answers, face
  loss, multiple faces, gaze off screen, fellow-eye-open, fullscreen exit,
  plus the post-hoc anomaly flags.
- **A report metadata block:** Report ID in the format `NV-YYYYMMDD-XXXX`,
  timestamp, operator field, an RMP (Registered Medical Practitioner) review
  / countersignature field (left blank, pending), and a patient consent
  checkbox tied to actual consent capture at landing (never pre-checked).
- **The regulatory disclaimer, rendered verbatim on every single report —
  never paraphrased, shortened, or omitted:**

```
This report is generated by Nadi Vision, an automated visual acuity
screening device. It is formatted per standard ophthalmic documentation
practice for ease of clinical review, but does not constitute a medical
diagnosis. Results should be reviewed and countersigned by a Registered
Medical Practitioner before use in any clinical, legal, or regulatory
context.
```

---

## Part 10 — Why the software architecture matters this much (Pi 4, not a laptop)

### The constraint that shapes everything below

Everything above describes *what* the device does. This part is about *how*
it has to be built so it actually runs reliably on the real target: a
**Raspberry Pi 4 Model B, 4GB RAM** (not the 8GB variant — assume the lower
number everywhere). A Pi 4 has 4 CPU cores and a hard memory ceiling. If the
camera capture, face detection, distance polling, WebSocket streaming to the
frontend, and scoring logic all compete carelessly for the same thread or
the same memory budget, the device will stutter, drop frames, or eventually
get killed by the OS's out-of-memory killer mid-test — which, for a
clinical-adjacent device, is a correctness failure, not just a performance
annoyance.

### The core rule: one dedicated execution unit per subsystem

Every major subsystem — distance sensing, camera capture, face/vision
inference, the WebSocket server, and the integrity monitor's evaluation loop
(if it runs independently) — gets **its own dedicated OS thread or process,
never shared, never piggybacked onto another subsystem's loop**. No
subsystem should silently run "inside" another's thread just because it
seemed convenient in the moment — that convenience is exactly what causes
one slow subsystem to stall an unrelated one.

All of these threads/processes are created and started from **one single
place**, `backend/main.py` — never spun up ad hoc from inside other modules.
They talk to each other only through **thread-safe, bounded queues** — never
shared mutable state, never a global variable read from two threads at once.

**Propose the concrete concurrency plan and get it confirmed before writing
any implementation code.** This is step 1 of the deliverable order in
Part 15 — not a suggestion to skip if it feels obvious.

### The required thread/process inventory

| Subsystem | Execution unit | Started from | Feeds | Consumes |
|---|---|---|---|---|
| `sensors/ultrasonic.py` (HC-SR04 poll + Median/EMA) | dedicated OS thread | `main.py` | `distance_queue` | GPIO reads only |
| `sensors/camera.py` (frame capture) | dedicated OS thread | `main.py` | `frame_queue` | camera hardware only |
| `vision/face_detection.py` (YuNet + gaze + eye-state) | dedicated thread **or** process — decide explicitly, see below | `main.py` | `attention_state_queue` | `frame_queue` |
| `integrity/monitor.py` (pause/resume state machine) | dedicated thread, or an asyncio task if purely queue-driven and non-blocking — decide explicitly | `main.py` | `integrity_flag_queue` / pause-state to WS layer | `attention_state_queue`, `distance_queue`, frontend event inputs |
| `server/ws_server.py` (WebSocket loop) | asyncio event loop, in its own thread if `main.py` isn't already asyncio-native | `main.py` | outbound WS messages to frontend | `distance_queue`, `attention_state_queue`, integrity/scoring state |
| `scoring/engine.py` (`AcuitySession`) | no thread of its own — pure, synchronous, called in response to events | `main.py` (one instance per eye session) | scored results to the report generator | trial answers from the frontend via WS |

### Decisions you must make explicitly, not by default

- **Threads vs. a separate process for YuNet inference.** Python's GIL
  (Global Interpreter Lock) means a CPU-heavy inference loop running as a
  plain thread can still starve other threads even with 4 cores available.
  State plainly whether YuNet runs in a thread (only acceptable if the
  underlying OpenCV DNN call actually releases the GIL during its C++
  execution — verify this, don't assume it) or in a separate process via
  `multiprocessing`, passing frames/results through a
  `multiprocessing.Queue` or shared memory. A second process duplicates some
  memory but sidesteps the GIL cleanly — justify whichever you pick against
  the measured CPU/memory tradeoff, not convenience.
- **Queue design** — what connects each pair of subsystems
  (`distance_queue`, `frame_queue`, `attention_state_queue`, etc.), with a
  bounded `maxsize` on every one of them (unbounded queues are a
  memory-growth risk on a 4GB device — not acceptable even during
  prototyping) and an explicit backpressure policy (drop-oldest is
  generally correct for live sensor data — never let a slow consumer block
  a sensor thread indefinitely).
- **How the WebSocket stream to the frontend hits ~15–20Hz** for
  distance + attention updates without blocking scoring/report logic — i.e.
  what the send loop does when a frame is running late.
- **Keeping GPIO/OpenCV blocking calls off the asyncio event loop** — via a
  thread pool executor or dedicated threads that feed queues the event loop
  only ever reads from.
- **Clean shutdown, owned entirely by `main.py`.** On SIGINT/SIGTERM (or a
  clean-exit request from the frontend), `main.py` signals every
  thread/process to stop (a shared stop-event, or a sentinel value pushed
  onto each queue), joins them with a timeout, and only then exits. No
  subsystem thread is left as a daemon thread to be killed ungracefully by
  default. Log which threads stopped cleanly vs. had to be force-terminated
  — a thread that won't stop cleanly is usually a sign of a blocking call
  that isn't respecting the stop signal.

Write the confirmed plan to `docs/concurrency_architecture.md`, and keep it
updated if the design changes mid-implementation.

---

## Part 11 — Keeping the vision pipeline within the Pi's CPU budget

The Pi 4 cannot run full-rate face/gaze inference at full camera resolution
while also servicing the sensor threads and the WebSocket loop without
starving something. Concrete strategy:

- Camera capture runs at native rate (e.g. 30fps) so the live preview shown
  to the operator stays smooth.
- **YuNet inference does not run on every frame** — sample roughly 1 in
  every 4–5 captured frames (~6–8Hz inference at 30fps capture). This is
  plenty for attention-state changes, which happen on human timescales — a
  head turn or eye closure isn't a sub-100ms event.
- Always downscale to the fixed 320×240 canvas (Part 5) before inference —
  never run YuNet on a full-resolution frame.
- Make the frame-skip interval a named constant,
  `FACE_DETECT_FRAME_SKIP`, in `vision/face_detection.py` — not a hardcoded
  magic number — so it can be tuned against measured real-world CPU load.
- The HC-SR04 thread polls independently at ~17Hz; **never couple its rate
  to the camera/vision frame-skip rate.** Both feed the WebSocket server
  through their own separate queues.
- Once implemented, actually measure Pi 4 CPU utilization for the vision
  pipeline and adjust `FACE_DETECT_FRAME_SKIP` if it's starving the sensor
  threads or the WebSocket loop.
- **Never retain a history of full-resolution frames in memory.** Keep only
  the current frame plus whatever's strictly needed for the ambient-light
  calculation (Part 9) and the in-flight YuNet input; drop everything else
  immediately after use. On a 4GB device, an accidentally growing frame
  buffer is the single most likely cause of an OOM kill during a long test
  session — treat this as a correctness requirement, not a performance
  nicety.

---

## Part 12 — The Pi 4 (4GB) memory and CPU budget

Design every subsystem against this ceiling from the start — don't build
first and optimize later.

### Memory budget (4096MB total)

| Consumer | Budget | Notes |
|---|---|---|
| OS + systemd services + drivers | ~350–450MB | Raspberry Pi OS Lite/Bookworm baseline; avoid a full desktop environment |
| Chromium (kiosk mode) | ~500–800MB | Single tab, no extensions, minimal flags |
| Next.js frontend process | ~150–300MB | Production mode, not dev mode |
| Python backend (sensors + vision + scoring + WS server) | ~400–700MB | Includes OpenCV + the YuNet model in memory |
| GPU memory split (`gpu_mem`) | 128–256MB | Reserved by the GPU/VideoCore, unavailable to Linux processes |
| Safety headroom | ~800MB–1GB | Buffer against page-cache pressure and transient allocations; avoids the OOM killer during a live test |

If measured usage doesn't fit this budget, that's a design problem to fix
(smaller model, tighter queue bounds, a lighter kiosk config) — never
something to paper over with swap.

### What this implies concretely

- **No full desktop environment for the kiosk.** Boot to a minimal Wayland
  compositor in kiosk mode (e.g. `cage` or `labwc` configured for kiosk use)
  or bare X11 with no window manager beyond what's needed to run Chromium
  fullscreen — not a full GNOME/LXDE session. Document and justify whichever
  boot target is chosen.
- **Chromium kiosk flags matter** — disable extensions, disable
  inapplicable background-throttling behavior, and minimize additional
  tabs/frames/iframes (`--single-process` is generally discouraged for
  stability, so don't reach for it as a shortcut).
- **Run Next.js in production mode (`next build` + `next start`) on the
  device — never `next dev`.** Dev mode's hot-reload/compilation overhead is
  not something a 4GB device should be carrying during a live test. Consider
  a static export for UI parts that don't need server-side rendering, to
  shrink the frontend process's footprint further.
- **Use `opencv-python-headless`, not the full `opencv-python` package**, to
  avoid pulling in GUI/Qt dependencies the backend never actually uses.
- **Load the YuNet model once at process start** — never per-frame or
  per-inference-call — and confirm memory stays flat across a multi-minute
  test session (no per-frame allocation growth) as an actual measurement,
  not an assumption.
- **Every queue (Part 10) needs an explicit bounded `maxsize`** (e.g. 2–3
  for frame queues, small integer counts for distance/attention-state
  queues), with a documented full-queue policy (drop-oldest for live sensor
  data; never block a sensor thread indefinitely for a slow consumer).
- **Avoid depending on swap.** Swap should exist only as a small safety net
  (e.g. via `zram`), never as working memory — repeated swapping on an SD
  card hurts both performance and the card's lifespan. If the design
  requires swap to stay under the OOM threshold in normal operation, the
  working set is too large and needs to shrink — that's not a reason to add
  more swap.
- **Run the backend as a systemd service with a `MemoryMax` cgroup limit**
  (set comfortably below the budget above, e.g. 900MB–1GB) so a memory leak
  fails loudly and restarts the service, rather than silently degrading the
  whole device or OOM-killing an unrelated process.
- **CPU note:** with 4 cores total, expect roughly one core's worth of
  headroom once camera capture, YuNet inference, sensor polling, and the
  WebSocket loop are all running simultaneously — tune the frame-skip
  constant and the thread/process split (Part 10) with this in mind, and
  check actual measured CPU utilization against it before considering the
  rebuild done.

### What to actually measure and report back

Produce a short table of measured RSS memory and CPU% per long-running
process (backend, Next.js, Chromium) during a representative full test run
(landing → setup → OU test → results), and compare it against the budget
table above in `docs/concurrency_architecture.md`. Flag any consumer that's
meaningfully over budget as an **open issue to resolve**, not a footnote to
note and move past — this is part of considering the rebuild complete
(Part 15, step 9).

---

## Part 13 — The full legitimacy/regulatory checklist

This exists because a clinical-adjacent device that overclaims what it's
verified is worse than one that's honest about its limits. Mark each item
**[code]** (must exist as working code) or **[doc]** (must exist in `docs/`,
and must never be faked or auto-filled as "complete" in code — regulatory
approval is a real-world process, not something code can grant itself).

**Report structure — [code], `report/generator.py`**
- [ ] Institutional header block (facility name, department — blank/
  templated if there's no fixed facility)
- [ ] Patient ID block (name/ID, age, sex, date, time)
- [ ] OD/OS/OU structured table (Part 6)
- [ ] UCVA vs BCVA as distinct rows (Part 6)
- [ ] Test method + conditions stated explicitly: chart type, distance,
  illumination (fed by the ambient-light module)
- [ ] Correction status as an explicit labeled field
- [ ] Scoring detail: LogMAR, Snellen (ft + metric), decimal VA
- [ ] Impression/summary line via WHO classification
- [ ] Plan/recommendation field: follow-up/referral checkboxes
- [ ] Report ID + timestamp (`NV-YYYYMMDD-XXXX`)

**Transparency block — [code], rendered verbatim on every report**
- [ ] Disclaimer block exactly as specified in Part 9
- [ ] Screened By / Reviewed By (RMP name + registration number, blank
  pending)
- [ ] Patient consent Yes/No, tied to actual consent capture at landing

**Legal/clinical legitimacy — [doc only, never fake in code]**
- [ ] `docs/regulatory_status.md` states RMP countersignature, MDR 2017
  licensing, and IEC approval are **not achievable through software** and
  are tracked as external, unresolved prerequisites — never auto-filled or
  implied complete

**Regulatory tracking — [doc], `docs/regulatory_status.md`**
- [ ] MDR 2017 Test Licence (Form MD-12/13) — status: not yet filed
- [ ] MDR 2017 Clinical Investigation (Form MD-22/23) — status: not yet
  filed, blocked on IEC
- [ ] IEC approval (Rule 50) — status: not yet obtained
- [ ] Telemedicine Practice Guidelines 2020 — document that the system must
  always be framed as RMP-assistive, never an autonomous consult/diagnosis;
  no code path may present a result as a final diagnosis
- [ ] DPDPA 2023 — consent flow documented; data minimization stated
  (collect only patient ID/age/gender/results/camera-derived data actually
  needed)
- [ ] Motor Vehicles Act / RTO thresholds (6/12, 6/9, 6/6, etc.) documented
  as a target use case only, not a certified pathway

**Scoring/error-tolerance framework — [doc + code]**
- [ ] `docs/scoring_methodology.md` reproduces the clinical-standard-vs-
  Nadi-Vision comparison table (chart structure, per-letter scoring,
  formula, distance correction, termination rule, low-vision fallback,
  derived outputs) kept accurate as features land
- [ ] Report generator labels every individual report's CI as
  screening-tier, never diagnostic-tier, with the ≤0.14 logMAR / ±7 letters
  diagnostic threshold stated as *not met by design*, not by device error
- [ ] Distance-error-to-logMAR-shift relationship documented (~0.004 logMAR
  shift per cm error at 1m, ~0.01 at 40cm)
- [ ] Sensor-to-eye/screen offset bias documented as a systematic bias,
  distinct from random sensor noise, in code comments and docs

**Prescription metadata — [code]**
- [ ] Optional SPH/CYL/AXIS/ADD/PRISM/PD fields on landing/report,
  record-only, never computed or inferred

---

## Part 14 — Project structure

```
nadi_vision/
├── backend/
│   ├── sensors/
│   │   ├── ultrasonic.py        # HC-SR04 read + Median/EMA filter, own thread
│   │   └── camera.py            # frame capture, own thread
│   ├── vision/
│   │   └── face_detection.py    # YuNet inference, gaze/head-pose, eye-state, attention state machine
│   ├── scoring/
│   │   ├── engine.py            # AcuitySession — continuous ETDRS-equivalent scoring, pure functions, unit-testable
│   │   └── constants.py         # LOGMAR_CEILING, VAS offset, sensor offsets, thresholds — all named
│   ├── integrity/
│   │   └── monitor.py           # TestIntegrityMonitor — flags in, pause/resume state out
│   ├── report/
│   │   └── generator.py         # builds the structured report object + PDF/HTML render
│   ├── server/
│   │   └── ws_server.py         # asyncio WebSocket server, consumes queues from sensor/vision threads
│   ├── config.py                # all named constants, offsets, thresholds in one place
│   └── main.py                  # SOLE entry point: instantiates every subsystem, starts its
│                                  # dedicated thread/process, wires the queues between them,
│                                  # and owns clean shutdown (Part 10's exact inventory)
├── frontend/                    # Next.js, Chromium kiosk
│   ├── app/
│   │   ├── landing/             # patient info, eye mode, correction status, prescription metadata, consent
│   │   ├── setup/                # camera/distance stability-lock screen
│   │   ├── test/                 # optotype + integrity monitor UI
│   │   └── results/               # OD/OS/UCVA/BCVA rows, disclaimer, report view
│   └── lib/ws_client.ts
├── tests/
│   ├── test_scoring_engine.py     # ceiling, VAS offset, decimal VA, distance correction, low-vision fallback
│   ├── test_integrity_monitor.py  # pause/resume debounce, flag-to-pause mapping, false-pause avoidance
│   └── test_report_generator.py   # OD/OS/OU row structure, UCVA/BCVA split, disclaimer block, Report ID format
├── docs/
│   ├── regulatory_status.md       # MDR 2017 / IEC / DPDPA / RTO status tracker, kept current
│   ├── scoring_methodology.md     # clinical-standard-vs-Nadi-Vision comparison, error tolerance tables
│   └── concurrency_architecture.md # the threading/async plan, once confirmed
└── README.md
```

Feel free to propose improvements to this layout, but keep the underlying
principle — modular by subsystem, no subsystem reaching into another's
internals — intact regardless of exact file names.

---

## Part 15 — Build order (do not reorder these steps)

1. **Propose the concurrency architecture and module interfaces — no
   implementation code yet.** Wait for explicit confirmation before
   proceeding (Part 10).
2. Scaffold the module structure with stub interfaces and the constants
   file (`scoring/constants.py`, `config.py`).
3. Implement `scoring/engine.py` as a per-eye `AcuitySession` class, plus
   `tests/test_scoring_engine.py`, **first** — this is the highest-risk
   correctness area (ceiling, VAS offset, decimal VA, distance correction,
   low-vision fallback, termination rule). Get this right before anything
   depends on it.
4. Implement `integrity/monitor.py` (pause/resume state machine, debounce
   logic) with `tests/test_integrity_monitor.py`.
5. Implement the sensor threads (`sensors/ultrasonic.py`,
   `sensors/camera.py`, `vision/face_detection.py`), then WebSocket server
   wiring (`server/ws_server.py`), per the confirmed concurrency plan.
6. Implement `report/generator.py` with the full Part 9 field set, the
   OD/OS/OU structured table, UCVA/BCVA split, and the verbatim disclaimer
   block — check it against the full Part 13 checklist before considering
   it done. Write `tests/test_report_generator.py` alongside it.
7. Write `docs/regulatory_status.md` and `docs/scoring_methodology.md`.
8. Wire the frontend last: landing → setup → test → results, adding OD/OS/
   OU selection, correction-status capture, occlusion-method capture,
   prescription metadata fields, and consent capture at landing. You may
   reference the old prototype's UI screens for design ideas at this stage
   only — rebuild the components fresh against the new backend interfaces.
9. Measure and report actual Pi 4 CPU **and RAM** utilization per process
   (backend, frontend/Next.js, Chromium kiosk) during a full end-to-end test
   run; tune `FACE_DETECT_FRAME_SKIP` and queue sizes accordingly, and
   record both measurements against the Part 12 budget in
   `docs/concurrency_architecture.md`. If actual usage exceeds budget, this
   is a **blocking issue** to resolve before considering the rebuild done —
   not a note for later.

At each step, state explicitly which Part 13 checklist items are now
satisfied — reconcile against the checklist as you go, not only at the end.

---

## Part 16 — Explicitly out of scope

Do not attempt to obtain IEC approval, file MDR 2017 forms, or claim
regulatory clearance anywhere in code or docs. Only track status in
`docs/regulatory_status.md`, and keep all disclaimer language accurate to
"not yet obtained."

---

## Part 17 — How to actually work through this

- Follow the **Build Order** in Part 15 step by step — don't jump ahead to
  frontend or report work before the scoring engine and its tests exist.
- Re-read the relevant Part above before starting each new step — don't
  rely on memory of an earlier summary of it.
- When a step finishes, state which Part 13 checklist items it closes and
  which Part 12 budget numbers it affects, if any.
- If a requirement here turns out to be wrong, impractical on the actual Pi
  4 hardware, or in tension with another requirement, say so directly and
  propose an alternative — don't quietly work around it.
- Treat the old `Raspberry_Vision` repo the way a new hire treats a previous
  team's abandoned codebase: worth reading for context on what was tried,
  never worth copying from. Every file in this rebuild is written fresh.
