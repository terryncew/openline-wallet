from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Union

import numpy as np


@dataclass
class WatchdogStatus:
    status: str  # "GREEN", "AMBER", "RED"
    freshness: float
    burn_rate: float
    recommendation: str


class AgentWatchdog:
    """
    Budget governor for autonomous agents.

    Detects low-entropy repetition ("Zombie Loops") early using Freshness:
        Freshness = unique(action_signatures) / total(action_signatures)

    Supports:
      - rolling-window freshness (early detection)
      - auto-calibration for different agent dialects / log schemas
    """

    def __init__(
        self,
        kill_threshold: float = 0.25,
        window_size: int = 15,
        min_steps: int = 5,
        normalizer: Optional[Callable[[object], str]] = None,
    ):
        self.kill_threshold = float(kill_threshold)
        self.window_size = int(window_size)
        self.min_steps = int(min_steps)
        self.history: List[str] = []
        self.normalizer = normalizer or self._default_normalizer

    @staticmethod
    def _default_normalizer(action: object) -> str:
        if action is None:
            return ""
        s = str(action).strip().lower()
        if not s:
            return ""
        return s.split()[0]

    def log_action(self, action: object) -> None:
        sig = self.normalizer(action)
        if sig:
            self.history.append(sig)

    @staticmethod
    def _freshness(seq: List[str]) -> float:
        if not seq:
            return 1.0
        return len(set(seq)) / len(seq)

    def audit(self, use_window: bool = True) -> WatchdogStatus:
        if not self.history:
            return WatchdogStatus("GREEN", 1.0, 0.0, "Continue")

        scope = self.history
        if use_window and len(self.history) > self.window_size:
            scope = self.history[-self.window_size:]

        total = len(scope)
        freshness = self._freshness(scope)
        burn_rate = 1.0 - freshness

        if total >= self.min_steps and freshness < self.kill_threshold:
            return WatchdogStatus("RED", freshness, burn_rate, "KILL RUN")

        if total >= self.min_steps and freshness < (self.kill_threshold + 0.15):
            return WatchdogStatus("AMBER", freshness, burn_rate, "WARN: DRIFT")

        return WatchdogStatus("GREEN", freshness, burn_rate, "HEALTHY")

    def min_window_freshness(self) -> float:
        if not self.history:
            return 1.0
        if len(self.history) <= self.window_size:
            return self._freshness(self.history)
        mins: List[float] = []
        for i in range(self.window_size, len(self.history) + 1):
            w = self.history[i - self.window_size: i]
            mins.append(self._freshness(w))
        return float(min(mins)) if mins else 1.0

    @staticmethod
    def calibrate(
        labeled_logs: List[Dict[str, Union[List[object], bool]]],
        window_size: int = 15,
        min_steps: int = 5,
        objective: str = "avoid_killing_winners",
        thresholds=None,
    ) -> float:
        if thresholds is None:
            thresholds = np.linspace(0.10, 0.50, 41)

        best_t = 0.25
        best_score = -1e9
        eps = 1e-12

        for t in thresholds:
            false_kill = 0
            missed_fail = 0
            correct = 0
            total = 0

            for run in labeled_logs:
                dog = AgentWatchdog(
                    kill_threshold=float(t),
                    window_size=window_size,
                    min_steps=min_steps,
                )
                for a in run["actions"]:
                    dog.log_action(a)

                feature = dog.min_window_freshness()
                predicted_zombie = feature < float(t)
                actual_success = bool(run["success"])

                if predicted_zombie and actual_success:
                    false_kill += 1
                elif (not predicted_zombie) and (not actual_success):
                    missed_fail += 1
                else:
                    correct += 1

                total += 1

            acc = correct / max(1, total)

            if objective == "avoid_killing_winners":
                score = acc - (2.5 * (false_kill / max(1, total))) - (1.0 * (missed_fail / max(1, total)))
            else:
                score = acc

            if (score > best_score + eps) or (abs(score - best_score) <= eps and float(t) < best_t):
                best_score = score
                best_t = float(t)

        return float(round(best_t, 2))
