"""Shared routing helpers for response strategies."""

from __future__ import annotations

import datetime as dt
import heapq
import logging
import math
import weakref
from dataclasses import dataclass
from itertools import count
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

_TOPOLOGY_CACHE: "weakref.WeakKeyDictionary[object, tuple[RouteSpan, ...]]" = (
    weakref.WeakKeyDictionary()
)
_SPAN_LOOKUP_CACHE: "weakref.WeakKeyDictionary[object, dict[tuple[object, object, object, int, int], RouteSpan]]" = (
    weakref.WeakKeyDictionary()
)


@dataclass(frozen=True)
class RouteSpan:
    """Static route-topology span between two ports on one service route."""

    service_route: object
    departure_port: object
    arrival_port: object
    departure_segment_index: int
    arrival_segment_index: int
    sailing_distance_nm: float
    traversed_segments: tuple[object, ...]
    traversed_ports: tuple[object, ...]


def build_feasible_candidate_bookings(context, now) -> list[RouteSpan]:
    """Build all feasible candidate bookings using the current disruption state."""
    close_berth_plans, congested_leg_plans = _get_active_disruption_plans(
        context, now
    )
    avoid_port_names = _get_avoid_port_names(close_berth_plans)
    disruption_key = (
        tuple(sorted(avoid_port_names)),
        tuple(sorted(_leg_key(plan.target_leg) for plan in congested_leg_plans if plan.target_leg is not None)),
    )

    spans = []
    for route in _get_route_spans(context):
        if not _route_is_available_for_booking(route.service_route, disruption_key):
            continue
        spans.append(route)

    return spans


def shortest_booking_path(
    context,
    origin_port,
    destination_port,
    candidate_bookings: Iterable[RouteSpan],
    transition_cost_fn: Callable[[object, RouteSpan], float],
    initial_previous_route: object = None,
) -> Optional[list[RouteSpan]]:
    """Return the minimum-cost booking path using Dijkstra over route-aware state."""
    outgoing: dict[object, list[RouteSpan]] = {}
    for edge in candidate_bookings:
        outgoing.setdefault(edge.departure_port, []).append(edge)

    start_state = (origin_port, initial_previous_route)
    distances = {start_state: 0.0}
    previous_edge: dict[tuple[object, object], RouteSpan] = {}
    previous_state: dict[tuple[object, object], tuple[object, object]] = {}
    heap: list[tuple[float, int, tuple[object, object]]] = []
    push_order = count()

    heapq.heappush(heap, (0.0, next(push_order), start_state))

    destination_state = None
    while heap:
        current_distance, _, current_state = heapq.heappop(heap)
        if current_distance != distances.get(current_state):
            continue
        current_port, current_route = current_state
        if current_port is destination_port:
            destination_state = current_state
            break

        for edge in outgoing.get(current_port, []):
            next_port = edge.arrival_port
            next_state = (next_port, edge.service_route)
            step_cost = transition_cost_fn(current_route, edge)
            alternative = current_distance + step_cost
            if alternative < distances.get(next_state, math.inf):
                distances[next_state] = alternative
                previous_edge[next_state] = edge
                previous_state[next_state] = current_state
                heapq.heappush(heap, (alternative, next(push_order), next_state))

    if destination_state is None:
        return None

    path: list[RouteSpan] = []
    cursor = destination_state
    while cursor != start_state:
        edge = previous_edge.get(cursor)
        if edge is None:
            return None
        path.append(edge)
        cursor = previous_state.get(cursor)
        if cursor is None:
            return None
    path.reverse()
    return path


def remove_bookings_from_service_routes(bookings) -> None:
    """Remove stale reverse references from service routes."""
    for booking in bookings:
        service_route = booking.service_route
        if service_route is None:
            continue
        while booking in service_route.associated_bookings:
            service_route.associated_bookings.remove(booking)


def expected_sailing_hours(edge: RouteSpan) -> float:
    """Expected sailing time in simulator hours for one candidate booking edge."""
    route_speed_knots = _get_service_route_speed_knots(edge.service_route)
    if route_speed_knots <= 0:
        return math.inf
    total_hours = 0.0
    for segment in edge.traversed_segments:
        leg = segment.associated_leg
        if leg is None:
            return math.inf
        total_hours += (
            leg.sailing_distance / route_speed_knots
        ) * leg.sailing_time_multiplier
    return total_hours


def distance_cost(edge: RouteSpan) -> float:
    """Distance-only cost used for diagnostics."""
    return edge.sailing_distance_nm


def distance_transition_cost(previous_route, edge: RouteSpan) -> float:
    """Distance-only transition cost used for diagnostics and comparisons."""
    return edge.sailing_distance_nm


def evaluate_booking_path_cost(
    path: Optional[Iterable[RouteSpan]],
    transition_cost_fn: Callable[[object, RouteSpan], float],
    initial_previous_route: object = None,
) -> float:
    """Evaluate a concrete booking path under the same route-aware transition cost."""
    if path is None:
        return math.inf

    total = 0.0
    previous_route = initial_previous_route
    for edge in path:
        step_cost = transition_cost_fn(previous_route, edge)
        if not math.isfinite(step_cost):
            return math.inf
        total += step_cost
        previous_route = edge.service_route
    return total


def build_path_signature(path: Optional[Iterable[RouteSpan]]) -> tuple:
    """Compact signature for debug comparison output."""
    if path is None:
        return ()
    return tuple(
        (
            edge.service_route.id,
            edge.departure_segment_index,
            edge.arrival_segment_index,
            edge.departure_port.name,
            edge.arrival_port.name,
        )
        for edge in path
    )


def build_canonical_path_signature(path: Optional[Iterable[RouteSpan]]) -> tuple:
    """Compact signature after merging contiguous same-service spans."""
    normalized = normalize_booking_path(path)
    return build_path_signature(normalized)


def normalize_booking_path(path: Optional[Iterable[RouteSpan]]) -> list[RouteSpan]:
    """Merge contiguous spans that stay on the same service route."""
    if path is None:
        return []

    normalized: list[RouteSpan] = []
    for edge in path:
        if not normalized:
            normalized.append(edge)
            continue

        previous = normalized[-1]
        if _can_merge_spans(previous, edge):
            normalized[-1] = RouteSpan(
                service_route=previous.service_route,
                departure_port=previous.departure_port,
                arrival_port=edge.arrival_port,
                departure_segment_index=previous.departure_segment_index,
                arrival_segment_index=edge.arrival_segment_index,
                sailing_distance_nm=previous.sailing_distance_nm + edge.sailing_distance_nm,
                traversed_segments=previous.traversed_segments + edge.traversed_segments,
                traversed_ports=previous.traversed_ports + edge.traversed_ports,
            )
        else:
            normalized.append(edge)

    return normalized


def _get_route_spans(context) -> tuple[RouteSpan, ...]:
    cached = _TOPOLOGY_CACHE.get(context)
    if cached is not None:
        return cached

    spans: list[RouteSpan] = []
    for service_route in context.service_routes:
        segments = sorted(
            service_route.segments, key=lambda segment: segment.sequence_index
        )
        segment_count = len(segments)
        if segment_count < 2:
            continue

        for start_index in range(segment_count):
            departure_port = segments[start_index].associated_leg.departure_port
            cumulative_distance = 0.0
            traversed_segments = []
            traversed_ports = []

            for step in range(1, segment_count):
                segment_index = (start_index + step - 1) % segment_count
                segment = segments[segment_index]
                leg = segment.associated_leg
                if leg is None:
                    continue

                cumulative_distance += leg.sailing_distance
                traversed_segments.append(segment)
                traversed_ports.append(leg.arrival_port)

                arrival_port = leg.arrival_port
                if departure_port == arrival_port:
                    continue

                spans.append(
                    RouteSpan(
                        service_route=service_route,
                        departure_port=departure_port,
                        arrival_port=arrival_port,
                        departure_segment_index=start_index + 1,
                        arrival_segment_index=segment_index + 1,
                        sailing_distance_nm=cumulative_distance,
                        traversed_segments=tuple(traversed_segments),
                        traversed_ports=tuple(traversed_ports),
                    )
                )

    cached = tuple(spans)
    _TOPOLOGY_CACHE[context] = cached
    return cached


def build_route_span_lookup(context) -> dict[tuple[object, object, object, int, int], RouteSpan]:
    """Return a cached lookup from route-span identity to RouteSpan."""
    cached = _SPAN_LOOKUP_CACHE.get(context)
    if cached is not None:
        return cached

    lookup: dict[tuple[object, object, object, int, int], RouteSpan] = {}
    for span in _get_route_spans(context):
        lookup[
            (
                span.service_route,
                span.departure_port,
                span.arrival_port,
                span.departure_segment_index,
                span.arrival_segment_index,
            )
        ] = span

    _SPAN_LOOKUP_CACHE[context] = lookup
    return lookup


def find_route_span(
    context,
    service_route,
    departure_port,
    arrival_port,
    departure_segment_index: int,
    arrival_segment_index: int,
) -> Optional[RouteSpan]:
    """Find one cached route span matching the supplied identity fields."""
    return build_route_span_lookup(context).get(
        (
            service_route,
            departure_port,
            arrival_port,
            departure_segment_index,
            arrival_segment_index,
        )
    )


def _get_service_route_speed_knots(service_route) -> float:
    vessels = getattr(service_route, "deployed_vessels", None) or []
    if not vessels:
        return 0.0
    vessel = vessels[0]
    vessel_class = getattr(vessel, "vessel_class", None)
    return float(getattr(vessel_class, "sailing_speed", 0.0) or 0.0)


def _can_merge_spans(previous: RouteSpan, edge: RouteSpan) -> bool:
    if previous.service_route is not edge.service_route:
        return False
    if previous.arrival_port is not edge.departure_port:
        return False
    next_departure_index = _next_segment_index(previous.service_route, previous.arrival_segment_index)
    return next_departure_index == edge.departure_segment_index


def _next_segment_index(service_route, segment_index: int) -> int:
    segments = sorted(getattr(service_route, "segments", None) or [], key=lambda segment: segment.sequence_index)
    if not segments:
        return segment_index
    sequence_indices = [segment.sequence_index for segment in segments]
    try:
        current_pos = sequence_indices.index(segment_index)
    except ValueError:
        return segment_index
    return sequence_indices[(current_pos + 1) % len(sequence_indices)]


def _is_active(plan, now: dt.datetime) -> bool:
    if plan.start_offset_days is None or plan.duration_days is None:
        return False
    start = dt.datetime.min + dt.timedelta(days=plan.start_offset_days)
    end = start + dt.timedelta(days=plan.duration_days)
    return start <= now < end


def _get_active_disruption_plans(context, now):
    close_berth_plans = [
        plan for plan in context.disruption_plans
        if plan.close_berth and _is_active(plan, now)
    ]
    congested_leg_plans = [
        plan for plan in context.disruption_plans
        if plan.multiplier > 1 and _is_active(plan, now)
    ]
    return close_berth_plans, congested_leg_plans


def _get_avoid_port_names(close_berth_plans):
    return {
        plan.target_berth.port.name.casefold()
        for plan in close_berth_plans
        if plan.target_berth is not None
    }


def _get_congested_legs(congested_leg_plans):
    return {
        plan.target_leg
        for plan in congested_leg_plans
        if plan.target_leg is not None
    }


def _route_is_available_for_booking(route, disruption_key):
    if getattr(route, "source_service_route", None) is None:
        return True
    if getattr(route, "disruption_key", None) != disruption_key:
        return False
    return bool(getattr(route, "deployed_vessels", None))


def _leg_key(leg):
    return (
        leg.departure_port.name.casefold(),
        leg.arrival_port.name.casefold(),
    )
