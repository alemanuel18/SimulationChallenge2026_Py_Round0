"""E8 alternative-route reservation helper.

This module preserves Default's alternative-route topology construction and
lifecycle, but changes only which vessel is reserved for a newly created
alternative service route.

It is intentionally self-contained to avoid importing ``simulation_model`` at
module load time. ``simulation_model`` imports ``UserStrategy`` in several
places, so importing it here would create a circular dependency.
"""

from __future__ import annotations

import datetime as dt
import math

from maritime_data_context import Segment, ServiceRoute


def create_alternative_service_routes(context, now, vessel=None):
    """Run the Default alternative-route lifecycle with E8 vessel ranking."""
    _restore_inactive_alternative_route_vessels(context, now, vessel)
    if not _is_disruption_active(context, now):
        return False

    _ensure_alternative_service_routes(context, now)
    _try_switch_empty_vessel_to_pending_route(vessel)
    return False


def _is_disruption_active(context, now) -> bool:
    for plan in getattr(context, "disruption_plans", []):
        if plan.start_offset_days is None or plan.duration_days is None:
            continue
        start = dt.datetime.min + dt.timedelta(days=plan.start_offset_days)
        end = start + dt.timedelta(days=plan.duration_days)
        if start <= now < end:
            return True
    return False


def _get_active_disruption_plans(context, now):
    close_berth_plans = []
    congested_leg_plans = []
    for plan in getattr(context, "disruption_plans", []):
        if plan.start_offset_days is None or plan.duration_days is None:
            continue
        start = dt.datetime.min + dt.timedelta(days=plan.start_offset_days)
        end = start + dt.timedelta(days=plan.duration_days)
        if not (start <= now < end):
            continue
        if plan.close_berth:
            close_berth_plans.append(plan)
        elif getattr(plan, "multiplier", 1.0) > 1.0:
            congested_leg_plans.append(plan)
    return close_berth_plans, congested_leg_plans


def _get_avoid_port_names(close_berth_plans):
    return {
        plan.target_berth.port.name.casefold()
        for plan in close_berth_plans
        if getattr(plan, "target_berth", None) is not None
    }


def _get_congested_leg_keys(congested_leg_plans):
    return {
        _leg_key(plan.target_leg)
        for plan in congested_leg_plans
        if getattr(plan, "target_leg", None) is not None
    }


def _get_active_disruption_key(context, now):
    close_berth_plans, congested_leg_plans = _get_active_disruption_plans(
        context, now
    )
    return (
        tuple(sorted(_get_avoid_port_names(close_berth_plans))),
        tuple(sorted(_get_congested_leg_keys(congested_leg_plans))),
    )


def _ensure_alternative_service_routes(context, now):
    close_berth_plans, congested_leg_plans = _get_active_disruption_plans(
        context, now
    )
    avoid_port_names = _get_avoid_port_names(close_berth_plans)
    congested_leg_keys = _get_congested_leg_keys(congested_leg_plans)
    if not avoid_port_names and not congested_leg_keys:
        return []

    disruption_key = (
        tuple(sorted(avoid_port_names)),
        tuple(sorted(congested_leg_keys)),
    )

    alternatives = []
    for source_route in list(getattr(context, "initial_service_routes", [])):
        if not _service_route_is_disrupted(
            source_route, avoid_port_names, congested_leg_keys
        ):
            continue

        alternative_route = next(
            (
                route
                for route in getattr(context, "service_routes", [])
                if route.source_service_route is source_route
                and route.disruption_key == disruption_key
            ),
            None,
        )
        if alternative_route is None:
            alternative_route = _build_alternative_service_route(
                context,
                source_route,
                avoid_port_names,
                congested_leg_keys,
                disruption_key,
            )
        if alternative_route is None:
            continue

        _reserve_one_vessel_for_alternative_route(
            context, source_route, alternative_route
        )
        alternatives.append(alternative_route)

    return alternatives


def _service_route_is_disrupted(route, avoid_port_names, congested_leg_keys):
    for segment in sorted(route.segments, key=lambda segment: segment.sequence_index):
        leg = segment.associated_leg
        if leg is None:
            continue
        if _leg_key(leg) in congested_leg_keys:
            return True
        if (
            leg.departure_port.name.casefold() in avoid_port_names
            or leg.arrival_port.name.casefold() in avoid_port_names
        ):
            return True
    return False


def _build_alternative_service_route(
    context,
    source_route,
    avoid_port_names,
    congested_leg_keys,
    disruption_key,
):
    source_segments = sorted(
        source_route.segments, key=lambda segment: segment.sequence_index
    )
    anchor_ports = []
    for segment in source_segments:
        port = segment.associated_leg.departure_port
        if port.name.casefold() in avoid_port_names:
            continue
        if not anchor_ports or anchor_ports[-1] is not port:
            anchor_ports.append(port)

    if len(anchor_ports) < 2:
        return None

    route_legs = []
    for index, departure_port in enumerate(anchor_ports):
        arrival_port = anchor_ports[(index + 1) % len(anchor_ports)]
        leg_path = _find_shortest_leg_path(
            context,
            departure_port,
            arrival_port,
            avoid_port_names,
            congested_leg_keys,
        )
        if not leg_path:
            return None
        route_legs.extend(leg_path)

    route_id = _next_alternative_route_id(context, source_route.id)
    route = ServiceRoute(
        id=route_id,
        name=f"{source_route.name} Disruption Alternative",
        start_day_of_week=source_route.start_day_of_week,
    )
    route.source_service_route = source_route
    route.disruption_key = disruption_key
    for sequence_index, leg in enumerate(route_legs, start=1):
        segment = Segment(sequence_index, leg, route)
        route.segments.append(segment)
        leg.segments.append(segment)
        getattr(context, "partial_service_routes", []).append(segment)

    getattr(context, "service_routes", []).append(route)
    return route


def _next_alternative_route_id(context, source_route_id):
    existing_ids = {route.id.casefold() for route in getattr(context, "service_routes", [])}
    index = 1
    while True:
        route_id = f"{source_route_id}-ALT-{index}"
        if route_id.casefold() not in existing_ids:
            return route_id
        index += 1


def _find_shortest_leg_path(
    context,
    origin_port,
    destination_port,
    avoid_port_names,
    congested_leg_keys,
):
    outgoing = {}
    for leg in getattr(context, "legs", []):
        if _leg_key(leg) in congested_leg_keys:
            continue
        if (
            leg.departure_port.name.casefold() in avoid_port_names
            or leg.arrival_port.name.casefold() in avoid_port_names
        ):
            continue
        outgoing.setdefault(leg.departure_port, []).append(leg)

    distances = {port: math.inf for port in getattr(context, "ports", [])}
    previous_leg = {}
    unvisited = set(getattr(context, "ports", []))
    distances[origin_port] = 0.0

    while unvisited:
        current = min(unvisited, key=lambda port: distances[port])
        if math.isinf(distances[current]) or current is destination_port:
            break
        unvisited.remove(current)
        for leg in outgoing.get(current, []):
            next_port = leg.arrival_port
            if next_port not in unvisited:
                continue
            alternative = distances[current] + leg.sailing_distance
            if alternative < distances[next_port]:
                distances[next_port] = alternative
                previous_leg[next_port] = leg

    if destination_port not in previous_leg:
        return None

    path = []
    cursor = destination_port
    while cursor is not origin_port:
        leg = previous_leg.get(cursor)
        if leg is None:
            return None
        path.append(leg)
        cursor = leg.departure_port
    path.reverse()
    return path


def _reserve_one_vessel_for_alternative_route(context, source_route, alternative_route):
    if any(
        vessel.assigned_service_route is alternative_route
        or vessel.pending_assigned_service_route is alternative_route
        for vessel in getattr(context, "vessels", [])
    ):
        return

    candidates = [
        vessel
        for vessel in getattr(source_route, "deployed_vessels", [])
        if vessel.assigned_service_route is source_route
        and vessel.pending_assigned_service_route is None
        and not vessel.carried_shipments
    ]
    if not candidates:
        return

    selected_vessel = min(
        candidates,
        key=lambda vessel: _reservation_priority(vessel, alternative_route),
    )
    selected_vessel.pending_assigned_service_route = alternative_route


def _reservation_priority(vessel, alternative_route):
    start_port = _get_alternative_start_port(alternative_route)
    current_port = _get_vessel_current_port(vessel)
    immediate_switchable = current_port is not None and current_port is start_port
    repositioning_hours = (
        0.0
        if immediate_switchable
        else _estimate_remaining_hours_until_port(vessel, start_port)
    )
    return (
        0 if immediate_switchable else 1,
        repositioning_hours,
        vessel.index,
    )


def _estimate_remaining_hours_until_port(vessel, target_port):
    if target_port is None:
        return math.inf

    current_port = _get_vessel_current_port(vessel)
    if current_port is None:
        return math.inf
    if current_port is target_port:
        return 0.0

    route = vessel.assigned_service_route
    if route is None or not route.segments:
        return math.inf

    segments = sorted(route.segments, key=lambda segment: segment.sequence_index)
    current_segment = vessel.current_segment

    if current_segment is None:
        start_index = _find_departure_segment_index(segments, current_port)
        if start_index is None:
            return math.inf
    else:
        current_index = _find_segment_list_index(
            segments, current_segment.sequence_index
        )
        if current_index < 0:
            return math.inf
        start_index = (current_index + 1) % len(segments)

    total_hours = 0.0
    cursor = start_index
    for _ in range(len(segments)):
        leg = segments[cursor].associated_leg
        if leg is None:
            return math.inf
        total_hours += _expected_sailing_hours(vessel, leg)
        if leg.arrival_port is target_port:
            return total_hours
        cursor = (cursor + 1) % len(segments)

    return math.inf


def _expected_sailing_hours(vessel, leg):
    vessel_class = getattr(vessel, "vessel_class", None)
    speed = getattr(vessel_class, "sailing_speed", 0) or 0
    if speed <= 0:
        return math.inf
    return (
        leg.sailing_distance / speed
    ) * (getattr(leg, "sailing_time_multiplier", 1.0) or 1.0)


def _get_alternative_start_port(alternative_route):
    segments = sorted(alternative_route.segments, key=lambda segment: segment.sequence_index)
    if not segments:
        return None
    first_segment = segments[0]
    leg = first_segment.associated_leg
    return leg.departure_port if leg is not None else None


def _find_departure_segment_index(segments, current_port):
    for index, segment in enumerate(segments):
        leg = segment.associated_leg
        if leg is not None and leg.departure_port is current_port:
            return index
    return None


def _find_segment_list_index(segments, sequence_index):
    return next(
        (
            index
            for index, segment in enumerate(segments)
            if segment.sequence_index == sequence_index
        ),
        -1,
    )


def _try_switch_empty_vessel_to_pending_route(vessel):
    if vessel is None or vessel.carried_shipments:
        return False

    pending_route = vessel.pending_assigned_service_route
    if pending_route is None or not pending_route.segments:
        return False

    current_port = _get_vessel_current_port(vessel)
    first_segment = min(
        pending_route.segments, key=lambda segment: segment.sequence_index
    )
    start_port = first_segment.associated_leg.departure_port
    if current_port is not start_port:
        return False

    current_segment = vessel.current_segment
    old_route = vessel.assigned_service_route
    if current_segment is not None:
        while vessel in current_segment.current_vessels:
            current_segment.current_vessels.remove(vessel)
    if old_route is not None:
        while vessel in old_route.deployed_vessels:
            old_route.deployed_vessels.remove(vessel)
    if vessel not in pending_route.deployed_vessels:
        pending_route.deployed_vessels.append(vessel)

    vessel.assigned_service_route = pending_route
    vessel.pending_assigned_service_route = None
    vessel.current_segment = None
    return True


def _try_switch_empty_vessel_to_source_route(vessel):
    if vessel is None or vessel.carried_shipments:
        return False

    alternative_route = vessel.assigned_service_route
    if alternative_route is None or alternative_route.source_service_route is None:
        return False

    source_route = alternative_route.source_service_route
    current_port = _get_vessel_current_port(vessel)
    if current_port is None:
        return False

    reentry_segment = _find_reentry_segment(source_route, current_port)
    if reentry_segment is None:
        return False

    current_segment = vessel.current_segment
    if current_segment is not None:
        while vessel in current_segment.current_vessels:
            current_segment.current_vessels.remove(vessel)
    while vessel in alternative_route.deployed_vessels:
        alternative_route.deployed_vessels.remove(vessel)
    if vessel not in source_route.deployed_vessels:
        source_route.deployed_vessels.append(vessel)

    vessel.assigned_service_route = source_route
    vessel.pending_assigned_service_route = None
    vessel.current_segment = reentry_segment
    if vessel not in reentry_segment.current_vessels:
        reentry_segment.current_vessels.append(vessel)
    return True


def _restore_inactive_alternative_route_vessels(context, now, vessel=None):
    vessels = [vessel] if vessel is not None else list(getattr(context, "vessels", []))
    active_disruption_key = _get_active_disruption_key(context, now)

    for current_vessel in vessels:
        if current_vessel is None:
            continue
        pending_route = current_vessel.pending_assigned_service_route
        if (
            pending_route is not None
            and pending_route.source_service_route is not None
            and pending_route.disruption_key != active_disruption_key
        ):
            current_vessel.pending_assigned_service_route = None

        assigned_route = current_vessel.assigned_service_route
        if (
            assigned_route is None
            or assigned_route.source_service_route is None
            or assigned_route.disruption_key == active_disruption_key
        ):
            continue
        _try_switch_empty_vessel_to_source_route(current_vessel)


def _get_vessel_current_port(vessel):
    current_segment = vessel.current_segment
    if current_segment is not None and current_segment.associated_leg is not None:
        return current_segment.associated_leg.arrival_port
    if vessel.current_berth is not None:
        return vessel.current_berth.port
    return None


def _find_reentry_segment(route, current_port):
    segments = sorted(route.segments, key=lambda segment: segment.sequence_index)
    for segment in segments:
        leg = segment.associated_leg
        if leg is not None and leg.arrival_port is current_port:
            return segment
    for segment in segments:
        leg = segment.associated_leg
        if leg is not None and leg.departure_port is current_port:
            return None
    return None


def _leg_key(leg):
    return (
        leg.departure_port.name.casefold(),
        leg.arrival_port.name.casefold(),
    )
