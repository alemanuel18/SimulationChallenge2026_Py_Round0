"""Expected sailing-time routing for E2."""

from __future__ import annotations

import logging
import math

from maritime_data_context import Booking

from typing import Optional

from .strategy_parameters import ENABLE_INITIAL_WAIT
from .routing import (
    build_feasible_candidate_bookings,
    build_path_signature,
    distance_cost,
    expected_sailing_hours,
    remove_bookings_from_service_routes,
    shortest_booking_path,
    _get_service_route_speed_knots,
)

logger = logging.getLogger(__name__)

_DEBUG_SAMPLE_LIMIT = 5
_DEBUG_SAMPLES_SEEN = 0


def assign_associated_bookings_by_expected_sailing_time(
    context, now, shipment
) -> Optional[bool]:
    """Assign the initial booking chain using expected sailing time.

    Returns:
        True when a time-optimal feasible booking chain was assigned.
        None when no feasible time-based path was found and the caller should
        fall back to the baseline/default strategy.
    """
    global _DEBUG_SAMPLES_SEEN

    demand = shipment.demand
    origin_port = demand.origin_port
    destination_port = demand.destination_port

    remove_bookings_from_service_routes(shipment.associated_bookings)
    shipment.associated_bookings = []
    shipment.current_booking_index = None

    if origin_port == destination_port:
        return True

    candidate_bookings = build_feasible_candidate_bookings(context, now)
    if not candidate_bookings:
        return None

    origin_cost_fn = None
    if ENABLE_INITIAL_WAIT:
        origin_cost_fn = lambda edge: (
            estimate_service_wait_hours(edge.service_route)
            + expected_sailing_hours(edge)
        )

    time_path = shortest_booking_path(
        context,
        origin_port,
        destination_port,
        candidate_bookings,
        expected_sailing_hours,
        origin_cost_fn=origin_cost_fn,
    )
    if not time_path:
        return None

    _maybe_log_distance_comparison(
        context,
        shipment,
        origin_port,
        destination_port,
        candidate_bookings,
        time_path,
    )

    _materialize_booking_chain(shipment, time_path)
    return True


def _materialize_booking_chain(shipment, path) -> None:
    for sequence_index, edge in enumerate(path, start=1):
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


def estimate_route_cycle_hours(service_route) -> float:
    """Estimate one full sailing cycle using current service-route state."""
    route_speed_knots = _get_service_route_speed_knots(service_route)
    if route_speed_knots <= 0:
        return math.inf

    total_hours = 0.0
    for segment in sorted(service_route.segments, key=lambda item: item.sequence_index):
        leg = segment.associated_leg
        if leg is None:
            return math.inf
        total_hours += (leg.sailing_distance / route_speed_knots) * leg.sailing_time_multiplier
    return total_hours


def estimate_headway_hours(service_route) -> float:
    """Estimate expected departure headway from the current deployed vessel count."""
    vessel_count = len(getattr(service_route, "deployed_vessels", None) or [])
    if vessel_count <= 0:
        return math.inf

    cycle_hours = estimate_route_cycle_hours(service_route)
    if not math.isfinite(cycle_hours):
        return math.inf
    return cycle_hours / vessel_count


def estimate_service_wait_hours(service_route) -> float:
    """Estimate expected initial waiting time for the first service route only."""
    headway_hours = estimate_headway_hours(service_route)
    if not math.isfinite(headway_hours):
        return math.inf
    return headway_hours / 2.0


def _maybe_log_distance_comparison(
    context,
    shipment,
    origin_port,
    destination_port,
    candidate_bookings,
    time_path,
) -> None:
    global _DEBUG_SAMPLES_SEEN

    if _DEBUG_SAMPLES_SEEN >= _DEBUG_SAMPLE_LIMIT:
        return
    if not logger.isEnabledFor(logging.DEBUG):
        return

    distance_path = shortest_booking_path(
        context,
        origin_port,
        destination_port,
        candidate_bookings,
        distance_cost,
    )

    time_signature = build_path_signature(time_path)
    distance_signature = build_path_signature(distance_path)
    if time_signature == distance_signature:
        logger.debug(
            "E2 routing shipment=%s distance path == sailing-time path: %s",
            getattr(shipment, "index", "?"),
            time_signature,
        )
    else:
        logger.debug(
            "E2 routing shipment=%s distance path=%s sailing-time path=%s",
            getattr(shipment, "index", "?"),
            distance_signature,
            time_signature,
        )

    _DEBUG_SAMPLES_SEEN += 1
