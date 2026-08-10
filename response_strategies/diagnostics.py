"""Aggregate diagnostics for response-strategy experiments."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RerouteDiagnostics:
    reroute_evaluations: int = 0
    reroutes_accepted: int = 0
    reroutes_rejected_threshold: int = 0
    reroutes_same_plan: int = 0
    reroutes_no_alternative: int = 0
    total_estimated_hours_saved: float = 0.0
    max_estimated_hours_saved: float = 0.0

    def reset(self) -> None:
        self.reroute_evaluations = 0
        self.reroutes_accepted = 0
        self.reroutes_rejected_threshold = 0
        self.reroutes_same_plan = 0
        self.reroutes_no_alternative = 0
        self.total_estimated_hours_saved = 0.0
        self.max_estimated_hours_saved = 0.0


REROUTE_DIAGNOSTICS = RerouteDiagnostics()


def reset_reroute_diagnostics() -> None:
    REROUTE_DIAGNOSTICS.reset()

