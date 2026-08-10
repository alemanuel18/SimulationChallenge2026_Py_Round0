"""Expected sailing-time routing for E1."""

from __future__ import annotations

import logging
import math

from maritime_data_context import Booking

from typing import Optional

from .routing import (
    build_feasible_candidate_bookings,
    build_path_signature,
    distance_cost,
    expected_sailing_hours,
    remove_bookings_from_service_routes,
    shortest_booking_path,
)

logger = logging.getLogger(__name__)

_DEBUG_SAMPLE_LIMIT = 5
_DEBUG_SAMPLES_SEEN = 0


def assign_associated_bookings_by_expected_sailing_time(
    context, now, shipment
) -> Optional[bool]:
    """Assign the initial booking chain using expected sailing time only.

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

    time_path = shortest_booking_path(
        context,
        origin_port,
        destination_port,
        candidate_bookings,
        expected_sailing_hours,
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
            "E1 routing shipment=%s distance path == sailing-time path: %s",
            getattr(shipment, "index", "?"),
            time_signature,
        )
    else:
        logger.debug(
            "E1 routing shipment=%s distance path=%s sailing-time path=%s",
            getattr(shipment, "index", "?"),
            distance_signature,
            time_signature,
        )

    _DEBUG_SAMPLES_SEEN += 1
