import { E_STROKES } from "./constants";
import type { EDirection } from "./types";

/**
 * Calculate the physical height (mm) of the Tumbling E for a given acuity level at a distance.
 *
 * E height  = d × tan(totalArcMin × π / (180 × 60))
 * totalArcMin = arcMinPerStroke × E_STROKES (5)
 */
export function optotypeHeightMm(
    distanceMeters: number,
    arcMinPerStroke: number
): number {
    const totalArcMin = arcMinPerStroke * E_STROKES;
    const radians = (totalArcMin * Math.PI) / (180 * 60);
    return distanceMeters * Math.tan(radians) * 1000;
}

/**
 * Convert mm to CSS pixels using calibration data.
 */
export function mmToPx(mm: number, mmPerPx: number): number {
    return mm / mmPerPx;
}

/**
 * Rotation angle (degrees) for the Tumbling E based on its facing direction.
 * The base E faces RIGHT.
 */
export function directionToRotation(dir: EDirection): number {
    switch (dir) {
        case "right": return 0;
        case "down":  return 90;
        case "left":  return 180;
        case "up":    return 270;
    }
}
