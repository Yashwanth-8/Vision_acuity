"use client";

/**
 * CameraSetupScreen.tsx
 *
 * Pre-test setup screen. In Pi mode:
 *  - Shows the live camera preview from the MJPEG endpoint served by
 *    nadivision-camera.service at http://localhost:8766/preview.
 *  - Reads face-detected status and distance from session.state (backend).
 *  - Enables "Start Test" only when a face is detected and distance is valid.
 *
 * No browser-side MediaPipe, no Kalman filter, no iris/IPD distance.
 * Distance is HC-SR04 only; all integrity decisions are backend-owned.
 */

import { motion } from "framer-motion";
import { useAppStore } from "@/lib/store";
import { useHardwareWS } from "@/lib/hardware-ws";

const MJPEG_URL = "http://localhost:8766/preview";

export default function CameraSetupScreen() {
    const setScreen = useAppStore((s) => s.setScreen);
    const sessionState = useAppStore((s) => s.sessionState);
    const { piMode } = useHardwareWS();

    const faceDetected = sessionState?.attention?.face_detected ?? false;
    const distanceM = sessionState?.distance_m;
    const distanceOk = distanceM != null && distanceM > 0.3 && distanceM < 3.5;
    const isReady = piMode && faceDetected && distanceOk;

    const distanceLabel =
        distanceM != null && distanceM > 0
            ? `${(distanceM * 100).toFixed(0)} cm`
            : "---";

    const distanceColor =
        !distanceOk ? "text-danger" :
        distanceM! < 1.5 ? "text-warning" : "text-success";

    return (
        <div className="flex min-h-screen flex-col items-center justify-center px-6">
            {/* Progress bar */}
            <div className="fixed top-0 left-0 right-0 h-1 bg-surface z-50">
                <motion.div
                    className="h-full bg-primary"
                    initial={{ width: "33%" }}
                    animate={{ width: "66%" }}
                    transition={{ duration: 0.5 }}
                />
            </div>

            <motion.div
                className="max-w-2xl w-full flex flex-col items-center gap-6"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5 }}
            >
                <div className="text-center">
                    <h2 className="text-2xl font-bold mb-1">Camera & Distance Setup</h2>
                    <p className="text-text-secondary text-sm">
                        Position yourself so your face is fully visible and you are
                        between <strong>1.5 m – 3.0 m</strong> from the screen.
                    </p>
                </div>

                {/* Camera preview */}
                <div className="relative w-full max-w-md rounded-2xl overflow-hidden bg-surface border border-white/10">
                    {piMode ? (
                        /* eslint-disable-next-line @next/next/no-img-element */
                        <img
                            src={MJPEG_URL}
                            alt="Camera preview"
                            className="w-full object-cover"
                            style={{ aspectRatio: "16/9" }}
                        />
                    ) : (
                        <div className="flex items-center justify-center bg-black/40" style={{ aspectRatio: "16/9" }}>
                            <p className="text-text-secondary text-sm">
                                Connecting to backend…
                            </p>
                        </div>
                    )}

                    {/* Face / distance status badge */}
                    <div className="absolute top-3 right-3 flex flex-col items-end gap-1">
                        <span
                            className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                                faceDetected
                                    ? "bg-success/20 text-success"
                                    : "bg-danger/20 text-danger"
                            }`}
                        >
                            {faceDetected ? "● Face detected" : "◯ No face"}
                        </span>
                        <span className={`px-2 py-0.5 rounded-full text-xs font-mono ${distanceColor} bg-black/40`}>
                            {distanceLabel}
                        </span>
                    </div>
                </div>

                {/* Status details */}
                <div className="flex gap-6 text-sm">
                    <div className="flex items-center gap-2">
                        <span className={faceDetected ? "text-success" : "text-danger"}>
                            {faceDetected ? "✓" : "✗"}
                        </span>
                        <span className="text-text-secondary">Face visible</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <span className={distanceOk ? "text-success" : "text-danger"}>
                            {distanceOk ? "✓" : "✗"}
                        </span>
                        <span className="text-text-secondary">
                            Distance {distanceLabel} (need 1.5 – 3.0 m)
                        </span>
                    </div>
                    <div className="flex items-center gap-2">
                        <span className={piMode ? "text-success" : "text-warning"}>
                            {piMode ? "✓" : "○"}
                        </span>
                        <span className="text-text-secondary">Backend connected</span>
                    </div>
                </div>

                {/* Start button */}
                <motion.button
                    onClick={() => setScreen("test")}
                    disabled={!isReady}
                    className={`px-10 py-4 rounded-2xl text-base font-semibold transition-all ${
                        isReady
                            ? "bg-primary text-black hover:bg-primary/90 cursor-pointer"
                            : "bg-surface text-text-muted cursor-not-allowed opacity-50"
                    }`}
                    whileTap={isReady ? { scale: 0.97 } : {}}
                >
                    {isReady ? "Start Test →" : "Waiting for face & distance…"}
                </motion.button>
            </motion.div>
        </div>
    );
}
