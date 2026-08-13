"""Selective initial-routing policy for the E10 Challenger experiment."""

from __future__ import annotations

from dataclasses import dataclass
import math

from maritime_data_context import Booking

from . import strategy_parameters
from .expected_time import estimate_nominal_capacity_teu, estimate_queued_teu
from .routing import (
    build_default_equivalent_candidate_bookings,
    build_normal_default_candidate_bookings,
    expected_sailing_hours,
    normalize_booking_path,
    remove_bookings_from_service_routes,
    shortest_booking_path_default_semantics,
)


@dataclass
class ChallengerDiagnostics:
    default_kept: int = 0
    disruption_affected: int = 0
    alternative_accepted: int = 0
    rejected_transfer_limit: int = 0
    rejected_cost: int = 0
    rejected_qcr: int = 0
    fallback_to_default: int = 0


DIAGNOSTICS = ChallengerDiagnostics()


def assign_associated_bookings_challenger(context, now, shipment) -> bool | None:
    """Keep the normal Default path unless it intersects an active disruption.

    ``None`` deliberately delegates to DefaultStrategy.  That is the safe
    outcome when the challenger cannot produce a qualified replacement.
    """
    demand = shipment.demand
    origin_port = demand.origin_port
    destination_port = demand.destination_port

    if origin_port is destination_port:
        return True

    default_path = shortest_booking_path_default_semantics(
        context,
        origin_port,
        destination_port,
        build_normal_default_candidate_bookings(context),
        lambda _previous_route, edge: edge.sailing_distance_nm,
    )
    if not default_path:
        DIAGNOSTICS.fallback_to_default += 1
        return None

    closed_port_names, congested_legs = _active_disruption_targets(context, now)
    if not _path_touches_disruption(default_path, closed_port_names, congested_legs):
        _materialize_booking_chain(shipment, default_path)
        DIAGNOSTICS.default_kept += 1
        return True

    DIAGNOSTICS.disruption_affected += 1
    alternative_path = shortest_booking_path_default_semantics(
        context,
        origin_port,
        destination_port,
        build_default_equivalent_candidate_bookings(context, now),
        lambda _previous_route, edge: expected_sailing_hours(edge),
    )
    if not alternative_path:
        return _keep_or_wait_for_default(shipment, default_path, closed_port_names)

    if _transshipment_count(alternative_path) > (
        _transshipment_count(default_path)
        + strategy_parameters.E10_MAX_EXTRA_TRANSSHIPMENTS
    ):
        DIAGNOSTICS.rejected_transfer_limit += 1
        return _keep_or_wait_for_default(shipment, default_path, closed_port_names)

    default_cost = _effective_sailing_hours(default_path)
    alternative_cost = _effective_sailing_hours(alternative_path)
    required_cost = default_cost * (
        1.0 - strategy_parameters.E10_MIN_EFFECTIVE_SAVING_RATIO
    )
    if not math.isfinite(alternative_cost) or alternative_cost >= required_cost:
        DIAGNOSTICS.rejected_cost += 1
        return _keep_or_wait_for_default(shipment, default_path, closed_port_names)

    default_qcr = _maximum_qcr(default_path, shipment)
    alternative_qcr = _maximum_qcr(alternative_path, shipment)
    if (
        alternative_qcr > strategy_parameters.E10_QCR_HIGH_PRESSURE
        and alternative_qcr > default_qcr + strategy_parameters.E10_QCR_ALLOWED_INCREASE
    ):
        DIAGNOSTICS.rejected_qcr += 1
        return _keep_or_wait_for_default(shipment, default_path, closed_port_names)

    _materialize_booking_chain(shipment, alternative_path)
    DIAGNOSTICS.alternative_accepted += 1
    return True


def _keep_or_wait_for_default(shipment, default_path, closed_port_names) -> bool:
    """Keep Default for congestion; wait when its path enters a closed port."""
    if _path_touches_closed_port(default_path, closed_port_names):
        return False
    _materialize_booking_chain(shipment, default_path)
    DIAGNOSTICS.default_kept += 1
    return True


def _active_disruption_targets(context, now) -> tuple[set[str], set[object]]:
    closed_port_names: set[str] = set()
    congested_legs: set[object] = set()
    for plan in context.disruption_plans:
        if not _is_active(plan, now):
            continue
        if plan.close_berth and plan.target_berth is not None:
            closed_port_names.add(plan.target_berth.port.name.casefold())
        if plan.multiplier > 1 and plan.target_leg is not None:
            congested_legs.add(plan.target_leg)
    return closed_port_names, congested_legs


def _is_active(plan, now) -> bool:
    if plan.start_offset_days is None or plan.duration_days is None:
        return False
    import datetime as dt

    start = dt.datetime.min + dt.timedelta(days=plan.start_offset_days)
    end = start + dt.timedelta(days=plan.duration_days)
    return start <= now < end


def _path_touches_disruption(path, closed_port_names, congested_legs) -> bool:
    for edge in path:
        if edge.departure_port.name.casefold() in closed_port_names:
            return True
        if edge.arrival_port.name.casefold() in closed_port_names:
            return True
        if any(port.name.casefold() in closed_port_names for port in edge.traversed_ports):
            return True
        if any(segment.associated_leg in congested_legs for segment in edge.traversed_segments):
            return True
    return False


def _path_touches_closed_port(path, closed_port_names) -> bool:
    return any(
        edge.departure_port.name.casefold() in closed_port_names
        or edge.arrival_port.name.casefold() in closed_port_names
        or any(port.name.casefold() in closed_port_names for port in edge.traversed_ports)
        for edge in path
    )


def _effective_sailing_hours(path) -> float:
    total = sum(expected_sailing_hours(edge) for edge in path)
    return total if math.isfinite(total) else math.inf


def _transshipment_count(path) -> int:
    return max(0, len(normalize_booking_path(path)) - 1)


def _maximum_qcr(path, shipment) -> float:
    queued_teu_cache: dict[tuple[object, object, int], int] = {}
    maximum = 0.0
    for edge in normalize_booking_path(path):
        capacity = estimate_nominal_capacity_teu(edge.service_route)
        if not math.isfinite(capacity) or capacity <= 0:
            return math.inf
        queued_teu = estimate_queued_teu(
            edge.departure_port,
            edge.service_route,
            edge.departure_segment_index,
            exclude_shipment=shipment,
            cache=queued_teu_cache,
        )
        maximum = max(maximum, queued_teu / capacity)
    return maximum


def _materialize_booking_chain(shipment, path) -> None:
    remove_bookings_from_service_routes(shipment.associated_bookings)
    shipment.associated_bookings = []
    shipment.current_booking_index = None

    for sequence_index, edge in enumerate(normalize_booking_path(path), start=1):
        booking = Booking(
            sequence_index=sequence_index,
            shipment=shipment,
            service_route=edge.service_route,
            departure_segment_index=edge.departure_segment_index,
            arrival_segment_index=edge.arrival_segment_index,
        )
        shipment.associated_bookings.append(booking)
        edge.service_route.associated_bookings.append(booking)

    shipment.current_booking_index = 1 if shipment.associated_bookings else None
