"""Dynamic rerouting helpers for E4."""

from __future__ import annotations

import math
from typing import Optional

from maritime_data_context import Booking

from .diagnostics import REROUTE_DIAGNOSTICS
from .expected_time import _transition_cost
from .routing import (
    RouteSpan,
    build_canonical_path_signature,
    build_feasible_candidate_bookings,
    evaluate_booking_path_cost,
    find_route_span,
    normalize_booking_path,
    shortest_booking_path,
)
from . import strategy_parameters

_REROUTE_TOLERANCE_HOURS = 1e-6


def maybe_reroute_carried_shipments(context, now, vessel) -> None:
    """Evaluate and apply reroutes for carried shipments on one vessel."""
    if vessel is None or vessel.current_segment is None:
        return

    candidate_bookings = build_feasible_candidate_bookings(context, now)
    if not candidate_bookings:
        return

    for shipment in list(vessel.carried_shipments):
        _maybe_reroute_shipment(
            context,
            now,
            vessel,
            shipment,
            candidate_bookings,
        )


def _maybe_reroute_shipment(context, now, vessel, shipment, candidate_bookings) -> None:
    REROUTE_DIAGNOSTICS.reroute_evaluations += 1

    current_plan = _build_current_remaining_plan(context, vessel, shipment)
    if current_plan is None:
        REROUTE_DIAGNOSTICS.reroutes_no_alternative += 1
        return

    current_path, current_previous_route = current_plan
    current_path = normalize_booking_path(current_path)
    current_cost = evaluate_booking_path_cost(
        current_path,
        _transition_cost,
        initial_previous_route=current_previous_route,
    )
    if not math.isfinite(current_cost):
        REROUTE_DIAGNOSTICS.reroutes_no_alternative += 1
        return

    current_booking = shipment.get_current_booking()
    current_port = _get_current_port(vessel)
    final_port = _get_final_booking_port(shipment)
    if current_port is None or final_port is None:
        REROUTE_DIAGNOSTICS.reroutes_no_alternative += 1
        return

    alternative_path = shortest_booking_path(
        context,
        current_port,
        final_port,
        candidate_bookings,
        _transition_cost,
        initial_previous_route=current_booking.service_route,
    )
    if not alternative_path:
        REROUTE_DIAGNOSTICS.reroutes_no_alternative += 1
        return

    alternative_path = normalize_booking_path(alternative_path)
    if build_canonical_path_signature(alternative_path) == build_canonical_path_signature(current_path):
        REROUTE_DIAGNOSTICS.reroutes_same_plan += 1
        return

    alternative_cost = evaluate_booking_path_cost(
        alternative_path,
        _transition_cost,
        initial_previous_route=current_booking.service_route,
    )
    if not math.isfinite(alternative_cost):
        REROUTE_DIAGNOSTICS.reroutes_no_alternative += 1
        return

    savings_hours = current_cost - alternative_cost
    if savings_hours + _REROUTE_TOLERANCE_HOURS < strategy_parameters.MIN_REROUTE_SAVING_HOURS:
        REROUTE_DIAGNOSTICS.reroutes_rejected_threshold += 1
        return

    if not _apply_reroute_from_current_port(
        context,
        vessel,
        shipment,
        current_plan,
        alternative_path,
    ):
        REROUTE_DIAGNOSTICS.reroutes_no_alternative += 1
        return

    REROUTE_DIAGNOSTICS.reroutes_accepted += 1
    REROUTE_DIAGNOSTICS.total_estimated_hours_saved += savings_hours
    if savings_hours > REROUTE_DIAGNOSTICS.max_estimated_hours_saved:
        REROUTE_DIAGNOSTICS.max_estimated_hours_saved = savings_hours


def _build_current_remaining_plan(context, vessel, shipment) -> Optional[tuple[list[RouteSpan], object]]:
    current_booking = shipment.get_current_booking()
    current_segment = vessel.current_segment
    current_port = _get_current_port(vessel)
    if current_port is None:
        return None

    remaining_path: list[RouteSpan] = []
    current_route = current_booking.service_route
    if current_route is None:
        return None

    if current_segment.sequence_index != current_booking.arrival_segment_index:
        arrival_port = _get_booking_arrival_port(current_booking)
        if arrival_port is None:
            return None
        next_segment_index = _next_segment_index(current_route, current_segment.sequence_index)
        current_span = find_route_span(
            context,
            current_route,
            current_port,
            arrival_port,
            next_segment_index,
            current_booking.arrival_segment_index,
        )
        if current_span is None:
            return None
        remaining_path.append(current_span)

    for booking in sorted(shipment.associated_bookings, key=lambda item: item.sequence_index):
        if booking.sequence_index <= current_booking.sequence_index:
            continue
        span = _booking_to_span(context, booking)
        if span is None:
            return None
        remaining_path.append(span)

    return remaining_path, current_route


def _booking_to_span(context, booking) -> Optional[RouteSpan]:
    departure_port = _get_booking_departure_port(booking)
    arrival_port = _get_booking_arrival_port(booking)
    if departure_port is None or arrival_port is None:
        return None
    return find_route_span(
        context,
        booking.service_route,
        departure_port,
        arrival_port,
        booking.departure_segment_index,
        booking.arrival_segment_index,
    )


def _apply_reroute_from_current_port(
    context,
    vessel,
    shipment,
    current_plan,
    alternative_path,
) -> bool:
    current_path, _current_route = current_plan
    current_booking = shipment.get_current_booking()
    current_segment = vessel.current_segment

    if current_segment is None:
        return False

    original_bookings = list(shipment.associated_bookings)
    retained = sorted(
        (
            booking
            for booking in shipment.associated_bookings
            if booking.sequence_index < current_booking.sequence_index
        ),
        key=lambda booking: booking.sequence_index,
    )

    completed_booking = current_booking
    completed_booking.arrival_segment_index = current_segment.sequence_index

    normalized_alternative = list(alternative_path)
    if normalized_alternative and normalized_alternative[0].service_route is completed_booking.service_route:
        completed_booking.arrival_segment_index = normalized_alternative[0].arrival_segment_index
        normalized_alternative = normalized_alternative[1:]

    next_sequence = current_booking.sequence_index + 1
    new_bookings = []
    for edge in normalized_alternative:
        booking = Booking(
            sequence_index=next_sequence,
            shipment=shipment,
            service_route=edge.service_route,
            departure_segment_index=edge.departure_segment_index,
            arrival_segment_index=edge.arrival_segment_index,
        )
        new_bookings.append(booking)
        next_sequence += 1

    retained_bookings = retained + [completed_booking]
    replaced_bookings = [
        booking for booking in original_bookings if booking not in retained_bookings
    ]
    _remove_bookings_from_service_routes(replaced_bookings)
    for booking in new_bookings:
        booking.service_route.associated_bookings.append(booking)

    shipment.associated_bookings.clear()
    shipment.associated_bookings.extend(retained_bookings + new_bookings)
    shipment.current_booking_index = completed_booking.sequence_index
    return True


def _remove_bookings_from_service_routes(bookings) -> None:
    for booking in bookings:
        service_route = booking.service_route
        if service_route is None:
            continue
        while booking in service_route.associated_bookings:
            service_route.associated_bookings.remove(booking)


def _get_current_port(vessel):
    current_segment = vessel.current_segment
    if current_segment is None:
        return None
    leg = current_segment.associated_leg
    return leg.arrival_port if leg is not None else None


def _get_final_booking_port(shipment):
    last_booking = max(
        shipment.associated_bookings,
        key=lambda booking: booking.sequence_index,
        default=None,
    )
    if last_booking is None or last_booking.service_route is None:
        return None
    return _get_booking_arrival_port(last_booking)


def _get_booking_departure_port(booking):
    service_route = booking.service_route
    if service_route is None:
        return None
    for segment in service_route.segments:
        if segment.sequence_index == booking.departure_segment_index:
            leg = segment.associated_leg
            return leg.departure_port if leg is not None else None
    return None


def _get_booking_arrival_port(booking):
    service_route = booking.service_route
    if service_route is None:
        return None
    for segment in service_route.segments:
        if segment.sequence_index == booking.arrival_segment_index:
            leg = segment.associated_leg
            return leg.arrival_port if leg is not None else None
    return None


def _next_segment_index(service_route, segment_index: int) -> int:
    segments = sorted(service_route.segments, key=lambda segment: segment.sequence_index)
    if not segments:
        return segment_index
    sequence_indices = [segment.sequence_index for segment in segments]
    try:
        current_pos = sequence_indices.index(segment_index)
    except ValueError:
        return segment_index
    return sequence_indices[(current_pos + 1) % len(sequence_indices)]
