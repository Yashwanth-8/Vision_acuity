# Scoring Methodology

## Purpose
Nadi Vision provides screening-tier visual acuity estimates using per-letter continuous scoring aligned with ETDRS-style credit. It is intended for triage and structured documentation, not autonomous diagnosis.

## Core Scoring Rule
- Start level: LogMAR 1.0.
- Per correct optotype: credit 0.02 LogMAR.
- Continuous LogMAR estimate:
	- `logmar = min(1.0, start_logmar - 0.02 * letters_correct)`
- Visual Acuity Score (VAS):
	- `VAS = letters_correct + 30`

This keeps the clinical granularity of per-letter scoring rather than coarse line-only pass/fail transitions.

## Termination Logic
- Five trials per line (`LETTERS_PER_LINE = 5`).
- Stop when line error rate exceeds 60% (`TERMINATION_ERROR_THRESHOLD = 0.6`).
- Continue to next line when the error threshold is not exceeded.

This policy balances test duration and reliability on constrained devices.

## Derived Outputs
For numeric LogMAR outcomes:
- Snellen feet: `20 / round(20 * 10^logmar)`
- Snellen metric: `6 / round(6 * 10^logmar)`
- Decimal VA: `10^(-logmar)`
- WHO class bands from LogMAR thresholds.

For low-vision outcomes:
- If numeric endpoint is not attainable, category outputs are used:
	- CF (Counting Fingers)
	- HM (Hand Motion)
	- LP (Light Perception)
	- NLP (No Light Perception)

## Confidence Interval (95%)
The implementation computes a screening-tier interval around the final LogMAR using response proportion variance from valid trials:
- Let `p = correct / total_valid_trials`
- `SE(p) = sqrt(p * (1 - p) / total_valid_trials)`
- Convert to LogMAR scale and apply `1.96 * SE` margin.

This CI is useful for uncertainty communication but should not be treated as a confirmatory clinical confidence statement.

## Distance Handling and Scaling Statement
Reported acuity is auto-scaled from the measured test distance using angular-subtension equivalence (ISO 8596-informed). The generated report includes a mandatory statement that the test distance is not the standard 4 m / 6 m / 20 ft chart distance.

## Trial Invalidation
Trials can be invalidated and excluded from scoring for integrity reasons (for example, mid-trial distance drift beyond tolerance). Invalidated trials are retained in raw logs for auditability.

## Known Bias Sources
- Sensor-to-screen and sensor-to-eye offsets introduce systematic error if unmeasured.
- Ambient lighting can reduce effective contrast and increase response variance.
- Head pose and occlusion can affect attention assessment quality.

Mitigation:
- Fixed constants in `backend/scoring/constants.py`.
- Integrity pause/resume and post-hoc flags.
- Explicit screening-only report disclaimer.

## Regulatory Positioning
This methodology supports structured screening output and clinician review workflows. It does not claim independent diagnostic authority.
