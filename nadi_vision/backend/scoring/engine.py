"""Continuous ETDRS-equivalent acuity scoring engine.

One session must be created per eye. OU testing is represented by two
independent sessions (OD then OS) and their results must never be merged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import time
from typing import List, Literal, Optional

from backend.scoring.constants import (
    LETTERS_PER_LINE,
    LOGMAR_CEILING,
    LOGMAR_PER_LETTER,
    LOW_VISION_DISTANCE_M,
    TERMINATION_WRONG_COUNT,
    VAS_OFFSET,
)


def _logmar_to_snellen_feet(logmar: float) -> str:
    denominator = round(20 * (10 ** logmar))
    return f"20/{denominator}"


def _logmar_to_snellen_metric(logmar: float) -> str:
    denominator = round(6 * (10 ** logmar))
    return f"6/{denominator}"


def _logmar_to_decimal(logmar: float) -> float:
    return 10 ** (-logmar)


def _who_classification(logmar: float) -> str:
    if logmar <= 0.0:
        return "Normal"
    if logmar <= 0.5:
        return "Mild"
    if logmar <= 1.0:
        return "Moderate"
    if logmar <= 1.3:
        return "Severe"
    return "Blind"


def _compute_ci_95(correct: int, total: int, center_logmar: float) -> tuple[float, float]:
    if total == 0:
        return (center_logmar, center_logmar)

    p = correct / total
    se = math.sqrt(p * (1 - p) / total) if 0.0 < p < 1.0 else 0.0
    se_logmar = se * total * LOGMAR_PER_LETTER
    margin = 1.96 * se_logmar
    return (center_logmar - margin, center_logmar + margin)


class LowVisionCategory(Enum):
    CF = "Counting Fingers"
    HM = "Hand Motion"
    LP = "Light Perception"
    NLP = "No Light Perception"


@dataclass
class TrialResult:
    level_logmar: float
    presented: str
    answered: str
    correct: bool
    distance_m: float
    response_time_ms: float
    timestamp: float
    invalidated: bool = False
    invalidation_reason: Optional[str] = None


@dataclass
class SessionResult:
    eye: Literal["OD", "OS"]
    correction: Literal["UCVA", "BCVA"]
    logmar: Optional[float]
    snellen_feet: str
    snellen_metric: str
    decimal_va: Optional[float]
    etdrs_letter_score: int
    vas: int
    who_classification: str
    total_trials: int
    correct_trials: int
    avg_distance_m: float
    confidence_interval_95: tuple[float, float]
    trials: List[TrialResult]
    low_vision_category: Optional[LowVisionCategory] = None
    low_vision_distance_m: Optional[float] = None


class AcuitySession:
    """Per-eye continuous scoring session."""

    def __init__(
        self,
        eye: Literal["OD", "OS"],
        correction: Literal["UCVA", "BCVA"],
        start_logmar: float = LOGMAR_CEILING,
        line_logmars: Optional[list[float]] = None,
    ) -> None:
        self._eye = eye
        self._correction = correction
        self._start_logmar = start_logmar
        self._line_logmars = line_logmars or [
            1.0,
            0.9,
            0.8,
            0.7,
            0.6,
            0.5,
            0.4,
            0.3,
            0.2,
            0.1,
            0.0,
            -0.1,
            -0.2,
            -0.3,
        ]

        self._line_index = 0
        self._line_trial_count = 0
        self._line_wrong_count = 0
        self._total_correct = 0
        self._terminated = False
        self._trials: List[TrialResult] = []

        self._reduced_distance_mode = False
        self._low_vision_category: Optional[LowVisionCategory] = None

    def get_current_logmar(self) -> float:
        return self._line_logmars[min(self._line_index, len(self._line_logmars) - 1)]

    def enter_reduced_distance_mode(self) -> None:
        self._reduced_distance_mode = True

    def record_low_vision_category(self, category: LowVisionCategory) -> None:
        self._low_vision_category = category
        self._terminated = True

    def record_response(
        self,
        presented: str,
        answered: str,
        distance_m: float,
        response_time_ms: float,
    ) -> TrialResult:
        if self._terminated:
            raise RuntimeError("Cannot record response after session termination")

        correct = presented == answered
        trial = TrialResult(
            level_logmar=self.get_current_logmar(),
            presented=presented,
            answered=answered,
            correct=correct,
            distance_m=distance_m,
            response_time_ms=response_time_ms,
            timestamp=time.time(),
        )
        self._trials.append(trial)

        self._line_trial_count += 1
        if correct:
            self._total_correct += 1
        else:
            self._line_wrong_count += 1
            # Terminate immediately at the third wrong answer (mid-line)
            if self._line_wrong_count >= TERMINATION_WRONG_COUNT:
                self._terminated = True
                return trial

        # Advance only when all five symbols on the line are completed
        if self._line_trial_count >= LETTERS_PER_LINE:
            next_index = self._line_index + 1
            if next_index >= len(self._line_logmars):
                # Completed the final line — session finalised, never loops
                self._terminated = True
            else:
                self._line_index = next_index
                self._line_trial_count = 0
                self._line_wrong_count = 0

        return trial

    def invalidate_last_trial(self, reason: str) -> None:
        if not self._trials:
            return

        last = self._trials[-1]
        if last.invalidated:
            return

        last.invalidated = True
        last.invalidation_reason = reason
        if last.correct:
            self._total_correct = max(0, self._total_correct - 1)

    def should_terminate(self) -> bool:
        return self._terminated

    def get_total_correct(self) -> int:
        """Total correctly identified symbols so far (ETDRS letter score)."""
        return self._total_correct

    def get_logmar_estimate(self) -> float:
        """Current continuous logMAR estimate based on correct letters so far."""
        return self._numeric_logmar()

    def _numeric_logmar(self) -> float:
        computed = self._start_logmar - (LOGMAR_PER_LETTER * self._total_correct)
        # For this screening workflow, 1.0 is the worst allowed numeric score.
        # Better-than-normal values are allowed to preserve continuous credit.
        return min(LOGMAR_CEILING, computed)

    def get_result(self) -> SessionResult:
        if self._low_vision_category is not None:
            return SessionResult(
                eye=self._eye,
                correction=self._correction,
                logmar=None,
                snellen_feet=self._low_vision_category.name,
                snellen_metric=self._low_vision_category.name,
                decimal_va=None,
                etdrs_letter_score=self._total_correct,
                vas=self._total_correct + VAS_OFFSET,
                who_classification="Severe",
                total_trials=len(self._trials),
                correct_trials=sum(1 for t in self._trials if t.correct and not t.invalidated),
                avg_distance_m=(sum(t.distance_m for t in self._trials) / len(self._trials)) if self._trials else 0.0,
                confidence_interval_95=(LOGMAR_CEILING, LOGMAR_CEILING),
                trials=self._trials,
                low_vision_category=self._low_vision_category,
                low_vision_distance_m=LOW_VISION_DISTANCE_M if self._reduced_distance_mode else None,
            )

        logmar = self._numeric_logmar()
        valid_trials = [t for t in self._trials if not t.invalidated]
        correct = sum(1 for t in valid_trials if t.correct)
        total = len(valid_trials)
        ci95 = _compute_ci_95(correct, total, center_logmar=logmar)

        return SessionResult(
            eye=self._eye,
            correction=self._correction,
            logmar=logmar,
            snellen_feet=_logmar_to_snellen_feet(logmar),
            snellen_metric=_logmar_to_snellen_metric(logmar),
            decimal_va=_logmar_to_decimal(logmar),
            etdrs_letter_score=self._total_correct,
            vas=self._total_correct + VAS_OFFSET,
            who_classification=_who_classification(logmar),
            total_trials=len(self._trials),
            correct_trials=correct,
            avg_distance_m=(sum(t.distance_m for t in self._trials) / len(self._trials)) if self._trials else 0.0,
            confidence_interval_95=ci95,
            trials=self._trials,
        )
