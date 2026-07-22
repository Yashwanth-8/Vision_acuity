# NadiVision — Clinical-Validation Readiness Rebuild Plan

**Target:** Raspberry Pi 4 (4 GB), CSI camera, HC-SR04, fixed kiosk display.  
**Purpose:** complete the production-quality rebuild before entering clinical validation. This is not a claim of clinical clearance or diagnosis.

## 1. Locked product decisions

- **MediaPipe** replaces YuNet for production face detection, based on the Pi benchmark: 13.81 ms mean / 18.89 ms p95 versus YuNet's 33.50 ms mean / 52.00 ms p95.
- The Pi camera uses system Python with Picamera2; the application and MediaPipe run in the Python 3.11 uv environment.
- **OpenCV is removed from the deployed camera and application path.** Picamera2 supplies camera streams and preview encoding; MediaPipe consumes RGB NumPy frames directly. OpenCV may remain only in an optional benchmark tool.
- HC-SR04 is the **only** production distance source. Browser IPD, iris, face-width, camera distance, and Kalman fallback estimates are development-only and must never affect a Pi session.
- The backend owns trial randomisation, scoring, integrity state, report generation, and device health. The frontend is display and input only.
- Tumbling E is the sole clinical optotype for this build. Remove Landolt C from the validation build unless a separate protocol is designed and validated.

## 2. Runtime architecture and Pi optimisation

```mermaid
flowchart LRal
  CAM["Camera service\nsystem Python + Picamera2"]
  PREV["Normal-colour preview\nJPEG/MJPEG 5–10 fps"]
  RING["3-slot shared-memory ring\n320×240 RGB frames"]
  UDS["Unix-domain socket\nframe metadata + health"]
  APP["Application service\nPython 3.11 + MediaPipe\nsession / scoring / integrity"]
  UI["Kiosk frontend"]
  US["HC-SR04 worker\n17 Hz"]
  CAM --> PREV --> UI
  CAM --> RING
  CAM --> UDS --> APP
  RING --> APP
  US --> APP
  APP <--> UI
```

### 2.1 Services and ownership

| Component | Runtime | Responsibility |
|---|---|---|
| `nadivision-camera.service` | Pi OS system Python | Owns CSI camera, normal-colour preview, sampled RGB inference frames, camera recovery and health. |
| `nadivision-app.service` | Python 3.11 uv environment | MediaPipe, ultrasonic state, integrity state machine, sessions, scoring, reports, API/control WebSocket. |
| Frontend | production static/server deployment | Kiosk UI; does not score or decide integrity. |

Run them with `systemd`, restart backoff, ordered startup/shutdown, log rotation, and health reporting. Deployment must not depend on manually maintained terminals or `npm run dev`.

### 2.2 Camera and frame transport

1. Configure Picamera2 once with independent streams:
   - preview stream: normal colour, direct camera JPEG/MJPEG encoding, 5–10 fps;
   - inference stream: 320×240 RGB.
2. Verify colour order with a physical red/green/blue/skin-tone target at device bring-up. Preview encoding must not share a colour-conversion path with inference.
3. Allocate three fixed shared-memory frame slots. Publish only `{sequence, slot, monotonic_timestamp, width, height, stride, colour_order}` over a Unix-domain `SOCK_SEQPACKET` socket.
4. The application always consumes the newest complete frame and drops stale notifications. It never queues history, reads JPEG files, or blocks camera capture.
5. Remove `/tmp/nadi_bridge`, `BridgeCameraWorker`, JPEG polling, base64 preview payloads, and all repeated encode/decode paths from production.

### 2.3 Sampling and concurrency budget

- Camera capture remains at **30 fps**.
- Face Detector receives **one out of every five captured frames**: **6 Hz**. It performs no-face and multiple-face checks.
- During an active OD/OS test, Face Landmarker and Hand Landmarker receive **one out of every ten captured frames**: **3 Hz**. They provide head-pose and fellow-eye/hand-occlusion evidence.
- Sample before shared-memory notification; do not resize/copy all 30 fps frames and discard three or four later in a process queue.
- The vision worker is a dedicated process. All models load once at process start.
- HC-SR04 runs independently at roughly **17 Hz**, with bounded latest-value transport.
- Application networking and session logic run asynchronously and never perform GPIO, camera capture, image conversion, or inference work inline.
- All queues/rings are bounded; the policy for live data is latest-frame/latest-value wins.

### 2.4 Performance release gates

- Face Detector at 320×240: p95 ≤30 ms.
- Face/Hand Landmark pipeline: benchmark on the target Pi at 3 Hz; retain it only if the measured latency, freshness, CPU, RSS, and temperature meet the device budget.
- Capture-to-face/multiple-face state p95 ≤200 ms.
- Preview age p95 ≤250 ms.
- No inference backlog, no live-frame disk writes, no unbounded RSS growth, and no sustained temperature ≥80°C or throttling.
- Run a 60-minute full-session soak and restart/fault tests before clinical-validation release.

If landmark/hand checks cannot meet their acceptance gates, the clinical-validation build is blocked; it must not silently weaken the requested camera-only occlusion control.

## 3. Integrity holds, camera checks, and UI behaviour

`TestIntegrityMonitor` is the backend authority for all holds. The frontend renders its exact state and disables all answer controls while held.

| Condition | Evidence | Required UI | Session action |
|---|---|---|---|
| No face | No valid face for 2.0 s | “Face the screen to continue” | Hold input and hide/blur E; resume after 1.5 s stable recovery. |
| Multiple faces | More than one detected face after short debounce | “Only one person should be in frame” | Hold until one face is stable. |
| Stale camera/vision | Camera heartbeat missing or attention state stale | “Camera check in progress” | Block starting; hold an active session after a brief grace period. |
| Fellow eye not covered | During OD/OS, insufficient hand-over-eye and fellow-eye-not-open evidence | “Cover your left/right eye with your hand to continue” | Fail closed: hold until confidence is restored. |
| Head turned away | Validated head-pose/frontal-face rule sustained for 1 s | “Please look at the screen” | Hold until stable. |
| Fullscreen/visibility loss | Browser sends fullscreen/visibility event | “Return to the test screen” | Hold until compliant. |
| Unstable distance | HC-SR04 not stable for 3 s | “Hold still while we set your test distance” | Do not accept answers. |
| Distance movement | HC-SR04 moves beyond trial tolerance | “Please return to position and hold still” | Hold input, live-rescale under overlay, then resume same unanswered trial. |

### 3.1 Camera-only fellow-eye verification

The existing Face Detection model cannot establish eye occlusion. Add a single-eye verifier that combines:

1. an unmirrored patient-eye mapping: OD means the patient’s right eye, OS means the patient’s left eye; preview mirroring is cosmetic only;
2. Face Landmarker eye/iris/eyelid landmarks to establish that the fellow eye is not visibly open;
3. Hand Landmarker geometry showing a hand overlapping the expected fellow-eye region;
4. temporal confidence from three consecutive 3 Hz landmark samples.

If the expected hand/eye state is uncertain, block and show the explicit hold overlay. Record selected eye, expected covered eye, evidence confidence, verifier version, and hold duration. This is a camera-only verification design; it must be validated against operator ground truth across glasses, lighting, skin tones, ages, head pose, and both eye sides before any claim of verified occlusion.

Treat the attention feature as **head-pose attention**, not precise eye-gaze tracking. Do not advertise exact gaze tracking unless a separate validated gaze model is introduced.

## 4. Ultrasonic-only autoscaling

### 4.1 Sensor contract

1. HC-SR04 publishes raw distance, Median(3)+EMA(α=0.7) filtered distance, validity, age, and configuration version.
2. Verify actual GPIO wiring and measure signed sensor-to-screen/eye offsets on the enclosure. Store them in a versioned device manifest.
3. A missing, stale, out-of-range, or unstable sensor blocks test start and holds an active test.

### 4.2 Optotype behaviour

1. Require three seconds of stable filtered ultrasonic distance before unlocking a response.
2. Calculate Tumbling E physical height using the current logMAR line’s five-stroke angular size and the corrected ultrasonic distance.
3. Convert physical millimetres to pixels using the device display profile.
4. While stable, display the E at the last stable size.
5. If the patient moves, hide/blur the E and block input. Under the hold overlay, continuously calculate the E size from the latest filtered ultrasonic distance, matching the base-version behaviour.
6. After a new three-second stable hold, reveal the correctly rescaled E and continue the **same unanswered direction/trial**. Do not score, advance, invalidate, or randomise solely because the patient repositioned.
7. Record distance, sensor age, physical E size, display profile, offset version, and all distance holds per trial/session.

### 4.3 Display calibration policy

- Prototype mode may use the existing automatic display estimate.
- A clinical-validation study may start only after every enrolled device has a one-time physically verified mm-per-pixel display profile, locked in its manifest. Browser PPI heuristics can propose a value but are not the clinical-study measurement source.

## 5. Server-owned scoring and reports

### 5.1 Session interface

Replace the ad-hoc WebSocket messages with versioned session messages:

| Direction | Message | Owner |
|---|---|---|
| UI → app | `session.start` | eye, UCVA/BCVA, consent, patient/operator metadata |
| app → UI | `session.state` | current trial token, display parameters, hold state, distance, integrity/device state |
| UI → app | `trial.answer` | opaque trial token, direction, timestamp |
| UI → app | `ui.fullscreen`, `ui.visibility` | integrity signals |
| app → UI | `report.ready` | final structured report |

The server generates direction and opaque trial token. The frontend never receives a correct-answer key, calculates an acuity result, or decides a hold.

### 5.2 Scoring protocol

- Use **14** Tumbling-E lines from logMAR **1.0 through −0.3**, five symbols each.
- Use continuous ETDRS-equivalent credit:

  `LogMAR = 1.0 − (0.02 × total correctly identified symbols)`

- Credit every correct symbol across every attempted line. Remove the frontend’s legacy 3-of-5 advancement/scoring logic.
- End the current line/session at the **third wrong answer** on that five-symbol line, matching the rebuild brief’s majority-error rule. Preserve all prior correct-letter credit.
- Fix final-line handling: successful completion of the smallest supported line finalises the session; it must never loop forever.
- Keep OD and OS as independent sequential `AcuitySession` instances. OU runs OD first, then OS; never merge or average results.
- Implement low-vision fallback as a separate server state. If the largest symbol cannot be completed, guide the patient to HC-SR04-verified 1.0 m and then record CF, HM, LP, or NLP when needed. This is a non-numeric low-vision outcome.
- Do not present the current calculated confidence interval as a validated clinical CI. Before clinical validation, label uncertainty as screening-tier/provisional while preserving raw trial data for the validation analysis.

### 5.3 Report minimums

Produce OD/OS and UCVA/BCVA rows separately, with logMAR, Snellen feet/metric, decimal VA, ETDRS letter score, VAS, low-vision category, per-trial distance, device/display configuration, all integrity events, session interruption state, consent, disclaimer, report ID, timestamp, operator, and RMP review fields.

## 6. Verification and validation work

### Software and integration tests

- Continuous scoring, third-wrong termination, final-line completion, low-vision flow, OD/OS separation, and report consistency.
- Trial-token replay/rejection, malformed protocol messages, reconnect behaviour, fullscreen/visibility, and backend-only scoring.
- Shared-memory slot reuse, producer/consumer restart, drop-oldest semantics, and proof that no live-frame filesystem writes occur.
- Hold state tests proving no input is accepted during face, occlusion, attention, sensor, or distance holds; distance recovery resumes the same unanswered trial.

### Hardware and field tests

- HC-SR04 range, filtering, offset sign, stability, and physical-reference accuracy checks.
- Camera colour contract, device restart, cable disconnect/reconnect, and preview correctness.
- Landmark/hand validation dataset: correct and incorrect hand occlusion for OD/OS, multiple people, no face, glasses, lighting, skin tones, head pose, age range, and operator-labelled ground truth.
- Full end-to-end 60-minute flow: landing → setup → OD → OS → report, including thermal/RSS/CPU/device-health records.
- Fault injection: camera service restart, app restart, sensor disconnect, browser reconnect, network loss, and low disk.

## 7. Clinical-validation entry gate

Do not enter clinical validation until the released device build has: a locked hardware/display/sensor manifest; correct physical optotype-size evidence; full software and Pi performance evidence; camera-only eye-occlusion performance against ground truth; controlled scoring/reporting; a documented intended-use protocol; and a risk/validation record. Software working in a demo does not establish clinical validity.

Clinical validation must demonstrate that outputs are accurate, reliable, and clinically meaningful for the intended use and population. Reference: [FDA/IMDRF SaMD clinical-evaluation guidance](https://www.fda.gov/files/medical%20devices/published/Software-as-a-Medical-Device-%28SAMD%29--Clinical-Evaluation---Guidance-for-Industry-and-Food-and-Drug-Administration-Staff.pdf).

