"""CSV instrumentation for ML-ready simulation decision and outcome data."""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path


DECISION_FIELDS = [
    "run_id", "seed", "timestamp", "measurement_day", "decision_type", "decision_source",
    "entity_id", "port", "route", "origin", "destination", "teu_size", "queue_length",
    "available_berths", "waiting_hours", "vessel_capacity", "onboard_teu",
    "active_disruption_count", "action", "action_details_json",
]
OUTCOME_FIELDS = [
    "run_id", "seed", "shipment_id", "origin", "destination", "teu_size", "generated_time",
    "completion_time", "completed", "transport_time_hours", "final_booking_count",
    "observed_duration_hours", "is_censored",
]


class MLDataLogger:
    def __init__(self, output_directory, run_id: str, seed: int, warm_up_days: int):
        self.output_directory = Path(output_directory) / run_id
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.run_id, self.seed, self.warm_up_days = run_id, seed, warm_up_days
        self.decision_path = self.output_directory / "decisions.csv"
        self.outcome_path = self.output_directory / "shipment_outcomes.csv"
        self._write_header(self.decision_path, DECISION_FIELDS)

    @staticmethod
    def _write_header(path: Path, fields: list[str]) -> None:
        with path.open("w", newline="", encoding="utf-8") as target:
            csv.DictWriter(target, fieldnames=fields).writeheader()

    def log_decision(self, current_time: dt.datetime, **values) -> None:
        row = {field: "" for field in DECISION_FIELDS}
        row.update(values)
        row.update({
            "run_id": self.run_id,
            "seed": self.seed,
            "timestamp": current_time.isoformat(),
            "measurement_day": (current_time - dt.datetime.min).total_seconds() / 86400 - self.warm_up_days,
        })
        details = row.get("action_details_json")
        if details and not isinstance(details, str):
            row["action_details_json"] = json.dumps(details, sort_keys=True, default=str)
        with self.decision_path.open("a", newline="", encoding="utf-8") as target:
            csv.DictWriter(target, fieldnames=DECISION_FIELDS, extrasaction="ignore").writerow(row)

    def write_shipment_outcomes(self, context, end_time: dt.datetime) -> None:
        with self.outcome_path.open("w", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(target, fieldnames=OUTCOME_FIELDS)
            writer.writeheader()
            for demand in context.demands:
                for shipment in demand.shipments:
                    completion = shipment.completion_time
                    duration_end = completion or end_time
                    writer.writerow({
                        "run_id": self.run_id, "seed": self.seed, "shipment_id": shipment.index,
                        "origin": demand.origin_port.name, "destination": demand.destination_port.name,
                        "teu_size": shipment.teu_size, "generated_time": shipment.generated_time.isoformat(),
                        "completion_time": completion.isoformat() if completion else "",
                        "completed": int(completion is not None),
                        "transport_time_hours": (
                            (completion - shipment.generated_time).total_seconds() / 3600
                            if completion else ""
                        ),
                        "observed_duration_hours": (duration_end - shipment.generated_time).total_seconds() / 3600,
                        "is_censored": int(completion is None),
                        "final_booking_count": len(shipment.associated_bookings),
                    })


def active_disruption_count(context, current_time: dt.datetime) -> int:
    count = 0
    for plan in context.disruption_plans:
        if plan.start_offset_days is None or plan.duration_days is None:
            continue
        start = dt.datetime.min + dt.timedelta(days=plan.start_offset_days)
        if start <= current_time < start + dt.timedelta(days=plan.duration_days):
            count += 1
    return count


def booking_signature(shipment) -> list[dict]:
    return [
        {"sequence": b.sequence_index, "route": b.service_route.id,
         "departure_segment": b.departure_segment_index, "arrival_segment": b.arrival_segment_index}
        for b in shipment.associated_bookings
    ]
