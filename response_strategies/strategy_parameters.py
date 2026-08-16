"""Tunable parameters for the user response strategy.

The defaults are intentionally centralized so a future optimizer can sweep
values by setting environment variables before each simulation run.
"""

from dataclasses import dataclass, fields
from functools import lru_cache
import os


@dataclass(frozen=True)
class StrategyParameters:
    expected_route_wait_days: float = 3.5
    transshipment_penalty_days: float = 1.2
    port_storage_penalty_days_per_1000_teu: float = 0.18
    port_arrival_vessel_penalty_days: float = 0.55
    route_pressure_penalty_days: float = 1.9
    route_pressure_sample_size: float = 240.0
    port_storage_sample_size: float = 600.0
    congestion_risk_penalty_days: float = 2.4
    closed_port_risk_penalty_days: float = 9.0
    no_deployed_vessel_penalty_days: float = 5.0
    disruption_lookahead_days: float = 21.0
    hard_congestion_multiplier: float = 7.0
    enable_custom_berth_priority: float = 1.0
    enable_initial_time_routing: float = 0.0
    enable_controlled_alternative_routes: float = 0.0
    enable_in_transit_replanning: float = 0.0

    replan_min_saving_days: float = 1.0
    replan_disruption_trigger_days: float = 0.25

    alternative_route_min_pressure: float = 0.45
    alternative_route_min_active_multiplier: float = 2.0

    berth_override_margin: float = 0.16
    berth_min_unloading_teu_advantage: float = 8.0
    berth_final_unload_weight: float = 0.42
    berth_transshipment_unload_weight: float = 0.18
    berth_unload_age_weight: float = 0.18
    berth_wait_weight: float = 0.12
    berth_carried_teu_weight: float = 0.06
    berth_handling_penalty_weight: float = 0.10


@lru_cache(maxsize=1)
def get_strategy_parameters() -> StrategyParameters:
    """Load parameters from WSC_STRATEGY_* environment variables once."""
    values = {}
    for field in fields(StrategyParameters):
        env_name = f"WSC_STRATEGY_{field.name.upper()}"
        raw_value = os.environ.get(env_name)
        if raw_value is None:
            continue
        try:
            values[field.name] = float(raw_value)
        except ValueError:
            continue
    return StrategyParameters(**values)
