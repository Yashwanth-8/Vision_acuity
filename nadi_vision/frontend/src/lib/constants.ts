import type { AcuityLevel } from "./types";

// Full ETDRS acuity chart — 14 lines, uniform 0.1 LogMAR steps
// arcMinPerStroke = 10^logMAR  (exact pinhole formula per ISO 8596)
export const ACUITY_LEVELS: AcuityLevel[] = [
    { logMAR: 1.0,  snellen: "20/200",  arcMinPerStroke: 10.000, trialsPerLevel: 5 },
    { logMAR: 0.9,  snellen: "20/160",  arcMinPerStroke: 7.943,  trialsPerLevel: 5 },
    { logMAR: 0.8,  snellen: "20/125",  arcMinPerStroke: 6.310,  trialsPerLevel: 5 },
    { logMAR: 0.7,  snellen: "20/100",  arcMinPerStroke: 5.012,  trialsPerLevel: 5 },
    { logMAR: 0.6,  snellen: "20/80",   arcMinPerStroke: 3.981,  trialsPerLevel: 5 },
    { logMAR: 0.5,  snellen: "20/63",   arcMinPerStroke: 3.162,  trialsPerLevel: 5 },
    { logMAR: 0.4,  snellen: "20/50",   arcMinPerStroke: 2.512,  trialsPerLevel: 5 },
    { logMAR: 0.3,  snellen: "20/40",   arcMinPerStroke: 1.995,  trialsPerLevel: 5 },
    { logMAR: 0.2,  snellen: "20/32",   arcMinPerStroke: 1.585,  trialsPerLevel: 5 },
    { logMAR: 0.1,  snellen: "20/25",   arcMinPerStroke: 1.259,  trialsPerLevel: 5 },
    { logMAR: 0.0,  snellen: "20/20",   arcMinPerStroke: 1.000,  trialsPerLevel: 5 },
    { logMAR: -0.1, snellen: "20/16",   arcMinPerStroke: 0.794,  trialsPerLevel: 5 },
    { logMAR: -0.2, snellen: "20/12.5", arcMinPerStroke: 0.631,  trialsPerLevel: 5 },
    { logMAR: -0.3, snellen: "20/10",   arcMinPerStroke: 0.501,  trialsPerLevel: 5 },
];

// The Tumbling E has 5 strokes tall (each stroke = arcMinPerStroke)
export const E_STROKES = 5;

