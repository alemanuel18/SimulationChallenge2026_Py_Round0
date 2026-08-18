"""Modular user strategy focused on reducing average transport time."""

from response_strategies.default_strategy import DefaultStrategy
from response_strategies.booking_optimizer import (
    assign_schedule_aware_bookings,
    record_service_call,
)
from response_strategies.routing_utils import (
    assign_min_expected_time_bookings,
    build_disruption_snapshot,
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
        default_vessel = DefaultStrategy.select_vessel_for_berth(
            maritime_data_context,
            port,
            waiting_vessels,
            available_berths,
            current_time,
            waiting_since_by_vessel,
        )

        waiting_values = []
        carried_values = []
        final_unload_values = []
        transshipment_unload_values = []
        unload_age_values = []
        handling_values = []

        for vessel in waiting_vessels:
            waiting_values.append(_waiting_days(vessel, current_time, waiting_since_by_vessel))
            carried_values.append(_carried_teu(vessel))
            final_unload_values.append(_final_discharge_teu_at_port(vessel, port))
            transshipment_unload_values.append(_transshipment_discharge_teu_at_port(vessel, port))
            unload_age_values.append(_weighted_unloading_age_days(vessel, current_time))
            handling_values.append(_handling_workload(vessel))

        scores = []
        for values in zip(
            _normalize(final_unload_values),
            _normalize(transshipment_unload_values),
            _normalize(unload_age_values),
            _normalize(waiting_values),
            _normalize(carried_values),
            _normalize(handling_values),
        ):
            (
                final_unload_score,
                transshipment_unload_score,
                unload_age_score,
                waiting_score,
                carried_score,
                handling_score,
            ) = values
            scores.append(
                params.berth_final_unload_weight * final_unload_score
                + params.berth_transshipment_unload_weight * transshipment_unload_score
                + params.berth_unload_age_weight * unload_age_score
                + params.berth_wait_weight * waiting_score
                + params.berth_carried_teu_weight * carried_score
                - params.berth_handling_penalty_weight * handling_score
            )

        best_index, best_vessel = max(
            enumerate(waiting_vessels), key=lambda item: (scores[item[0]], -item[0])
        )
        if default_vessel is None or best_vessel is default_vessel:
            return best_vessel

        default_index = waiting_vessels.index(default_vessel)
        score_gain = scores[best_index] - scores[default_index]
        unloading_gain = (
            final_unload_values[best_index]
            + transshipment_unload_values[best_index]
            - final_unload_values[default_index]
            - transshipment_unload_values[default_index]
        )
        final_gain = final_unload_values[best_index] - final_unload_values[default_index]
        if (
            score_gain >= params.berth_override_margin
            and (
                unloading_gain >= params.berth_min_unloading_teu_advantage
                or final_gain > 0
            )
        ):
            return best_vessel

        return None

    @staticmethod
    def create_alternative_service_routes(context, now, vessel=None):
        params = get_strategy_parameters()
        snapshot = build_disruption_snapshot(context, now, params)
        if (
            params.suppress_kaohsiung_s2_detour >= 0.5
            and _snapshot_touches_port(snapshot, "kaohsiung")
        ):
            return True
        if params.enable_controlled_alternative_routes < 0.5:
            return None

        if not snapshot.active_closed_ports and not snapshot.congested_leg_multipliers:
            DefaultStrategy.create_alternative_service_routes(context, now, vessel)
            return True

        if _should_activate_alternative_routes(context, snapshot, params, vessel):
            DefaultStrategy.create_alternative_service_routes(context, now, vessel)
        return True

    @staticmethod
    def assign_associated_bookings(context, now, shipment):
        params = get_strategy_parameters()
        if params.enable_schedule_aware_booking >= 0.5:
            return assign_schedule_aware_bookings(
                context, now, shipment, params
            )
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
        record_service_call(context, now, vessel, params)
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


def _snapshot_touches_port(snapshot, port_name):
    name = port_name.casefold()
    if name in snapshot.active_closed_ports:
        return True
    return any(
        leg.departure_port.name.casefold() == name
        or leg.arrival_port.name.casefold() == name
        for leg in snapshot.congested_leg_multipliers
    )


def _waiting_days(vessel, current_time, waiting_since_by_vessel):
    waiting_since = waiting_since_by_vessel.get(vessel, current_time)
    return max(0.0, (current_time - waiting_since).total_seconds() / 86400.0)


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


def _normalize(values):
    if not values:
        return []
    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return [0.0] * len(values)
    span = maximum - minimum
    return [(value - minimum) / span for value in values]
