"""Expected transport-time routing for E3."""

from __future__ import annotations

import logging
import math

from maritime_data_context import Booking

from typing import Optional

from . import strategy_parameters
from .routing import (
    build_feasible_candidate_bookings,
    build_default_equivalent_candidate_bookings,
    build_path_signature,
    distance_transition_cost,
    expected_sailing_hours,
    normalize_booking_path,
    remove_bookings_from_service_routes,
    shortest_booking_path,
    shortest_booking_path_default_semantics,
    shortest_booking_path_with_operational_tie_break,
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
    strategy_parameters.validate_wait_configuration()

    demand = shipment.demand
    origin_port = demand.origin_port
    destination_port = demand.destination_port

    remove_bookings_from_service_routes(shipment.associated_bookings)
    shipment.associated_bookings = []
    shipment.current_booking_index = None

    if origin_port == destination_port:
        return True

    if strategy_parameters.EXPERIMENT in {"E1_3", "E1_4", "E1_5", "E5", "E6"}:
        candidate_bookings = build_default_equivalent_candidate_bookings(context, now)
    else:
        candidate_bookings = build_feasible_candidate_bookings(context, now)
    if not candidate_bookings:
        return None

    if strategy_parameters.EXPERIMENT == "E1_4":
        time_path = shortest_booking_path_with_operational_tie_break(
            context,
            origin_port,
            destination_port,
            candidate_bookings,
            _transition_cost,
        )
    elif strategy_parameters.EXPERIMENT == "E5":
        initial_wait_cache: dict[tuple[object, object], float] = {}

        def _transition_cost_with_initial_wait(previous_route, edge):
            sailing_hours = expected_sailing_hours(edge)
            if not math.isfinite(sailing_hours):
                return math.inf

            if previous_route is not None:
                return sailing_hours

            waiting_hours = estimate_initial_next_vessel_wait_hours(
                context,
                now,
                edge.service_route,
                edge.departure_port,
                initial_wait_cache,
            )
            if not math.isfinite(waiting_hours):
                return math.inf
            return sailing_hours + waiting_hours

        time_path = shortest_booking_path(
            context,
            origin_port,
            destination_port,
            candidate_bookings,
            _transition_cost_with_initial_wait,
        )
    elif strategy_parameters.EXPERIMENT == "E6":
        capacity_delay_cache: dict[tuple[object, object, int], float] = {}
        queued_teu_cache: dict[tuple[object, object, int], int] = {}

        def _transition_cost_with_capacity_delay(previous_route, edge):
            sailing_hours = expected_sailing_hours(edge)
            if not math.isfinite(sailing_hours):
                return math.inf

            if previous_route is not None:
                return sailing_hours

            capacity_delay_hours = estimate_capacity_delay_hours(
                origin_port,
                edge.service_route,
                edge.departure_segment_index,
                exclude_shipment=shipment,
                cache=capacity_delay_cache,
                queued_teu_cache=queued_teu_cache,
            )
            if not math.isfinite(capacity_delay_hours):
                return math.inf
            return sailing_hours + capacity_delay_hours

        time_path = shortest_booking_path(
            context,
            origin_port,
            destination_port,
            candidate_bookings,
            _transition_cost_with_capacity_delay,
        )
    elif strategy_parameters.EXPERIMENT == "E1_5":
        time_path = shortest_booking_path_default_semantics(
            context,
            origin_port,
            destination_port,
            candidate_bookings,
            _transition_cost,
        )
    else:
        time_path = shortest_booking_path(
            context,
            origin_port,
            destination_port,
            candidate_bookings,
            _transition_cost,
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
    if strategy_parameters.EXPERIMENT in {"E1_2", "E1_3", "E1_4", "E1_5", "E5", "E6"}:
        path = normalize_booking_path(path)

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


def estimate_queued_teu(
    port,
    service_route,
    departure_segment_index: int,
    exclude_shipment=None,
    cache: Optional[dict[tuple[object, object, int], int]] = None,
) -> int:
    """Count physically waiting TEU competing for a specific departure."""
    cache_key = (port, service_route, departure_segment_index)
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    queued_teu = 0
    for shipment in list(getattr(port, "shipments_in_storage", None) or []):
        if shipment is exclude_shipment:
            continue
        if getattr(shipment, "current_storage_port", None) is not port:
            continue
        if getattr(shipment, "carrying_vessel", None) is not None:
            continue
        current_booking_index = getattr(shipment, "current_booking_index", None)
        if current_booking_index is None:
            continue
        try:
            booking = shipment.get_current_booking()
        except ValueError:
            continue
        if booking.service_route is not service_route:
            continue
        if booking.departure_segment_index != departure_segment_index:
            continue
        queued_teu += int(getattr(shipment, "teu_size", 0) or 0)

    if cache is not None:
        cache[cache_key] = queued_teu
    return queued_teu


def estimate_nominal_capacity_teu(service_route) -> float:
    """Estimate representative vessel capacity for a route."""
    vessels = list(getattr(service_route, "deployed_vessels", None) or [])
    if not vessels:
        return math.inf

    capacities = []
    for vessel in vessels:
        vessel_class = getattr(vessel, "vessel_class", None)
        capacity = float(getattr(vessel_class, "teu_capacity", 0.0) or 0.0)
        if capacity > 0:
            capacities.append(capacity)
    if not capacities:
        return math.inf
    return sum(capacities) / len(capacities)


def estimate_capacity_delay_hours(
    port,
    service_route,
    departure_segment_index: int,
    exclude_shipment=None,
    cache: Optional[dict[tuple[object, object, int], float]] = None,
    queued_teu_cache: Optional[dict[tuple[object, object, int], int]] = None,
) -> float:
    """Estimate boarding delay from spillover beyond one vessel capacity."""
    cache_key = (port, service_route, departure_segment_index)
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    queued_teu = estimate_queued_teu(
        port,
        service_route,
        departure_segment_index,
        exclude_shipment=exclude_shipment,
        cache=queued_teu_cache,
    )
    nominal_capacity = estimate_nominal_capacity_teu(service_route)
    headway_hours = estimate_headway_hours(service_route)

    if (
        not math.isfinite(nominal_capacity)
        or nominal_capacity <= 0
        or not math.isfinite(headway_hours)
    ):
        delay_hours = math.inf
    else:
        pressure = queued_teu / nominal_capacity
        spillover = max(0.0, pressure - 1.0)
        delay_hours = spillover * headway_hours

    if cache is not None:
        cache[cache_key] = delay_hours
    return delay_hours


def estimate_initial_next_vessel_wait_hours(
    context,
    now,
    service_route,
    boarding_port,
    cache: Optional[dict[tuple[object, object], float]] = None,
) -> float:
    """Estimate the next compatible vessel wait at the boarding port.

    The estimate is computed from currently observable vessel position/state only:
      - current_segment
      - current_berth
      - assigned_service_route
      - route segment order
      - current sailing-time multipliers
      - route start_day_of_week for vessels not yet released

    The result is cached per (service_route, boarding_port) for a single
    booking assignment when ``cache`` is provided.
    """
    cache_key = (service_route, boarding_port)
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    vessels = list(getattr(service_route, "deployed_vessels", None) or [])
    if not vessels:
        wait_hours = math.inf
    else:
        wait_hours = min(
            (
                estimate_vessel_eta_to_port(vessel, boarding_port, now)
                for vessel in vessels
            ),
            default=math.inf,
        )

    if cache is not None:
        cache[cache_key] = wait_hours
    return wait_hours


def estimate_vessel_eta_to_port(vessel, boarding_port, now) -> float:
    """Estimate when a vessel can next serve a shipment at ``boarding_port``.

    Semantics used here:
      - Sailing vessels: remaining time to the next arrival at the target port
        along the current cyclic route order, including the current segment.
      - Berth / handling vessels: if already at the target port, wait is 0; otherwise
        the estimate starts from the next route segment after the vessel's current
        completed segment.
      - Awaiting-instructions vessels (``current_segment`` and ``current_berth`` are
        both ``None``): the estimate adds the route's next scheduled release delay
        from ``start_day_of_week`` and then the sailing time to the target port.

    This is intentionally conservative and uses only current observable state.
    """
    route = getattr(vessel, "assigned_service_route", None)
    if route is None or boarding_port is None:
        return math.inf

    segments = sorted(
        getattr(route, "segments", None) or [],
        key=lambda segment: segment.sequence_index,
    )
    if not segments:
        return math.inf

    vessel_class = getattr(vessel, "vessel_class", None)
    route_speed_knots = float(getattr(vessel_class, "sailing_speed", 0.0) or 0.0)
    if route_speed_knots <= 0:
        return math.inf

    current_segment = getattr(vessel, "current_segment", None)
    current_berth = getattr(vessel, "current_berth", None)

    if current_berth is not None:
        berth_port = getattr(current_berth, "port", None)
        if berth_port is boarding_port:
            return 0.0

        if current_segment is None:
            start_index = _find_first_departure_segment_index(segments, berth_port)
            if start_index is None:
                return math.inf
            return _estimate_remaining_route_time(
                segments,
                start_index,
                boarding_port,
                route_speed_knots,
            )

        next_index = _next_segment_position(segments, current_segment)
        if next_index is None:
            return math.inf
        return _estimate_remaining_route_time(
            segments,
            next_index,
            boarding_port,
            route_speed_knots,
        )

    if current_segment is None:
        initial_delay_hours = _route_initial_release_delay_hours(route, now)
        return initial_delay_hours + _estimate_remaining_route_time(
            segments,
            0,
            boarding_port,
            route_speed_knots,
        )

    current_leg = getattr(current_segment, "associated_leg", None)
    if current_leg is not None and current_leg.arrival_port is boarding_port:
        return _segment_sailing_hours(current_segment, route_speed_knots)

    start_index = _segment_position(segments, current_segment)
    if start_index is None:
        return math.inf
    return _estimate_remaining_route_time(
        segments,
        start_index,
        boarding_port,
        route_speed_knots,
    )


def _segment_position(segments, target_segment) -> Optional[int]:
    for index, segment in enumerate(segments):
        if segment is target_segment:
            return index
    target_sequence_index = getattr(target_segment, "sequence_index", None)
    if target_sequence_index is None:
        return None
    for index, segment in enumerate(segments):
        if segment.sequence_index == target_sequence_index:
            return index
    return None


def _next_segment_position(segments, current_segment) -> Optional[int]:
    current_index = _segment_position(segments, current_segment)
    if current_index is None:
        return None
    return (current_index + 1) % len(segments)


def _find_first_departure_segment_index(segments, port) -> Optional[int]:
    for index, segment in enumerate(segments):
        leg = getattr(segment, "associated_leg", None)
        if leg is not None and leg.departure_port is port:
            return index
    return None


def _segment_sailing_hours(segment, route_speed_knots: float) -> float:
    leg = getattr(segment, "associated_leg", None)
    if leg is None:
        return math.inf
    return (leg.sailing_distance / route_speed_knots) * leg.sailing_time_multiplier


def _estimate_remaining_route_time(
    segments,
    start_index: int,
    boarding_port,
    route_speed_knots: float,
) -> float:
    total_hours = 0.0
    segment_count = len(segments)
    for offset in range(segment_count):
        segment = segments[(start_index + offset) % segment_count]
        segment_hours = _segment_sailing_hours(segment, route_speed_knots)
        if not math.isfinite(segment_hours):
            return math.inf
        total_hours += segment_hours
        leg = getattr(segment, "associated_leg", None)
        if leg is not None and leg.arrival_port is boarding_port:
            return total_hours
    return math.inf


def _route_initial_release_delay_hours(route, now) -> float:
    start_day = getattr(route, "start_day_of_week", None)
    if start_day is None or now is None:
        return 0.0

    total_seconds = (
        now.hour * 3600
        + now.minute * 60
        + now.second
        + now.microsecond / 1e6
    )
    current_day_fraction = total_seconds / 86400.0
    current_day = now.weekday() + current_day_fraction

    delay_days = start_day - current_day
    if delay_days < 0:
        delay_days += 7.0
    return max(0.0, delay_days) * 24.0


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
        distance_transition_cost,
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


def _transition_cost(previous_route, edge) -> float:
    """Charge sailing time plus the correct route-entry wait for E1/E2/E3."""
    strategy_parameters.validate_wait_configuration()

    sailing_hours = expected_sailing_hours(edge)
    if not math.isfinite(sailing_hours):
        return math.inf

    if not strategy_parameters.ENABLE_INITIAL_WAIT:
        return sailing_hours

    waiting_hours = 0.0
    if previous_route is None:
        waiting_hours = estimate_service_wait_hours(edge.service_route)
    elif previous_route is edge.service_route:
        waiting_hours = 0.0
    elif strategy_parameters.ENABLE_TRANSFER_COST:
        waiting_hours = estimate_service_wait_hours(edge.service_route)

    if not math.isfinite(waiting_hours):
        return math.inf
    return sailing_hours + waiting_hours
