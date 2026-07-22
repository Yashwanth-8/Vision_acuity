// ---- Types for NadiVision ----

export interface PatientInfo {
    age?: number;
    gender?: "M" | "F" | "Other";
    patientId?: string;
}

// Screens that actually exist in the app
export type AppScreen =
    | "landing"
    | "camera-setup"
    | "test"
    | "results";

export type EDirection = "up" | "down" | "left" | "right";

// Acuity levels for display reference (scoring is owned by the backend)
export interface AcuityLevel {
    logMAR: number;
    snellen: string;
    arcMinPerStroke: number;
    trialsPerLevel: number;
}

// Session state received from backend via session.state message
export interface SessionState {
    type?: string;
    session_status: "idle" | "active" | "complete";
    eye?: "OD" | "OS";
    trial_token?: string;
    direction?: EDirection;
    logmar?: number;
    e_height_mm?: number;
    total_correct?: number;
    logmar_estimate?: number;
    distance_m?: number | null;
    hold: {
        paused: boolean;
        warning: boolean;
        message: string | null;
    };
    attention?: {
        face_detected: boolean;
        num_faces: number;
    };
}

// Final test result assembled from report.ready payload
export interface TestResult {
    acuitySnellen: string;
    acuityLogMAR: number;
    fractionalLogMAR: number;
    etdrsLetterScore: number;
    whoClassification: string;
    decimalVA: number;
    eyeTested: "OD" | "OS" | "OU";
    correctionStatus: "unaided" | "glasses" | "contact-lenses";
    patientInfo: PatientInfo | null;
    confidenceInterval: { lower: number; upper: number; confidence: number };
    testDistance: number;
    testDuration: number;
    date: string;
    perLevelScores: { level: AcuityLevel; correct: number; total: number }[];
    reportId?: string;
    disclaimer?: string;
}

export interface CalibrationData {
    mmPerPx: number;
    deviceLabel: string;
    calibratedAt: number;
}
