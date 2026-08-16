"""Expected-time routing helpers for response strategies."""

from dataclasses import dataclass
import datetime as dt
import heapq
import itertools
import math

from maritime_data_context import Booking


@dataclass(frozen=True)
class CandidateBookingEdge:
    service_route: object
    departure_port: object
    arrival_port: object
    departure_segment_index: int
    arrival_segment_index: int
    sailing_days: float
    expected_cost_days: float
    disruption_penalty_days: float


@dataclass(frozen=True)
class DisruptionSnapshot:
    active_closed_ports: frozenset
    risk_closed_ports: frozenset
    congested_leg_multipliers: dict


def build_disruption_snapshot(context, now, params) -> DisruptionSnapshot:
    active_closed_ports = set()
    risk_closed_ports = set()
    congested_leg_multipliers = {}

    current_day = _simulation_day(now)
    for plan in getattr(context, "disruption_plans", []):
        if plan.start_offset_days is None or plan.duration_days is None:
            continue

        start_day = float(plan.start_offset_days)
        end_day = start_day + float(plan.duration_days)
        is_active = start_day <= current_day < end_day
        is_risk = (
            current_day < start_day
            and start_day - current_day <= params.disruption_lookahead_days
        )
        if not is_active and not is_risk:
            continue

        if plan.close_berth and plan.target_berth is not None:
            port_name = plan.target_berth.port.name.casefold()
            if is_active:
                active_closed_ports.add(port_name)
            else:
                risk_closed_ports.add(port_name)
            continue

        if plan.target_leg is not None and plan.multiplier > 1:
            multiplier = float(plan.multiplier)
            if is_risk:
                multiplier = 1.0 + (multiplier - 1.0) * 0.55
            current = congested_leg_multipliers.get(plan.target_leg, 1.0)
            congested_leg_multipliers[plan.target_leg] = max(current, multiplier)

    return DisruptionSnapshot(
        active_closed_ports=frozenset(active_closed_ports),
        risk_closed_ports=frozenset(risk_closed_ports),
        congested_leg_multipliers=congested_leg_multipliers,
    )


def assign_min_expected_time_bookings(context, now, shipment, params):
    demand = shipment.demand
    origin_port = demand.origin_port
    destination_port = demand.destination_port

    remove_bookings_from_service_routes(shipment.associated_bookings)
    shipment.associated_bookings = []
    shipment.current_booking_index = None

    if origin_port is destination_port:
        return True

    snapshot = build_disruption_snapshot(context, now, params)
    if (
        origin_port.name.casefold() in snapshot.active_closed_ports
        or destination_port.name.casefold() in snapshot.active_closed_ports
    ):
        return False

    path = find_cached_min_cost_path(
        context, origin_port, destination_port, snapshot, params
    )
    if not path:
        return False

    apply_booking_path(shipment, path, starting_sequence=1)
    shipment.current_booking_index = 1
    return True


def find_cached_min_cost_path(context, origin_port, destination_port, snapshot, params):
    cache = getattr(context, "_critical_time_path_cache", None)
    if cache is None:
        cache = {}
        context._critical_time_path_cache = cache

    key = (
        origin_port.name,
        destination_port.name,
        _snapshot_key(snapshot),
        len(getattr(context, "service_routes", [])),
    )
    if key not in cache:
        edges = build_candidate_booking_edges(context, snapshot, params)
        cache[key] = find_min_cost_path(
            context, origin_port, destination_port, edges, params
        )
    return cache[key]


def build_candidate_booking_edges(context, snapshot, params):
    edges = []
    route_pressure = {
        route: _route_pressure(route, context, params)
        for route in getattr(context, "service_routes", [])
    }
    port_pressure = {
        port: _port_pressure_days(port, context, params)
        for port in getattr(context, "ports", [])
    }

    for service_route in getattr(context, "service_routes", []):
        segments = sorted(service_route.segments, key=lambda segment: segment.sequence_index)
        segment_count = len(segments)
        if segment_count < 2:
            continue

        route_has_service = _route_has_service(service_route, context)
        pressure_penalty = route_pressure.get(service_route, 0.0)
        if not route_has_service and service_route.source_service_route is None:
            continue

        for start_index in range(segment_count):
            departure_port = segments[start_index].associated_leg.departure_port
            if departure_port.name.casefold() in snapshot.active_closed_ports:
                continue

            sailing_days = 0.0
            disruption_penalty = 0.0
            for step in range(1, segment_count):
                segment_index = (start_index + step - 1) % segment_count
                segment = segments[segment_index]
                leg = segment.associated_leg
                arrival_port = leg.arrival_port

                if arrival_port.name.casefold() in snapshot.active_closed_ports:
                    break

                leg_days, leg_penalty = _leg_expected_days(leg, service_route, snapshot, params)
                sailing_days += leg_days
                disruption_penalty += leg_penalty

                if departure_port is arrival_port:
                    continue

                risk_penalty = 0.0
                if arrival_port.name.casefold() in snapshot.risk_closed_ports:
                    risk_penalty += params.closed_port_risk_penalty_days
                if not route_has_service:
                    risk_penalty += params.no_deployed_vessel_penalty_days

                expected_cost = (
                    params.expected_route_wait_days
                    + sailing_days
                    + disruption_penalty
                    + risk_penalty
                    + pressure_penalty * params.route_pressure_penalty_days
                    + port_pressure.get(arrival_port, 0.0)
                )
                edges.append(
                    CandidateBookingEdge(
                        service_route=service_route,
                        departure_port=departure_port,
                        arrival_port=arrival_port,
                        departure_segment_index=segments[start_index].sequence_index,
                        arrival_segment_index=segment.sequence_index,
                        sailing_days=sailing_days,
                        expected_cost_days=expected_cost,
                        disruption_penalty_days=disruption_penalty + risk_penalty,
                    )
                )

    return edges


def _snapshot_key(snapshot):
    congested = tuple(
        sorted(
            (
                leg.departure_port.name.casefold(),
                leg.arrival_port.name.casefold(),
                round(multiplier, 3),
            )
            for leg, multiplier in snapshot.congested_leg_multipliers.items()
        )
    )
    return (
        tuple(sorted(snapshot.active_closed_ports)),
        tuple(sorted(snapshot.risk_closed_ports)),
        congested,
    )


def find_min_cost_path(context, origin_port, destination_port, edges, params):
    outgoing = {}
    for edge in edges:
        outgoing.setdefault(edge.departure_port, []).append(edge)

    distances = {port: math.inf for port in context.ports}
    previous = {}
    tie_breaker = itertools.count()
    heap = [(0.0, next(tie_breaker), origin_port, None)]
    distances[origin_port] = 0.0

    while heap:
        cost, _, port, previous_route = heapq.heappop(heap)
        if cost > distances[port]:
            continue
        if port is destination_port:
            break

        for edge in outgoing.get(port, []):
            transfer_penalty = 0.0
            if previous_route is not None and previous_route is not edge.service_route:
                transfer_penalty = params.transshipment_penalty_days
            alternative = cost + edge.expected_cost_days + transfer_penalty
            if alternative < distances[edge.arrival_port]:
                distances[edge.arrival_port] = alternative
                previous[edge.arrival_port] = (port, edge)
                heapq.heappush(
                    heap,
                    (alternative, next(tie_breaker), edge.arrival_port, edge.service_route),
                )

    if destination_port not in previous:
        return None

    path = []
    cursor = destination_port
    while cursor is not origin_port:
        item = previous.get(cursor)
        if item is None:
            return None
        cursor, edge = item
        path.append(edge)
    path.reverse()
    return path


def apply_booking_path(shipment, path, starting_sequence):
    sequence = starting_sequence
    for edge in path:
        booking = Booking(
            sequence_index=sequence,
            shipment=shipment,
            service_route=edge.service_route,
            departure_segment_index=edge.departure_segment_index,
            arrival_segment_index=edge.arrival_segment_index,
        )
        shipment.associated_bookings.append(booking)
        edge.service_route.associated_bookings.append(booking)
        sequence += 1


def replan_carried_shipments(context, now, vessel, params):
    current_segment = vessel.current_segment
    if current_segment is None or current_segment.associated_leg is None:
        return True

    current_port = current_segment.associated_leg.arrival_port
    snapshot = build_disruption_snapshot(context, now, params)
    if (
        not snapshot.active_closed_ports
        and not snapshot.risk_closed_ports
        and not snapshot.congested_leg_multipliers
    ):
        return True

    for shipment in list(vessel.carried_shipments):
        try:
            current_booking = shipment.get_current_booking()
        except ValueError:
            continue

        final_port = get_final_booking_port(shipment)
        if final_port is None or final_port is current_port:
            continue
        if final_port.name.casefold() in snapshot.active_closed_ports:
            continue

        new_path = find_cached_min_cost_path(
            context, current_port, final_port, snapshot, params
        )
        if not new_path:
            continue

        old_cost = estimate_remaining_booking_cost(shipment, current_segment, snapshot, params)
        new_cost = estimate_path_cost(new_path, params)
        impacted = remaining_plan_disruption_penalty(
            shipment, current_segment, snapshot, params
        ) >= params.replan_disruption_trigger_days
        improves_enough = new_cost + params.replan_min_saving_days < old_cost
        if not impacted and not improves_enough:
            continue

        replace_unfinished_bookings_from_current_port(
            shipment,
            current_booking,
            current_segment,
            new_path,
        )

    return True


def estimate_path_cost(path, params):
    if not path:
        return 0.0
    total = 0.0
    previous_route = None
    for edge in path:
        total += edge.expected_cost_days
        if previous_route is not None and previous_route is not edge.service_route:
            total += params.transshipment_penalty_days
        previous_route = edge.service_route
    return total


def estimate_remaining_booking_cost(shipment, current_segment, snapshot, params):
    total = 0.0
    current_index = shipment.current_booking_index
    previous_route = None
    for booking in sorted(shipment.associated_bookings, key=lambda item: item.sequence_index):
        if booking.sequence_index < current_index:
            continue
        cost, route = _booking_cost_from_current_position(
            booking, current_segment, snapshot, params
        )
        if previous_route is not None and previous_route is not route:
            total += params.transshipment_penalty_days
        total += cost
        previous_route = route
    return total


def remaining_plan_disruption_penalty(shipment, current_segment, snapshot, params):
    total = 0.0
    current_index = shipment.current_booking_index
    for booking in sorted(shipment.associated_bookings, key=lambda item: item.sequence_index):
        if booking.sequence_index < current_index:
            continue
        cost, _ = _booking_cost_from_current_position(
            booking, current_segment, snapshot, params, disruption_only=True
        )
        total += cost
    return total


def replace_unfinished_bookings_from_current_port(
    shipment, current_booking, current_segment, path
):
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

    next_sequence = current_booking.sequence_index + 1
    new_bookings = []
    first_edge = path[0]
    if first_edge.service_route is completed_booking.service_route:
        completed_booking.arrival_segment_index = first_edge.arrival_segment_index
        remaining_edges = path[1:]
    else:
        remaining_edges = path

    for edge in remaining_edges:
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
    remove_bookings_from_service_routes(replaced_bookings)
    for booking in new_bookings:
        booking.service_route.associated_bookings.append(booking)
    shipment.associated_bookings.clear()
    shipment.associated_bookings.extend(retained_bookings + new_bookings)
    shipment.current_booking_index = completed_booking.sequence_index


def remove_bookings_from_service_routes(bookings):
    for booking in bookings:
        service_route = booking.service_route
        if service_route is None:
            continue
        while booking in service_route.associated_bookings:
            service_route.associated_bookings.remove(booking)


def get_final_booking_port(shipment):
    last_booking = max(
        shipment.associated_bookings,
        key=lambda booking: booking.sequence_index,
        default=None,
    )
    if last_booking is None or last_booking.service_route is None:
        return None
    segment = _segment_by_sequence(
        last_booking.service_route, last_booking.arrival_segment_index
    )
    return segment.associated_leg.arrival_port if segment is not None else None


def route_pressure(route, context, params=None):
    return _route_pressure(route, context, params)


def port_pressure_days(port, context, params):
    return _port_pressure_days(port, context, params)


def _booking_cost_from_current_position(
    booking, current_segment, snapshot, params, disruption_only=False
):
    route = booking.service_route
    segments = sorted(route.segments, key=lambda segment: segment.sequence_index)
    if not segments:
        return 0.0, route

    if booking.sequence_index == getattr(booking.shipment, "current_booking_index", None):
        current_position = _segment_list_index(segments, current_segment.sequence_index)
        arrival_position = _segment_list_index(segments, booking.arrival_segment_index)
        if current_position < 0 or arrival_position < 0 or current_position == arrival_position:
            return 0.0, route
        start_position = (current_position + 1) % len(segments)
    else:
        start_position = _segment_list_index(segments, booking.departure_segment_index)
        arrival_position = _segment_list_index(segments, booking.arrival_segment_index)
        if start_position < 0 or arrival_position < 0:
            return 0.0, route

    sailing_days = 0.0
    disruption_penalty = 0.0
    position = start_position
    while True:
        segment = segments[position]
        leg_days, leg_penalty = _leg_expected_days(
            segment.associated_leg, route, snapshot, params
        )
        sailing_days += leg_days
        disruption_penalty += leg_penalty
        if position == arrival_position:
            break
        position = (position + 1) % len(segments)

    if disruption_only:
        return disruption_penalty, route
    return params.expected_route_wait_days + sailing_days + disruption_penalty, route


def _leg_expected_days(leg, route, snapshot, params):
    speed = _route_average_speed(route)
    sailing_days = 0.0
    if speed > 0:
        sailing_days = leg.sailing_distance / speed / 24.0

    multiplier = max(
        float(getattr(leg, "sailing_time_multiplier", 1.0) or 1.0),
        float(snapshot.congested_leg_multipliers.get(leg, 1.0)),
    )
    penalty = 0.0
    if multiplier > 1.0:
        if multiplier >= params.hard_congestion_multiplier:
            penalty += params.congestion_risk_penalty_days * 2.0
        else:
            penalty += params.congestion_risk_penalty_days * (multiplier - 1.0)
    return sailing_days * multiplier, penalty


def _route_average_speed(route):
    speeds = [
        vessel.vessel_class.sailing_speed
        for vessel in getattr(route, "deployed_vessels", [])
        if vessel.vessel_class is not None and vessel.vessel_class.sailing_speed > 0
    ]
    if speeds:
        return sum(speeds) / len(speeds)
    source_route = getattr(route, "source_service_route", None)
    if source_route is not None:
        return _route_average_speed(source_route)
    return 18.0


def _route_has_service(route, context):
    return any(
        vessel.assigned_service_route is route
        or vessel.pending_assigned_service_route is route
        for vessel in getattr(context, "vessels", [])
    )


def _route_pressure(route, context, params=None):
    active_teu = 0.0
    bookings = getattr(route, "associated_bookings", [])
    sample_size = int(getattr(params, "route_pressure_sample_size", 240.0))
    if sample_size > 0 and len(bookings) > sample_size:
        bookings = bookings[-sample_size:]

    for booking in bookings:
        shipment = getattr(booking, "shipment", None)
        if shipment is None or shipment.completion_time is not None:
            continue
        active_teu += getattr(shipment, "teu_size", 0) or 0

    capacities = [
        vessel.vessel_class.teu_capacity
        for vessel in getattr(context, "vessels", [])
        if (
            vessel.assigned_service_route is route
            or vessel.pending_assigned_service_route is route
        )
        and vessel.vessel_class is not None
        and vessel.vessel_class.teu_capacity > 0
    ]
    if not capacities:
        source_route = getattr(route, "source_service_route", None)
        if source_route is not None:
            return _route_pressure(source_route, context, params)
        return 1.0 if active_teu > 0 else 0.0

    weekly_capacity = sum(capacities) / max(1, len(capacities))
    return min(3.0, active_teu / max(1.0, weekly_capacity))


def _port_pressure_days(port, context, params):
    stored_shipments = getattr(port, "shipments_in_storage", [])
    sample_size = int(getattr(params, "port_storage_sample_size", 600.0))
    if sample_size > 0 and len(stored_shipments) > sample_size:
        stored_shipments = stored_shipments[-sample_size:]

    storage_teu = sum(
        getattr(shipment, "teu_size", 0) or 0
        for shipment in stored_shipments
        if getattr(shipment, "completion_time", None) is None
    )
    incoming_vessels = sum(
        1
        for vessel in getattr(context, "vessels", [])
        if vessel.current_segment is not None
        and vessel.current_segment.associated_leg is not None
        and vessel.current_segment.associated_leg.arrival_port is port
    )
    berth_count = max(1, len(getattr(port, "berths", [])))
    vessel_pressure = max(0, incoming_vessels - berth_count)
    return (
        storage_teu / 1000.0 * params.port_storage_penalty_days_per_1000_teu
        + vessel_pressure * params.port_arrival_vessel_penalty_days
    )


def _segment_by_sequence(route, sequence_index):
    return next(
        (
            segment
            for segment in route.segments
            if segment.sequence_index == sequence_index
        ),
        None,
    )


def _segment_list_index(segments, sequence_index):
    return next(
        (
            index
            for index, segment in enumerate(segments)
            if segment.sequence_index == sequence_index
        ),
        -1,
    )


def _simulation_day(now):
    return (now - dt.datetime.min).total_seconds() / 86400.0
