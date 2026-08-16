"""Conservative, schedule-aware optimization of initial shipment bookings."""

from dataclasses import dataclass
import datetime as dt
import math
import statistics

from maritime_data_context import Booking
from response_strategies.default_strategy import DefaultStrategy
from response_strategies.routing_utils import build_disruption_snapshot


_CARGO_PRESSURE_PORTS = frozenset(
    name.casefold() for name in ("Shanghai", "Singapore", "Busan")
)


@dataclass(frozen=True)
class BookingOption:
    service_route: object
    departure_port: object
    arrival_port: object
    departure_segment_index: int
    arrival_segment_index: int
    segments: tuple
    signature: tuple
    sailing_days: float


@dataclass(frozen=True)
class BookingOptionIndex:
    topology_key: tuple
    by_booking_key: dict
    by_od: dict


def record_service_call(context, now, vessel, params) -> None:
    """Record when a route gets berth access for its next departure segment."""
    if params.enable_schedule_aware_booking < 0.5 or vessel is None:
        return

    route = getattr(vessel, "assigned_service_route", None)
    if route is None:
        return
    try:
        next_segment = vessel.get_next_segment()
    except (AttributeError, TypeError, ValueError):
        return
    if next_segment is None:
        return

    histories = getattr(context, "_schedule_aware_call_history", None)
    if histories is None:
        histories = {}
        context._schedule_aware_call_history = histories

    key = (route, next_segment.sequence_index)
    history = histories.setdefault(key, [])
    if history and now <= history[-1]:
        return
    history.append(now)

    history_size = max(2, int(params.booking_call_history_size))
    if len(history) > history_size:
        del history[:-history_size]


def assign_schedule_aware_bookings(context, now, shipment, params) -> bool:
    """Start from the default path, then make only timetable-safe improvements."""
    assigned = DefaultStrategy.assign_associated_bookings(context, now, shipment)
    if not assigned:
        return False
    if len(shipment.associated_bookings) == 0:
        return True

    if len(shipment.associated_bookings) < 2:
        _increment_stat(context, "default_kept")
        return True

    index = _get_booking_option_index(context)
    original_options = _options_for_bookings(index, shipment.associated_bookings)
    if not original_options:
        return True

    snapshot = build_disruption_snapshot(context, now, params)
    direct_options = _find_safe_direct_options(
        context,
        original_options,
        index,
        snapshot,
        params,
    )
    if not direct_options:
        _increment_stat(context, "default_kept")
        return True

    original_projection = _project_chain(context, now, original_options, params)
    direct_option, direct_projection = _best_direct_option(
        context, now, direct_options, params
    )
    if (
        original_projection is None
        or direct_option is None
        or direct_projection
        + dt.timedelta(days=params.booking_min_direct_saving_days)
        >= original_projection
    ):
        _increment_stat(context, "default_kept")
        return True

    removed_transfers = len(original_options) - 1
    _replace_bookings(shipment, [direct_option])
    _increment_stat(context, "optimized_shipments")
    _increment_stat(context, "direct_consolidations")
    _increment_stat(context, "transfers_removed", removed_transfers)
    return True


def _get_booking_option_index(context) -> BookingOptionIndex:
    topology_key = tuple(
        (route, tuple(sorted(route.segments, key=lambda item: item.sequence_index)))
        for route in context.service_routes
    )
    cached = getattr(context, "_schedule_aware_option_index", None)
    if cached is not None and cached.topology_key == topology_key:
        return cached

    by_booking_key = {}
    by_od = {}
    for route in context.service_routes:
        segments = sorted(route.segments, key=lambda item: item.sequence_index)
        segment_count = len(segments)
        if segment_count < 2:
            continue
        for start_position in range(segment_count):
            path_segments = []
            sailing_days = 0.0
            departure_port = segments[start_position].associated_leg.departure_port
            for step in range(1, segment_count):
                position = (start_position + step - 1) % segment_count
                segment = segments[position]
                path_segments.append(segment)
                sailing_days += _segment_sailing_days(segment, route)
                arrival_port = segment.associated_leg.arrival_port
                if arrival_port is departure_port:
                    continue
                option = BookingOption(
                    service_route=route,
                    departure_port=departure_port,
                    arrival_port=arrival_port,
                    departure_segment_index=segments[
                        start_position
                    ].sequence_index,
                    arrival_segment_index=segment.sequence_index,
                    segments=tuple(path_segments),
                    signature=_path_signature(path_segments),
                    sailing_days=sailing_days,
                )
                key = (
                    route,
                    option.departure_segment_index,
                    option.arrival_segment_index,
                )
                by_booking_key[key] = option
                by_od.setdefault((departure_port, arrival_port), []).append(option)

    cached = BookingOptionIndex(
        topology_key=topology_key,
        by_booking_key=by_booking_key,
        by_od=by_od,
    )
    context._schedule_aware_option_index = cached
    return cached


def _options_for_bookings(index, bookings):
    options = []
    for booking in sorted(bookings, key=lambda item: item.sequence_index):
        key = (
            booking.service_route,
            booking.departure_segment_index,
            booking.arrival_segment_index,
        )
        option = index.by_booking_key.get(key)
        if option is None:
            return []
        options.append(option)
    return options


def _find_safe_direct_options(context, original_options, index, snapshot, params):
    if params.enable_hub_transfer_consolidation < 0.5:
        return []
    if len(original_options) < 2:
        return []
    transfer_ports = [option.arrival_port for option in original_options[:-1]]
    if not any(port.name.casefold() in _CARGO_PRESSURE_PORTS for port in transfer_ports):
        return []

    origin_port = original_options[0].departure_port
    destination_port = original_options[-1].arrival_port
    original_sailing_days = sum(option.sailing_days for option in original_options)
    maximum_sailing_days = (
        original_sailing_days + params.booking_max_direct_extra_sailing_days
    )
    return [
        option
        for option in index.by_od.get((origin_port, destination_port), ())
        if option.sailing_days <= maximum_sailing_days
        and _route_has_active_service(context, option.service_route)
        and _option_is_safe(option, snapshot)
    ]


def _best_direct_option(context, now, options, params):
    projected = []
    for option in options:
        pickup_time = _predict_next_call(context, option, now, params)
        if pickup_time is None:
            continue
        arrival_time = _project_arrival(pickup_time, option, params)
        projected.append((arrival_time, option.service_route.id, option))
    if not projected:
        return None, None
    arrival_time, _, option = min(projected, key=lambda item: (item[0], item[1]))
    return option, arrival_time


def _project_chain(context, ready_time, options, params):
    for option in options:
        pickup_time = _predict_next_call(context, option, ready_time, params)
        if pickup_time is None:
            return None
        ready_time = _project_arrival(pickup_time, option, params)
    return ready_time


def _predict_next_call(context, option, ready_time, params):
    histories = getattr(context, "_schedule_aware_call_history", {})
    history = histories.get(
        (option.service_route, option.departure_segment_index), ()
    )
    if not history:
        return None

    intervals = [
        (later - earlier).total_seconds() / 86400.0
        for earlier, later in zip(history, history[1:])
        if later > earlier
    ]
    if intervals:
        interval_days = statistics.median(intervals[-6:])
    else:
        interval_days = params.booking_default_headway_days
    interval_days = min(
        params.booking_max_headway_days,
        max(params.booking_min_headway_days, interval_days),
    )

    next_call = history[-1]
    if next_call < ready_time:
        elapsed_days = (ready_time - next_call).total_seconds() / 86400.0
        periods = max(1, math.ceil(elapsed_days / interval_days - 1e-12))
        next_call += dt.timedelta(days=periods * interval_days)
    return next_call


def _project_arrival(pickup_time, option, params):
    return pickup_time + dt.timedelta(
        days=option.sailing_days + params.booking_handling_buffer_days
    )


def _option_is_safe(option, snapshot):
    blocked_ports = snapshot.active_closed_ports | snapshot.risk_closed_ports
    for segment in option.segments:
        leg = segment.associated_leg
        if leg in snapshot.congested_leg_multipliers:
            return False
        if leg.departure_port.name.casefold() in blocked_ports:
            return False
        if leg.arrival_port.name.casefold() in blocked_ports:
            return False
    return True


def _route_has_active_service(context, route):
    return any(
        vessel.assigned_service_route is route
        for vessel in getattr(context, "vessels", ())
    )


def _segment_sailing_days(segment, route):
    speeds = [
        vessel.vessel_class.sailing_speed
        for vessel in getattr(route, "deployed_vessels", ())
        if vessel.vessel_class is not None and vessel.vessel_class.sailing_speed > 0
    ]
    speed = sum(speeds) / len(speeds) if speeds else 20.0
    return segment.associated_leg.sailing_distance / speed / 24.0


def _path_signature(segments):
    return tuple(
        (
            segment.associated_leg.departure_port,
            segment.associated_leg.arrival_port,
            round(float(segment.associated_leg.sailing_distance), 6),
        )
        for segment in segments
    )


def _replace_bookings(shipment, options):
    for booking in list(shipment.associated_bookings):
        route = booking.service_route
        while booking in route.associated_bookings:
            route.associated_bookings.remove(booking)

    shipment.associated_bookings = []
    for sequence_index, option in enumerate(options, start=1):
        booking = Booking(
            sequence_index=sequence_index,
            shipment=shipment,
            service_route=option.service_route,
            departure_segment_index=option.departure_segment_index,
            arrival_segment_index=option.arrival_segment_index,
        )
        shipment.associated_bookings.append(booking)
        option.service_route.associated_bookings.append(booking)
    shipment.current_booking_index = 1


def _increment_stat(context, name, amount=1):
    stats = getattr(context, "_schedule_aware_booking_stats", None)
    if stats is None:
        stats = {}
        context._schedule_aware_booking_stats = stats
    stats[name] = stats.get(name, 0) + amount
