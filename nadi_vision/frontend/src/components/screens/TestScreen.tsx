"use client";

/**
 * TestScreen.tsx — Backend-driven acuity test display.
 *
 * The backend owns trial generation, scoring, integrity, and hold decisions.
 * This screen:
 *   1. Sends session.start on mount.
 *   2. Reads session.state from the Zustand store (populated by hardware-ws).
 *   3. Renders the Tumbling E at the size and direction specified by the backend.
 *   4. Sends trial.answer on arrow-key / touch press.
 *   5. Sends ui.fullscreen and ui.visibility integrity signals.
 *   6. Shows hold overlays exactly as directed by hold.paused / hold.message.
 *   7. Navigates to results when hardware-ws receives report.ready.
 */

import { useEffect, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useAppStore } from "@/lib/store";
import {
    useHardwareWS,
    sendSessionStart,
    sendTrialAnswer,
    sendFullscreenState,
    sendVisibilityState,
} from "@/lib/hardware-ws";
import { directionToRotation, mmToPx } from "@/lib/optotype";
import type { EDirection } from "@/lib/types";

// ---------------------------------------------------------------------------
// Tumbling E SVG optotype (server-specified size)
// ---------------------------------------------------------------------------

function TumblingE({ direction, sizePx }: { direction: EDirection; sizePx: number }) {
    const rotation = directionToRotation(direction);
    return (
        <motion.div
            key={direction + sizePx}
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
        >
            <svg
                width={sizePx}
                height={sizePx}
                viewBox="0 0 5 5"
                style={{ transform: `rotate(${rotation}deg)`, display: "block" }}
                aria-hidden="true"
            >
                {/* Tumbling E facing RIGHT: 5×5 grid */}
                <rect x="0" y="0" width="5" height="1" fill="currentColor" />
                <rect x="0" y="0" width="1" height="5" fill="currentColor" />
                <rect x="0" y="2" width="5" height="1" fill="currentColor" />
                <rect x="0" y="4" width="5" height="1" fill="currentColor" />
            </svg>
        </motion.div>
    );
}

// ---------------------------------------------------------------------------
// Hold overlay
// ---------------------------------------------------------------------------

function HoldOverlay({ message, distanceM }: { message: string; distanceM: number | null | undefined }) {
    return (
        <motion.div
            className="absolute inset-0 flex flex-col items-center justify-center z-50 bg-black/85"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
        >
            <div className="glass rounded-3xl px-10 py-10 flex flex-col items-center text-center max-w-xs w-full">
                <div className="w-12 h-12 rounded-full border-2 border-warning/60 flex items-center justify-center mb-5">
                    <span className="text-warning text-xl">⏸</span>
                </div>
                <p className="text-text-primary text-lg font-semibold leading-snug mb-3">
                    {message}
                </p>
                {distanceM != null && distanceM > 0 && (
                    <p className="text-text-secondary text-sm font-mono">
                        {(distanceM * 100).toFixed(0)} cm
                    </p>
                )}
            </div>
        </motion.div>
    );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function TestScreen() {
    const { sessionState, eyeTested, correctionStatus, patientInfo, calibration } =
        useAppStore();
    const { piMode } = useHardwareWS();

    const trialStartRef = useRef(Date.now());
    const wakeLockRef = useRef<WakeLockSentinel | null>(null);
    // mm/px from one-time screen calibration; clinical accuracy requires a
    // physically-verified device manifest.
    const mmPerPx = calibration?.mmPerPx ?? 0.264; // ~96 DPI fallback

    // ------------------------------------------------------------------
    // On mount: send session.start
    // ------------------------------------------------------------------
    useEffect(() => {
        sendSessionStart({
            eye: eyeTested === "OU" ? "OD" : eyeTested,
            correction: correctionStatus === "unaided" ? "UCVA" : "BCVA",
            consent: true,
            patientInfo,
        });
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    // ------------------------------------------------------------------
    // Reset trial timer whenever direction changes (new trial from backend)
    // ------------------------------------------------------------------
    useEffect(() => {
        trialStartRef.current = Date.now();
    }, [sessionState?.direction, sessionState?.trial_token]);

    // ------------------------------------------------------------------
    // Fullscreen
    // ------------------------------------------------------------------
    useEffect(() => {
        document.documentElement.requestFullscreen?.().catch(() => {});

        const onFS = () => sendFullscreenState(!!document.fullscreenElement);
        document.addEventListener("fullscreenchange", onFS);

        return () => {
            document.removeEventListener("fullscreenchange", onFS);
            document.exitFullscreen?.().catch(() => {});
        };
    }, []);

    // ------------------------------------------------------------------
    // Visibility (tab switch)
    // ------------------------------------------------------------------
    useEffect(() => {
        const onVis = () => sendVisibilityState(!document.hidden);
        document.addEventListener("visibilitychange", onVis);
        return () => document.removeEventListener("visibilitychange", onVis);
    }, []);

    // ------------------------------------------------------------------
    // WakeLock — keep screen on during test
    // ------------------------------------------------------------------
    useEffect(() => {
        if ("wakeLock" in navigator) {
            (navigator as Navigator & { wakeLock: { request(type: string): Promise<WakeLockSentinel> } })
                .wakeLock.request("screen")
                .then((lock) => { wakeLockRef.current = lock; })
                .catch(() => {});
        }
        return () => { wakeLockRef.current?.release().catch(() => {}); };
    }, []);

    // ------------------------------------------------------------------
    // Answer handler — sends trial.answer to backend
    // ------------------------------------------------------------------
    const handleAnswer = useCallback(
        (answered: EDirection) => {
            const sess = sessionState;
            if (!sess || sess.session_status !== "active") return;
            if (sess.hold.paused) return;
            if (!sess.trial_token || !sess.direction) return;

            const responseMs = Date.now() - trialStartRef.current;
            sendTrialAnswer(sess.trial_token, answered, responseMs);
        },
        [sessionState],
    );

    // ------------------------------------------------------------------
    // Keyboard listener (arrow keys + ESP32 BLE HID)
    // ------------------------------------------------------------------
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            const map: Record<string, EDirection> = {
                ArrowUp: "up",
                ArrowDown: "down",
                ArrowLeft: "left",
                ArrowRight: "right",
            };
            const dir = map[e.key];
            if (dir) {
                e.preventDefault();
                handleAnswer(dir);
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [handleAnswer]);

    // ------------------------------------------------------------------
    // Derived display values
    // ------------------------------------------------------------------
    const sess = sessionState;
    const isHeld = sess?.hold.paused ?? false;
    const holdMsg = sess?.hold.message ?? "";
    const direction = sess?.direction as EDirection | undefined;
    const eHeightMm = sess?.e_height_mm ?? 0;
    const eHeightPx = eHeightMm > 0 ? mmToPx(eHeightMm, mmPerPx) : 0;
    const distanceM = sess?.distance_m;
    const sessionStatus = sess?.session_status ?? "idle";
    const logmarEstimate = sess?.logmar_estimate ?? 1.0;

    // ------------------------------------------------------------------
    // Waiting for backend connection
    // ------------------------------------------------------------------
    if (!piMode) {
        return (
            <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-black">
                <div className="w-8 h-8 rounded-full border-2 border-primary/40 border-t-primary animate-spin" />
                <p className="text-text-secondary text-sm">Connecting to NadiVision backend…</p>
            </div>
        );
    }

    // ------------------------------------------------------------------
    // Render
    // ------------------------------------------------------------------
    return (
        <div className="relative flex min-h-screen flex-col bg-black select-none overflow-hidden text-white">

            {/* ── Hold overlay ── */}
            <AnimatePresence>
                {isHeld && (
                    <HoldOverlay message={holdMsg} distanceM={distanceM} />
                )}
            </AnimatePresence>

            {/* ── Status bar ── */}
            <div className="flex items-center justify-between px-6 py-3 border-b border-white/5 z-10">
                <span className="text-xs text-text-secondary font-mono">
                    {sess?.eye ?? "---"} · LogMAR {logmarEstimate.toFixed(2)}
                </span>
                <span className="text-xs font-mono text-text-muted">
                    {distanceM != null && distanceM > 0
                        ? `${(distanceM * 100).toFixed(0)} cm`
                        : "d = ---"}
                </span>
                <span
                    className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        sessionStatus === "active" && !isHeld
                            ? "bg-success/20 text-success"
                            : "bg-warning/20 text-warning"
                    }`}
                >
                    {sessionStatus === "active" && !isHeld ? "● Active" : "⏸ Hold"}
                </span>
            </div>

            {/* ── Optotype ── */}
            <div className="flex-1 flex items-center justify-center relative">
                <AnimatePresence mode="wait">
                    {!isHeld && direction && eHeightPx > 0 && sessionStatus === "active" && (
                        <TumblingE
                            key={sess?.trial_token}
                            direction={direction}
                            sizePx={eHeightPx}
                        />
                    )}
                </AnimatePresence>

                {sessionStatus === "idle" && (
                    <p className="text-text-secondary text-sm">
                        Waiting for session…
                    </p>
                )}
            </div>

            {/* ── Touch buttons (4-way D-pad) ── */}
            {!isHeld && sessionStatus === "active" && (
                <div className="fixed bottom-8 left-1/2 -translate-x-1/2">
                    <div className="grid grid-cols-3 grid-rows-3 gap-2 w-44 h-44">
                        {/* UP */}
                        <button
                            className="col-start-2 row-start-1 h-12 w-12 rounded-full bg-white/10 active:bg-white/30 flex items-center justify-center text-xl"
                            onPointerDown={(e) => { e.preventDefault(); handleAnswer("up"); }}
                            aria-label="Up"
                        >↑</button>
                        {/* LEFT */}
                        <button
                            className="col-start-1 row-start-2 h-12 w-12 rounded-full bg-white/10 active:bg-white/30 flex items-center justify-center text-xl"
                            onPointerDown={(e) => { e.preventDefault(); handleAnswer("left"); }}
                            aria-label="Left"
                        >←</button>
                        {/* RIGHT */}
                        <button
                            className="col-start-3 row-start-2 h-12 w-12 rounded-full bg-white/10 active:bg-white/30 flex items-center justify-center text-xl"
                            onPointerDown={(e) => { e.preventDefault(); handleAnswer("right"); }}
                            aria-label="Right"
                        >→</button>
                        {/* DOWN */}
                        <button
                            className="col-start-2 row-start-3 h-12 w-12 rounded-full bg-white/10 active:bg-white/30 flex items-center justify-center text-xl"
                            onPointerDown={(e) => { e.preventDefault(); handleAnswer("down"); }}
                            aria-label="Down"
                        >↓</button>
                    </div>
                </div>
            )}
        </div>
    );
}
