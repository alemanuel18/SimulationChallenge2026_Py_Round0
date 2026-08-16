"""Modular user strategy focused on reducing average transport time."""

from response_strategies.default_strategy import DefaultStrategy
from response_strategies.routing_utils import (
    assign_min_expected_time_bookings,
    build_disruption_snapshot,
    port_pressure_days,
    replan_carried_shipments,
    route_pressure,
)
from response_strategies.strategy_parameters import get_strategy_parameters


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
        waiting_since_by_vessel = waiting_since_by_vessel or {}
        port_pressure = port_pressure_days(port, maritime_data_context, params)

        waiting_values = []
        carried_values = []
        completion_values = []
        age_values = []
        capacity_values = []
        handling_values = []
        downstream_values = []

        for vessel in waiting_vessels:
            waiting_values.append(_waiting_days(vessel, current_time, waiting_since_by_vessel))
            carried_values.append(_carried_teu(vessel))
            completion_values.append(_completion_teu_at_current_port(vessel, port))
            age_values.append(_weighted_cargo_age_days(vessel, current_time))
            capacity_values.append(_vessel_capacity(vessel))
            handling_values.append(_handling_workload(vessel))
            downstream_values.append(port_pressure + _next_port_pressure(vessel, maritime_data_context, params))

        scores = []
        for values in zip(
            _normalize(waiting_values),
            _normalize(carried_values),
            _normalize(completion_values),
            _normalize(age_values),
            _normalize(capacity_values),
            _normalize(handling_values),
            _normalize(downstream_values),
        ):
            (
                waiting_score,
                carried_score,
                completion_score,
                age_score,
                capacity_score,
                handling_score,
                downstream_score,
            ) = values
            scores.append(
                params.berth_wait_weight * waiting_score
                + params.berth_carried_teu_weight * carried_score
                + params.berth_completion_teu_weight * completion_score
                + params.berth_age_weight * age_score
                + params.berth_capacity_weight * capacity_score
                - params.berth_handling_penalty_weight * handling_score
                - params.berth_downstream_penalty_weight * downstream_score
            )

        return max(enumerate(waiting_vessels), key=lambda item: (scores[item[0]], -item[0]))[1]

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


def _waiting_days(vessel, current_time, waiting_since_by_vessel):
    waiting_since = waiting_since_by_vessel.get(vessel, current_time)
    return max(0.0, (current_time - waiting_since).total_seconds() / 86400.0)


def _carried_teu(vessel):
    return sum(
        getattr(shipment, "teu_size", 0) or 0
        for shipment in getattr(vessel, "carried_shipments", [])
    )


def _completion_teu_at_current_port(vessel, port):
    total = 0
    for shipment in getattr(vessel, "carried_shipments", []):
        demand = getattr(shipment, "demand", None)
        if demand is not None and demand.destination_port is port:
            total += getattr(shipment, "teu_size", 0) or 0
    return total


def _weighted_cargo_age_days(vessel, current_time):
    weighted_age = 0.0
    for shipment in getattr(vessel, "carried_shipments", []):
        generated_time = getattr(shipment, "generated_time", None)
        if generated_time is None:
            continue
        teu = getattr(shipment, "teu_size", 0) or 0
        age_days = max(0.0, (current_time - generated_time).total_seconds() / 86400.0)
        weighted_age += age_days * teu
    return weighted_age


def _vessel_capacity(vessel):
    vessel_class = getattr(vessel, "vessel_class", None)
    return getattr(vessel_class, "teu_capacity", 0) or 0


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


def _next_port_pressure(vessel, context, params):
    try:
        next_segment = vessel.get_next_segment()
    except (AttributeError, ValueError):
        return 0.0
    leg = getattr(next_segment, "associated_leg", None)
    if leg is None:
        return 0.0
    return port_pressure_days(leg.arrival_port, context, params)


def _normalize(values):
    if not values:
        return []
    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return [0.0] * len(values)
    span = maximum - minimum
    return [(value - minimum) / span for value in values]
