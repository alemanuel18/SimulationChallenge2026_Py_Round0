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


@dataclass(frozen=True)
class RouteSpan:
    """Static route-topology span between two ports on one service route."""

    service_route: object
    departure_port: object
    arrival_port: object
    departure_segment_index: int
    arrival_segment_index: int
    sailing_distance_nm: float
    route_speed_knots: float
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

        if route.route_speed_knots <= 0:
            continue

        spans.append(route)

    return spans


def shortest_booking_path(
    context,
    origin_port,
    destination_port,
    candidate_bookings: Iterable[RouteSpan],
    cost_fn: Callable[[RouteSpan], float],
) -> Optional[list[RouteSpan]]:
    """Return the minimum-cost port path using Dijkstra over candidate bookings."""
    outgoing: dict[object, list[RouteSpan]] = {}
    for edge in candidate_bookings:
        outgoing.setdefault(edge.departure_port, []).append(edge)

    distances = {port: math.inf for port in context.ports}
    previous_edge: dict[object, RouteSpan] = {}
    heap: list[tuple[float, int, object]] = []
    push_order = count()

    distances[origin_port] = 0.0
    heapq.heappush(heap, (0.0, next(push_order), origin_port))

    while heap:
        current_distance, _, current_port = heapq.heappop(heap)
        if current_distance != distances.get(current_port):
            continue
        if current_port is destination_port:
            break

        for edge in outgoing.get(current_port, []):
            next_port = edge.arrival_port
            alternative = current_distance + cost_fn(edge)
            if alternative < distances.get(next_port, math.inf):
                distances[next_port] = alternative
                previous_edge[next_port] = edge
                heapq.heappush(heap, (alternative, next(push_order), next_port))

    if destination_port not in previous_edge:
        return None

    path: list[RouteSpan] = []
    cursor = destination_port
    while cursor is not origin_port:
        edge = previous_edge.get(cursor)
        if edge is None:
            return None
        path.append(edge)
        cursor = edge.departure_port
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
    total_hours = 0.0
    for segment in edge.traversed_segments:
        leg = segment.associated_leg
        if leg is None:
            return math.inf
        total_hours += (
            leg.sailing_distance / edge.route_speed_knots
        ) * leg.sailing_time_multiplier
    return total_hours


def distance_cost(edge: RouteSpan) -> float:
    """Distance-only cost used for diagnostics."""
    return edge.sailing_distance_nm


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


def _get_route_spans(context) -> tuple[RouteSpan, ...]:
    cached = _TOPOLOGY_CACHE.get(context)
    if cached is not None:
        return cached

    spans: list[RouteSpan] = []
    for service_route in context.service_routes:
        route_speed_knots = _get_service_route_speed_knots(service_route)
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
                        route_speed_knots=route_speed_knots,
                        traversed_segments=tuple(traversed_segments),
                        traversed_ports=tuple(traversed_ports),
                    )
                )

    cached = tuple(spans)
    _TOPOLOGY_CACHE[context] = cached
    return cached


def _get_service_route_speed_knots(service_route) -> float:
    vessels = getattr(service_route, "deployed_vessels", None) or []
    if not vessels:
        return 0.0
    vessel = vessels[0]
    vessel_class = getattr(vessel, "vessel_class", None)
    return float(getattr(vessel_class, "sailing_speed", 0.0) or 0.0)


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
