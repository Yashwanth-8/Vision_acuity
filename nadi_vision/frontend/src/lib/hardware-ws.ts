/**
 * hardware-ws.ts
 *
 * Session-protocol WebSocket client for the NadiVision backend at ws://localhost:8765.
 *
 * Versioned message contract (Updates.md §5.1):
 *   UI  → app   session.start     eye, correction, consent, patient metadata
 *   UI  → app   trial.answer      opaque token, direction, response_time_ms
 *   UI  → app   ui.fullscreen     is_fullscreen (bool)
 *   UI  → app   ui.visibility     visible (bool)
 *   app → UI    session.state     continuous: trial params, hold state, distance
 *   app → UI    report.ready      final structured report
 *
 * The backend owns all scoring and integrity decisions.
 * The frontend only sends answers and integrity signals; it never scores.
 */

import { useEffect, useState } from "react";
import { useAppStore } from "@/lib/store";
import { ACUITY_LEVELS } from "./constants";
import type { SessionState, TestResult } from "./types";

const WS_URL = "ws://localhost:8765";
const RECONNECT_DELAY_MS = 2_000;
const CONNECT_TIMEOUT_MS = 5_000;

export interface HardwareWSState {
    /** true when connected to the Python backend */
    piMode: boolean;
}

// ---------------------------------------------------------------------------
// Singleton WebSocket manager
// ---------------------------------------------------------------------------

class HardwareWSManager {
    private static instance: HardwareWSManager | null = null;

    private ws: WebSocket | null = null;
    private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    private destroyed = false;
    private subscribers: Set<(s: HardwareWSState) => void> = new Set();
    private state: HardwareWSState = { piMode: false };

    static getInstance(): HardwareWSManager {
        if (!HardwareWSManager.instance) {
            HardwareWSManager.instance = new HardwareWSManager();
        }
        return HardwareWSManager.instance;
    }

    subscribe(cb: (s: HardwareWSState) => void): () => void {
        this.subscribers.add(cb);
        cb({ ...this.state });
        return () => this.subscribers.delete(cb);
    }

    private notify() {
        for (const cb of this.subscribers) cb({ ...this.state });
    }

    connect() {
        if (this.destroyed || this.ws?.readyState === WebSocket.OPEN) return;
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);

        try {
            this.ws = new WebSocket(WS_URL);
        } catch {
            this.scheduleReconnect();
            return;
        }

        const connectTimeout = setTimeout(() => {
            if (this.ws && this.ws.readyState !== WebSocket.OPEN) this.ws.close();
        }, CONNECT_TIMEOUT_MS);

        this.ws.onopen = () => {
            clearTimeout(connectTimeout);
            this.state = { piMode: true };
            this.notify();
        };

        this.ws.onmessage = (ev) => {
            try {
                const msg = JSON.parse(ev.data as string);
                switch (msg.type) {
                    case "session.state":
                        useAppStore.getState().setSessionState(msg as SessionState);
                        break;
                    case "report.ready":
                        this.handleReport(msg.report);
                        break;
                }
            } catch {
                // malformed — ignore
            }
        };

        this.ws.onclose = () => {
            clearTimeout(connectTimeout);
            this.state = { piMode: false };
            // Reset session state to idle so the UI shows the waiting screen
            useAppStore.getState().setSessionState({
                session_status: "idle",
                hold: { paused: false, message: null },
            });
            this.notify();
            if (!this.destroyed) this.scheduleReconnect();
        };

        this.ws.onerror = () => { /* onclose fires after onerror */ };
    }

    private handleReport(report: Record<string, unknown> | null) {
        if (!report) return;
        const rows = report.rows as Record<string, unknown>[] | undefined;
        const row = rows?.[0];
        if (!row) return;

        const store = useAppStore.getState();
        const ci = row.confidence_interval_95 as [number, number] | undefined;

        // Build per-level scores from report data for the results breakdown
        const rawPerLevel = (row.per_level_scores as Array<{ logmar: number; correct: number; total: number }> | undefined) ?? [];
        const perLevelScores = rawPerLevel
            .map((ls) => {
                const level = ACUITY_LEVELS.find((l: { logMAR: number }) =>
                    Math.abs(l.logMAR - ls.logmar) < 0.005
                );
                return level ? { level, correct: ls.correct, total: ls.total } : null;
            })
            .filter(Boolean) as { level: { logMAR: number; snellen: string; arcMinPerStroke: number; trialsPerLevel: number }; correct: number; total: number }[];

        const result: TestResult = {
            acuitySnellen: String(row.snellen_feet ?? "N/A"),
            acuityLogMAR: Number(row.logmar ?? 1.0),
            fractionalLogMAR: Number(row.logmar ?? 1.0),
            etdrsLetterScore: Number(row.etdrs_letters ?? 0),
            whoClassification: String(row.who_classification ?? "Unknown"),
            decimalVA: Number(row.decimal_va ?? 0),
            eyeTested: store.eyeTested,
            correctionStatus: store.correctionStatus,
            patientInfo: store.patientInfo,
            confidenceInterval: {
                lower: ci?.[0] ?? 0,
                upper: ci?.[1] ?? 0,
                confidence: 95,
            },
            testDistance: Number(row.avg_distance_m ?? 0),
            testDuration: 0,
            date: String(report.timestamp_utc ?? new Date().toISOString()),
            perLevelScores,
            reportId: String(report.report_id ?? ""),
            disclaimer: String(report.disclaimer ?? ""),
        };
        store.setTestResult(result);
        store.setScreen("results");
    }

    private scheduleReconnect() {
        this.reconnectTimer = setTimeout(() => this.connect(), RECONNECT_DELAY_MS);
    }

    sendMessage(msg: object) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(msg));
        }
    }

    destroy() {
        this.destroyed = true;
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
        this.ws?.close();
        HardwareWSManager.instance = null;
    }
}

// ---------------------------------------------------------------------------
// React hook
// ---------------------------------------------------------------------------

export function useHardwareWS(): HardwareWSState {
    const manager = HardwareWSManager.getInstance();
    const [state, setState] = useState<HardwareWSState>(() => ({ piMode: false }));

    useEffect(() => {
        const unsub = manager.subscribe(setState);
        manager.connect();
        return unsub;
    }, [manager]);

    return state;
}

// ---------------------------------------------------------------------------
// Imperative senders — call from event handlers and lifecycle effects
// ---------------------------------------------------------------------------

export function sendSessionStart(params: {
    eye: "OD" | "OS";
    correction: "UCVA" | "BCVA";
    consent: boolean;
    patientInfo?: { age?: number; gender?: string; patientId?: string } | null;
}) {
    HardwareWSManager.getInstance().sendMessage({ type: "session.start", ...params });
}

export function sendTrialAnswer(
    token: string,
    direction: string,
    responseTimeMs: number,
) {
    HardwareWSManager.getInstance().sendMessage({
        type: "trial.answer",
        token,
        direction,
        response_time_ms: responseTimeMs,
    });
}

export function sendFullscreenState(isFullscreen: boolean) {
    HardwareWSManager.getInstance().sendMessage({
        type: "ui.fullscreen",
        is_fullscreen: isFullscreen,
    });
}

export function sendVisibilityState(visible: boolean) {
    HardwareWSManager.getInstance().sendMessage({
        type: "ui.visibility",
        visible,
    });
}
