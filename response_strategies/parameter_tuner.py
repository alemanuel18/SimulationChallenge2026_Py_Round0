"""Resumeable binary-feature tuner for response strategy experiments.

The script is intentionally self-contained and only uses the standard library.
It launches each simulation in a fresh Python process so the strategy parameter
cache is rebuilt for every candidate.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys
import time


FEATURES = (
    "enable_custom_berth_priority",
    "enable_schedule_aware_booking",
    "enable_hub_transfer_consolidation",
    "enable_initial_time_routing",
    "enable_controlled_alternative_routes",
    "enable_in_transit_replanning",
    "suppress_kaohsiung_s2_detour",
)

DEFAULT_FEATURE_VALUES = {
    "enable_custom_berth_priority": 1.0,
    "enable_schedule_aware_booking": 1.0,
    "enable_hub_transfer_consolidation": 1.0,
    "enable_initial_time_routing": 0.0,
    "enable_controlled_alternative_routes": 0.0,
    "enable_in_transit_replanning": 0.0,
    "suppress_kaohsiung_s2_detour": 0.0,
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
O2DESPY_ROOT = PROJECT_ROOT / "o2despy"
OUTPUT_DIR = PROJECT_ROOT / "Output"
RUNS_DIR = Path(__file__).resolve().parent / "tuning_runs" / "binary_feature_search"
TRIALS_CSV = RUNS_DIR / "trials.csv"
BEST_JSON = RUNS_DIR / "best.json"


def main() -> int:
    args = parse_args()
    random_source = random.Random(args.seed)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    completed = load_completed_candidate_keys(TRIALS_CSV)
    print(f"Tuning directory: {RUNS_DIR}")
    print(f"Completed candidates loaded: {len(completed)}")
    print("Stop with Ctrl+C. The current trial will be marked as interrupted.")
    print()

    trial_number = next_trial_number(TRIALS_CSV)
    candidate_stream = iter_candidates(
        max_focused_flips=args.max_focused_flips,
        explore_remaining=not args.focused_only,
        random_source=random_source,
    )

    try:
        for candidate in candidate_stream:
            key = candidate_key(candidate)
            if key in completed:
                continue

            trial_number += 1
            result = run_trial(trial_number, key, candidate)
            append_trial(TRIALS_CSV, result)
            completed.add(key)
            update_best(result)
            print_result(result)

        print("No quedan combinaciones binarias nuevas para probar.")
        if args.idle_when_exhausted:
            print("Esperando indefinidamente hasta Ctrl+C.")
            while True:
                time.sleep(60)
        return 0
    except KeyboardInterrupt:
        print()
        print("Tuner detenido por Ctrl+C.")
        return 130


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run response-strategy binary feature experiments, saving one "
            "log/output folder per trial and resuming from trials.csv."
        )
    )
    parser.add_argument(
        "--max-focused-flips",
        type=int,
        default=2,
        help=(
            "Focused stage size: test combinations that flip at most this many "
            "features from the current default. Default: 2."
        ),
    )
    parser.add_argument(
        "--focused-only",
        action="store_true",
        help=(
            "Only run the reduced focused set. Without this flag, the tuner "
            "continues with the remaining binary combinations after that set."
        ),
    )
    parser.add_argument(
        "--idle-when-exhausted",
        action="store_true",
        help=(
            "After every unique candidate has been tested, keep the process "
            "alive until Ctrl+C instead of exiting."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260821,
        help="Shuffle seed for the post-focused exploration order.",
    )
    return parser.parse_args()


def iter_candidates(max_focused_flips: int, explore_remaining: bool, random_source):
    seen = set()

    focused = []
    for flip_count in range(0, max(0, max_focused_flips) + 1):
        for names in itertools.combinations(FEATURES, flip_count):
            candidate = dict(DEFAULT_FEATURE_VALUES)
            for name in names:
                candidate[name] = 0.0 if candidate[name] >= 0.5 else 1.0
            focused.append(candidate)

    for candidate in focused:
        key = candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        yield candidate

    if not explore_remaining:
        return

    remaining = []
    for bits in itertools.product((0.0, 1.0), repeat=len(FEATURES)):
        candidate = dict(zip(FEATURES, bits))
        key = candidate_key(candidate)
        if key not in seen:
            remaining.append(candidate)
            seen.add(key)

    random_source.shuffle(remaining)
    for candidate in remaining:
        yield candidate


def run_trial(trial_number: int, key: str, candidate: dict[str, float]) -> dict:
    label = f"trial_{trial_number:05d}_{key[:10]}"
    trial_dir = RUNS_DIR / label
    trial_dir.mkdir(parents=True, exist_ok=True)
    output_copy_dir = trial_dir / "Output"
    stdout_path = trial_dir / "stdout.log"
    stderr_path = trial_dir / "stderr.log"
    env_path = trial_dir / "env.json"
    result_path = trial_dir / "result.json"

    env = os.environ.copy()
    env["PYTHONPATH"] = build_pythonpath(env.get("PYTHONPATH", ""))
    for name, value in candidate.items():
        env[f"WSC_STRATEGY_{name.upper()}"] = str(value)

    env_payload = {
        "candidate_key": key,
        "features": candidate,
        "command": [sys.executable, "-c", "import main; main.run_simulation()"],
        "project_root": str(PROJECT_ROOT),
        "started_at": now_iso(),
    }
    write_json(env_path, env_payload)

    started = time.perf_counter()
    status = "failed"
    return_code = None
    overall_mean = None
    period_stddev = None
    peak_period_att = None
    high_period_count = None
    error = ""

    print(f"[{label}] starting {format_candidate(candidate)}")
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        process = subprocess.Popen(
            [sys.executable, "-c", "import main; main.run_simulation()"],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=stdout,
            stderr=stderr,
            text=True,
            start_new_session=sys.platform != "win32",
        )
        try:
            return_code = wait_for_process(process)
        except KeyboardInterrupt:
            status = "interrupted"
            terminate_process(process)
            copy_output_directory(output_copy_dir)
            elapsed = time.perf_counter() - started
            result = build_result(
                trial_number,
                key,
                trial_dir,
                candidate,
                status,
                return_code,
                elapsed,
                overall_mean,
                period_stddev,
                peak_period_att,
                high_period_count,
                "Interrupted by Ctrl+C.",
            )
            write_json(result_path, result)
            append_trial(TRIALS_CSV, result)
            raise

    copy_output_directory(output_copy_dir)
    elapsed = time.perf_counter() - started

    if return_code == 0:
        try:
            metrics = read_att_metrics(output_copy_dir / "ATT_By_Statistics_Interval.csv")
            overall_mean = metrics["overall_mean"]
            period_stddev = metrics["period_stddev"]
            peak_period_att = metrics["peak_period_att"]
            high_period_count = metrics["high_period_count"]
            status = "success"
        except (OSError, ValueError) as exc:
            error = f"Could not read ATT metrics: {exc}"
    else:
        error = f"Simulation exited with code {return_code}."

    result = build_result(
        trial_number,
        key,
        trial_dir,
        candidate,
        status,
        return_code,
        elapsed,
        overall_mean,
        period_stddev,
        peak_period_att,
        high_period_count,
        error,
    )
    write_json(result_path, result)
    return result


def wait_for_process(process: subprocess.Popen) -> int:
    while True:
        return_code = process.poll()
        if return_code is not None:
            return return_code
        time.sleep(5)


def terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        process.terminate()
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def copy_output_directory(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    if OUTPUT_DIR.exists():
        shutil.copytree(OUTPUT_DIR, destination)
    else:
        destination.mkdir(parents=True, exist_ok=True)


def build_pythonpath(existing: str) -> str:
    paths = [str(PROJECT_ROOT), str(O2DESPY_ROOT)]
    if existing:
        paths.append(existing)
    return os.pathsep.join(paths)


def read_att_metrics(path: Path) -> dict[str, float | int | None]:
    period_values = []
    overall_mean = None
    period_stddev = None

    with path.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            label = row.get("EndDay", "")
            raw_value = row.get("AverageTransportTime", "")
            if not raw_value:
                continue
            value = float(raw_value)
            if label == "OverallMean":
                overall_mean = value
            elif label == "PeriodStdDev":
                period_stddev = value
            else:
                period_values.append(value)

    if overall_mean is None:
        raise ValueError("OverallMean row not found")

    return {
        "overall_mean": overall_mean,
        "period_stddev": period_stddev,
        "peak_period_att": max(period_values) if period_values else None,
        "high_period_count": sum(value >= 21.0 for value in period_values),
    }


def build_result(
    trial_number: int,
    key: str,
    trial_dir: Path,
    candidate: dict[str, float],
    status: str,
    return_code: int | None,
    elapsed_seconds: float,
    overall_mean: float | None,
    period_stddev: float | None,
    peak_period_att: float | None,
    high_period_count: int | None,
    error: str,
) -> dict:
    return {
        "trial": trial_number,
        "candidate_key": key,
        "status": status,
        "return_code": return_code,
        "overall_mean": overall_mean,
        "period_stddev": period_stddev,
        "peak_period_att": peak_period_att,
        "high_period_count": high_period_count,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "trial_dir": str(trial_dir),
        "features": candidate,
        "error": error,
        "finished_at": now_iso(),
    }


def append_trial(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "trial",
        "candidate_key",
        "status",
        "overall_mean",
        "period_stddev",
        "peak_period_att",
        "high_period_count",
        "elapsed_seconds",
        "return_code",
        "trial_dir",
        "error",
        *FEATURES,
    ]
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        row = {name: result.get(name) for name in fieldnames}
        for feature in FEATURES:
            row[feature] = result["features"][feature]
        writer.writerow(row)


def update_best(result: dict) -> None:
    if result["status"] != "success" or result["overall_mean"] is None:
        return
    current = None
    if BEST_JSON.exists():
        try:
            current = json.loads(BEST_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current = None
    if current is None or result["overall_mean"] < current.get("overall_mean", float("inf")):
        write_json(BEST_JSON, result)


def print_result(result: dict) -> None:
    if result["status"] == "success":
        print(
            f"[trial_{result['trial']:05d}] mean={result['overall_mean']:.2f} "
            f"std={result['period_stddev']} peak={result['peak_period_att']} "
            f"high_periods={result['high_period_count']} "
            f"elapsed={format_elapsed(result['elapsed_seconds'])}"
        )
    else:
        print(
            f"[trial_{result['trial']:05d}] {result['status']} "
            f"elapsed={format_elapsed(result['elapsed_seconds'])} "
            f"{result['error']}"
        )


def load_completed_candidate_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    completed = set()
    with path.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            if row.get("status") in {"success", "failed"}:
                completed.add(row["candidate_key"])
    return completed


def next_trial_number(path: Path) -> int:
    if not path.exists():
        return 0
    highest = 0
    with path.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            try:
                highest = max(highest, int(row.get("trial", "0") or 0))
            except ValueError:
                continue
    return highest


def candidate_key(candidate: dict[str, float]) -> str:
    payload = json.dumps(
        {name: candidate[name] for name in FEATURES},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def format_candidate(candidate: dict[str, float]) -> str:
    enabled = [name for name in FEATURES if candidate[name] >= 0.5]
    return "enabled=" + (",".join(enabled) if enabled else "none")


def format_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
