# Regulatory and Clinical Status

## Current Product Position
Nadi Vision is currently positioned as a screening-support system. It produces structured acuity outputs for clinician review and does not provide autonomous diagnosis.

## Claims Explicitly Not Made
- No independent diagnostic claim.
- No claim of MDR or IEC approval in the current codebase.
- No claim that generated reports are a substitute for clinician judgement.

## Mandatory Report Language
Every report includes a fixed disclaimer stating:
- screening-only intent,
- need for Registered Medical Practitioner review/countersign,
- no direct medico-legal/regulatory diagnostic use without clinician sign-off.

Implementation reference:
- `backend/report/generator.py` includes immutable disclaimer text.

## Compliance Workstreams (Tracking)
- MDR 2017 test license filing: pending.
- IEC pathway and device-class mapping: pending.
- Telemedicine workflow framing with clinician oversight: in progress.
- DPDPA 2023 consent, retention, and minimization controls: in progress.

## Data Governance Principles
- Explicit consent required before testing.
- Minimum necessary patient metadata only.
- Test outputs are tagged as screening-tier.
- Traceability retained via report ID (`NV-YYYYMMDD-XXXX`) and UTC timestamp.

## Operational Safeguards Implemented
- Integrity monitor can pause tests for non-compliant conditions.
- Post-hoc integrity flags are persisted in report payload.
- Distance-scaling statement is always included to avoid misinterpretation against fixed-distance charts.

## Remaining Requirements Before Regulatory Submission
- Formal clinical validation protocol execution.
- Device risk management file completion.
- Verification/validation trace matrix completion.
- Human factors and usability evidence package.
- Finalized privacy impact assessment and retention policy.
