import { create } from "zustand";
import type {
    AppScreen,
    CalibrationData,
    SessionState,
    TestResult,
    PatientInfo,
} from "./types";

interface AppState {
    // Navigation
    screen: AppScreen;
    setScreen: (s: AppScreen) => void;

    // Display calibration (mm/px — used to convert e_height_mm from backend to px)
    calibration: CalibrationData | null;
    setCalibration: (c: CalibrationData) => void;

    // Clinical pre-test data (sent to backend in session.start)
    eyeTested: "OD" | "OS" | "OU";
    setEyeTested: (e: "OD" | "OS" | "OU") => void;
    correctionStatus: "unaided" | "glasses" | "contact-lenses";
    setCorrectionStatus: (c: "unaided" | "glasses" | "contact-lenses") => void;
    patientInfo: PatientInfo | null;
    setPatientInfo: (p: PatientInfo | null) => void;

    // Active session state — set from backend session.state messages
    sessionState: SessionState | null;
    setSessionState: (s: SessionState) => void;

    // Final result — assembled from backend report.ready message
    testResult: TestResult | null;
    setTestResult: (r: TestResult) => void;

    // Reset between tests
    resetTest: () => void;
}

export const useAppStore = create<AppState>((set) => ({
    screen: "landing",
    setScreen: (screen) => set({ screen }),

    calibration: null,
    setCalibration: (calibration) => set({ calibration }),

    eyeTested: "OD",
    setEyeTested: (eyeTested) => set({ eyeTested }),
    correctionStatus: "unaided",
    setCorrectionStatus: (correctionStatus) => set({ correctionStatus }),
    patientInfo: null,
    setPatientInfo: (patientInfo) => set({ patientInfo }),

    sessionState: null,
    setSessionState: (sessionState) => set({ sessionState }),

    testResult: null,
    setTestResult: (testResult) => set({ testResult }),

    resetTest: () => set({
        sessionState: null,
        testResult: null,
    }),
}));
