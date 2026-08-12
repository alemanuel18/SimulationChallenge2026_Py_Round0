"""E9 booking assignment helper.

E9 keeps Default path selection and only normalizes contiguous bookings that
remain on the same service route before materializing the booking chain.
"""

from __future__ import annotations

from maritime_data_context import Booking

from .routing import (
    build_default_equivalent_candidate_bookings,
    distance_transition_cost,
    normalize_booking_path,
    shortest_booking_path_default_semantics,
)
from .routing import remove_bookings_from_service_routes


def assign_associated_bookings(context, now, shipment) -> bool:
    """Assign Default-equivalent bookings, then collapse safe same-route spans."""
    demand = shipment.demand
    origin_port = demand.origin_port
    destination_port = demand.destination_port

    remove_bookings_from_service_routes(shipment.associated_bookings)
    shipment.associated_bookings = []
    shipment.current_booking_index = None

    if origin_port == destination_port:
        return True

    candidate_bookings = build_default_equivalent_candidate_bookings(context, now)
    if not candidate_bookings:
        return False

    raw_path = shortest_booking_path_default_semantics(
        context,
        origin_port,
        destination_port,
        candidate_bookings,
        distance_transition_cost,
    )
    if not raw_path:
        return False

    normalized_path = normalize_booking_path(raw_path)
    if _expanded_segment_sequence(raw_path) != _expanded_segment_sequence(
        normalized_path
    ):
        raise ValueError(
            "E9 normalization must preserve the exact physical segment sequence."
        )

    _materialize_booking_chain(shipment, normalized_path)
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


def _expanded_segment_sequence(path) -> tuple[object, ...]:
    return tuple(
        segment
        for edge in path
        for segment in getattr(edge, "traversed_segments", ()) or ()
    )
