"""Modular user strategy focused on reducing average transport time."""

import datetime as dt

from config.simulation_config import WARM_UP_DAYS
from response_strategies.default_strategy import DefaultStrategy
from response_strategies.routing_utils import (
    assign_min_expected_time_bookings,
    build_disruption_snapshot,
    replan_carried_shipments,
    route_pressure,
)
from response_strategies.strategy_parameters import get_strategy_parameters

_CARGO_WAITING_PORTS = frozenset(
    port.casefold() for port in ("Shanghai", "Singapore", "Busan")
)
_VESSEL_WAITING_PORTS = frozenset(
    port.casefold()
    for port in ("Kaohsiung", "Jakarta", "Ho Chi Minh City", "Busan")
)
_HYBRID_PORTS = frozenset(port.casefold() for port in ("Busan",))


class CriticalTimeStrategy:
    @staticmethod
    def select_vessel_for_berth(
        maritime_data_context,
        port,
        waiting_vessels,
        available_berths,
        current_time,
        waiting_since_by_vessel=None,
    ):
        if not waiting_vessels:
            return None

        params = get_strategy_parameters()
        if params.enable_custom_berth_priority < 0.5:
            return None
        if _is_berth_priority_paused(current_time, params):
            return None
        waiting_since_by_vessel = waiting_since_by_vessel or {}
        port_mode = _get_port_mode(port)
        default_vessel = DefaultStrategy.select_vessel_for_berth(
            maritime_data_context,
            port,
            waiting_vessels,
            available_berths,
            current_time,
            waiting_since_by_vessel,
        )

        metrics = [
            _berth_metrics(vessel, port, current_time, waiting_since_by_vessel)
            for vessel in waiting_vessels
        ]

        scores = _score_berth_candidates(metrics, port_mode, params)

        best_index, best_vessel = max(
            enumerate(waiting_vessels), key=lambda item: (scores[item[0]], -item[0])
        )
        if default_vessel is None or best_vessel is default_vessel:
            return best_vessel

        default_index = waiting_vessels.index(default_vessel)
        score_gain = scores[best_index] - scores[default_index]
        unloading_gain = (
            metrics[best_index]["final_unload_teu"]
            + metrics[best_index]["transshipment_unload_teu"]
            - metrics[default_index]["final_unload_teu"]
            - metrics[default_index]["transshipment_unload_teu"]
        )
        final_gain = (
            metrics[best_index]["final_unload_teu"]
            - metrics[default_index]["final_unload_teu"]
        )
        loading_gain = (
            metrics[best_index]["loading_teu"] - metrics[default_index]["loading_teu"]
        )
        turnaround_gain = (
            metrics[default_index]["handling_teu"] - metrics[best_index]["handling_teu"]
        )
        if (
            score_gain >= params.berth_override_margin
            and _has_port_mode_advantage(
                port_mode,
                unloading_gain,
                final_gain,
                loading_gain,
                turnaround_gain,
                params,
            )
        ):
            return best_vessel

        return None

    @staticmethod
    def create_alternative_service_routes(context, now, vessel=None):
        params = get_strategy_parameters()
        if params.enable_controlled_alternative_routes < 0.5:
            return None
        snapshot = build_disruption_snapshot(context, now, params)

        if not snapshot.active_closed_ports and not snapshot.congested_leg_multipliers:
            DefaultStrategy.create_alternative_service_routes(context, now, vessel)
            return True

        if _should_activate_alternative_routes(context, snapshot, params, vessel):
            DefaultStrategy.create_alternative_service_routes(context, now, vessel)
        return True

    @staticmethod
    def assign_associated_bookings(context, now, shipment):
        params = get_strategy_parameters()
        if params.enable_initial_time_routing < 0.5:
            return None
        snapshot = build_disruption_snapshot(context, now, params)
        if (
            not snapshot.active_closed_ports
            and not snapshot.risk_closed_ports
            and not snapshot.congested_leg_multipliers
        ):
            return None
        return assign_min_expected_time_bookings(context, now, shipment, params)

    @staticmethod
    def adjust_bookings_before_cargo_handling(context, now, vessel):
        params = get_strategy_parameters()
        if params.enable_in_transit_replanning < 0.5:
            return None
        return replan_carried_shipments(context, now, vessel, params)


def _should_activate_alternative_routes(context, snapshot, params, vessel):
    if any(multiplier >= params.alternative_route_min_active_multiplier for multiplier in snapshot.congested_leg_multipliers.values()):
        return True
    if snapshot.active_closed_ports:
        return True

    affected_routes = [
        route
        for route in getattr(context, "initial_service_routes", [])
        if _route_touches_disruption(route, snapshot)
    ]
    if any(
        route_pressure(route, context, params) >= params.alternative_route_min_pressure
        for route in affected_routes
    ):
        return True

    if vessel is not None and vessel.assigned_service_route in affected_routes:
        return bool(getattr(vessel, "carried_shipments", []))

    return False


def _route_touches_disruption(route, snapshot):
    for segment in getattr(route, "segments", []):
        leg = segment.associated_leg
        if leg in snapshot.congested_leg_multipliers:
            return True
        if leg.departure_port.name.casefold() in snapshot.active_closed_ports:
            return True
        if leg.arrival_port.name.casefold() in snapshot.active_closed_ports:
            return True
    return False


def _get_port_mode(port):
    port_name = port.name.casefold()
    if port_name in _HYBRID_PORTS:
        return "hybrid"
    if port_name in _CARGO_WAITING_PORTS:
        return "cargo"
    if port_name in _VESSEL_WAITING_PORTS:
        return "vessel"
    return "default"


def _berth_metrics(vessel, port, current_time, waiting_since_by_vessel):
    loading_teu = _loading_teu_from_port(vessel, port)
    final_unload_teu = _final_discharge_teu_at_port(vessel, port)
    transshipment_unload_teu = _transshipment_discharge_teu_at_port(vessel, port)
    handling_teu = _handling_workload(vessel)
    return {
        "waiting_days": _waiting_days(vessel, current_time, waiting_since_by_vessel),
        "carried_teu": _carried_teu(vessel),
        "loading_teu": loading_teu,
        "final_unload_teu": final_unload_teu,
        "transshipment_unload_teu": transshipment_unload_teu,
        "unload_age_days": _weighted_unloading_age_days(vessel, current_time),
        "handling_teu": handling_teu,
    }


def _score_berth_candidates(metrics, port_mode, params):
    loading_scores = _normalize([item["loading_teu"] for item in metrics])
    final_unload_scores = _normalize([item["final_unload_teu"] for item in metrics])
    transshipment_unload_scores = _normalize(
        [item["transshipment_unload_teu"] for item in metrics]
    )
    unload_age_scores = _normalize([item["unload_age_days"] for item in metrics])
    waiting_scores = _normalize([item["waiting_days"] for item in metrics])
    carried_scores = _normalize([item["carried_teu"] for item in metrics])
    handling_scores = _normalize([item["handling_teu"] for item in metrics])
    quick_turn_scores = [1.0 - score for score in handling_scores]

    scores = []
    for index in range(len(metrics)):
        unload_score = (
            final_unload_scores[index]
            + 0.45 * transshipment_unload_scores[index]
            + 0.20 * unload_age_scores[index]
        )
        if port_mode == "cargo":
            score = (
                params.berth_cargo_port_loading_weight * loading_scores[index]
                + params.berth_cargo_port_unload_weight * unload_score
                + params.berth_cargo_port_wait_weight * waiting_scores[index]
                - params.berth_handling_penalty_weight * handling_scores[index]
            )
        elif port_mode == "vessel":
            score = (
                params.berth_vessel_port_turnaround_weight * quick_turn_scores[index]
                + params.berth_vessel_port_unload_weight * unload_score
                + params.berth_vessel_port_wait_weight * waiting_scores[index]
            )
        elif port_mode == "hybrid":
            score = (
                params.berth_hybrid_loading_weight * loading_scores[index]
                + params.berth_hybrid_unload_weight * unload_score
                + params.berth_hybrid_turnaround_weight * quick_turn_scores[index]
                + params.berth_wait_weight * waiting_scores[index]
            )
        else:
            score = (
                params.berth_final_unload_weight * final_unload_scores[index]
                + params.berth_transshipment_unload_weight
                * transshipment_unload_scores[index]
                + params.berth_unload_age_weight * unload_age_scores[index]
                + params.berth_wait_weight * waiting_scores[index]
                + params.berth_carried_teu_weight * carried_scores[index]
                - params.berth_handling_penalty_weight * handling_scores[index]
            )
        scores.append(score)
    return scores


def _has_port_mode_advantage(
    port_mode,
    unloading_gain,
    final_gain,
    loading_gain,
    turnaround_gain,
    params,
):
    if port_mode == "cargo":
        return loading_gain >= params.berth_min_loading_teu_advantage
    if port_mode == "vessel":
        return turnaround_gain >= params.berth_min_turnaround_advantage
    if port_mode == "hybrid":
        return (
            loading_gain >= params.berth_min_loading_teu_advantage
            or unloading_gain >= params.berth_min_unloading_teu_advantage
            or turnaround_gain >= params.berth_min_turnaround_advantage
            or final_gain > 0
        )
    return (
        unloading_gain >= params.berth_min_unloading_teu_advantage
        or final_gain > 0
    )


def _waiting_days(vessel, current_time, waiting_since_by_vessel):
    waiting_since = waiting_since_by_vessel.get(vessel, current_time)
    return max(0.0, (current_time - waiting_since).total_seconds() / 86400.0)


def _is_berth_priority_paused(current_time, params):
    simulation_day = (current_time - dt.datetime.min).total_seconds() / 86400.0
    measured_day = simulation_day - WARM_UP_DAYS
    return (
        params.berth_pause_start_measured_day
        <= measured_day
        <= params.berth_pause_end_measured_day
    )


def _carried_teu(vessel):
    return sum(
        getattr(shipment, "teu_size", 0) or 0
        for shipment in getattr(vessel, "carried_shipments", [])
    )


def _final_discharge_teu_at_port(vessel, port):
    total = 0
    for shipment in _discharging_shipments(vessel):
        demand = getattr(shipment, "demand", None)
        if demand is not None and demand.destination_port is port:
            total += getattr(shipment, "teu_size", 0) or 0
    return total


def _transshipment_discharge_teu_at_port(vessel, port):
    total = 0
    for shipment in _discharging_shipments(vessel):
        demand = getattr(shipment, "demand", None)
        if demand is not None and demand.destination_port is not port:
            total += getattr(shipment, "teu_size", 0) or 0
    return total


def _loading_teu_from_port(vessel, port):
    total = 0
    for shipment in _loading_shipments(vessel):
        if getattr(shipment, "current_storage_port", None) is port:
            total += getattr(shipment, "teu_size", 0) or 0
    return total


def _weighted_unloading_age_days(vessel, current_time):
    weighted_age = 0.0
    for shipment in _discharging_shipments(vessel):
        generated_time = getattr(shipment, "generated_time", None)
        if generated_time is None:
            continue
        teu = getattr(shipment, "teu_size", 0) or 0
        age_days = max(0.0, (current_time - generated_time).total_seconds() / 86400.0)
        weighted_age += age_days * teu
    return weighted_age


def _handling_workload(vessel):
    try:
        return sum(
            getattr(shipment, "teu_size", 0) or 0
            for shipment in vessel.get_discharging_shipments_at_current_segment()
        ) + sum(
            getattr(shipment, "teu_size", 0) or 0
            for shipment in vessel.get_loading_shipments_at_next_segment()
        )
    except (AttributeError, TypeError, ValueError):
        return 0


def _discharging_shipments(vessel):
    try:
        return list(vessel.get_discharging_shipments_at_current_segment())
    except (AttributeError, TypeError, ValueError):
        return []


def _loading_shipments(vessel):
    try:
        return list(vessel.get_loading_shipments_at_next_segment())
    except (AttributeError, TypeError, ValueError):
        return []


def _normalize(values):
    if not values:
        return []
    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return [0.0] * len(values)
    span = maximum - minimum
    return [(value - minimum) / span for value in values]
