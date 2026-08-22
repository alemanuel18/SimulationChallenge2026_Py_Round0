"""Bayesian optimization tuner for response strategy parameters using Optuna.

Features:
- Warm-starts using historical trials from binary_feature_search/trials.csv.
- Persists state in SQLite so runs can be stopped with Ctrl+C and resumed seamlessly.
- Optimizes continuous weights (berth, storage, congestion, booking) and binary features.
- Uses Optuna's multivariate TPE (Tree-structured Parzen Estimator) for sample efficiency.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
O2DESPY_ROOT = PROJECT_ROOT / "o2despy"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(O2DESPY_ROOT) not in sys.path:
    sys.path.insert(0, str(O2DESPY_ROOT))

import importlib.util

import optuna
from optuna.samplers import TPESampler

# Load StrategyParameters without triggering circular import in package __init__.py
_param_file = Path(__file__).resolve().parent / "strategy_parameters.py"
_spec = importlib.util.spec_from_file_location("strategy_parameters_mod", _param_file)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_mod)
StrategyParameters = _mod.StrategyParameters

OUTPUT_DIR = PROJECT_ROOT / "Output"
TUNING_DIR = Path(__file__).resolve().parent / "tuning_runs" / "bayesian_optuna"
TRIALS_CSV = TUNING_DIR / "trials.csv"
BEST_JSON = TUNING_DIR / "best.json"
SQLITE_DB = TUNING_DIR / "study.db"
LEGACY_TRIALS_CSV = (
    Path(__file__).resolve().parent / "tuning_runs" / "binary_feature_search" / "trials.csv"
)

# Best baseline configuration from Trial 11
BASELINE_TRIAL_11 = {
    "enable_custom_berth_priority": 0.0,
    "enable_schedule_aware_booking": 1.0,
    "enable_hub_transfer_consolidation": 1.0,
    "enable_initial_time_routing": 1.0,
    "enable_controlled_alternative_routes": 0.0,
    "enable_in_transit_replanning": 0.0,
    "suppress_kaohsiung_s2_detour": 0.0,
}

# Best baseline with custom berth priority active from Trial 8
BASELINE_TRIAL_8 = {
    "enable_custom_berth_priority": 1.0,
    "enable_schedule_aware_booking": 1.0,
    "enable_hub_transfer_consolidation": 1.0,
    "enable_initial_time_routing": 0.0,
    "enable_controlled_alternative_routes": 0.0,
    "enable_in_transit_replanning": 0.0,
    "suppress_kaohsiung_s2_detour": 1.0,
}

DEFAULT_PARAMS = {
    field.name: getattr(StrategyParameters(), field.name)
    for field in StrategyParameters.__dataclass_fields__.values()
}


def define_search_space(trial: optuna.Trial, scope: str) -> dict[str, float]:
    """Define parameter search distributions based on selected scope."""
    params = dict(DEFAULT_PARAMS)

    # 1. Structural / Binary Features
    if scope == "all":
        params["enable_custom_berth_priority"] = float(
            trial.suggest_categorical("enable_custom_berth_priority", [0.0, 1.0])
        )
        params["enable_schedule_aware_booking"] = float(
            trial.suggest_categorical("enable_schedule_aware_booking", [0.0, 1.0])
        )
        params["enable_hub_transfer_consolidation"] = float(
            trial.suggest_categorical("enable_hub_transfer_consolidation", [0.0, 1.0])
        )
        params["enable_initial_time_routing"] = float(
            trial.suggest_categorical("enable_initial_time_routing", [0.0, 1.0])
        )
        params["enable_controlled_alternative_routes"] = float(
            trial.suggest_categorical("enable_controlled_alternative_routes", [0.0, 1.0])
        )
        params["enable_in_transit_replanning"] = float(
            trial.suggest_categorical("enable_in_transit_replanning", [0.0, 1.0])
        )
        params["suppress_kaohsiung_s2_detour"] = float(
            trial.suggest_categorical("suppress_kaohsiung_s2_detour", [0.0, 1.0])
        )
    elif scope == "berth-focus":
        params.update(BASELINE_TRIAL_8)
        params["enable_custom_berth_priority"] = 1.0
    else:  # continuous / routing-focus (default: Trial 11 foundation)
        params.update(BASELINE_TRIAL_11)

    # 2. Continuous & Numerical Hyperparameters
    if scope in {"continuous", "all", "routing-focus"}:
        params["expected_route_wait_days"] = trial.suggest_float(
            "expected_route_wait_days", 1.5, 5.5, step=0.25
        )
        params["transshipment_penalty_days"] = trial.suggest_float(
            "transshipment_penalty_days", 0.4, 2.5, step=0.1
        )
        params["port_storage_penalty_days_per_1000_teu"] = trial.suggest_float(
            "port_storage_penalty_days_per_1000_teu", 0.05, 0.45, step=0.01
        )
        params["port_arrival_vessel_penalty_days"] = trial.suggest_float(
            "port_arrival_vessel_penalty_days", 0.15, 1.20, step=0.05
        )
        params["route_pressure_penalty_days"] = trial.suggest_float(
            "route_pressure_penalty_days", 0.8, 4.0, step=0.1
        )
        params["congestion_risk_penalty_days"] = trial.suggest_float(
            "congestion_risk_penalty_days", 1.0, 5.0, step=0.2
        )
        params["closed_port_risk_penalty_days"] = trial.suggest_float(
            "closed_port_risk_penalty_days", 4.0, 16.0, step=0.5
        )
        params["disruption_lookahead_days"] = trial.suggest_float(
            "disruption_lookahead_days", 10.0, 30.0, step=1.0
        )

        # Booking headway parameters
        params["booking_min_headway_days"] = trial.suggest_float(
            "booking_min_headway_days", 1.0, 4.0, step=0.25
        )
        params["booking_max_headway_days"] = trial.suggest_float(
            "booking_max_headway_days", 8.0, 16.0, step=0.5
        )
        params["booking_handling_buffer_days"] = trial.suggest_float(
            "booking_handling_buffer_days", 0.05, 0.75, step=0.05
        )
        params["booking_min_direct_saving_days"] = trial.suggest_float(
            "booking_min_direct_saving_days", 0.25, 2.5, step=0.25
        )
        params["booking_max_direct_extra_sailing_days"] = trial.suggest_float(
            "booking_max_direct_extra_sailing_days", 1.5, 5.5, step=0.25
        )

    # 3. Custom Berth Priority Hyperparameters
    if scope in {"berth-focus", "all"} or params.get("enable_custom_berth_priority", 0.0) >= 0.5:
        params["berth_override_margin"] = trial.suggest_float(
            "berth_override_margin", 0.05, 0.35, step=0.01
        )
        params["berth_min_unloading_teu_advantage"] = trial.suggest_float(
            "berth_min_unloading_teu_advantage", 2.0, 20.0, step=1.0
        )
        params["berth_final_unload_weight"] = trial.suggest_float(
            "berth_final_unload_weight", 0.15, 0.70, step=0.01
        )
        params["berth_transshipment_unload_weight"] = trial.suggest_float(
            "berth_transshipment_unload_weight", 0.05, 0.40, step=0.01
        )
        params["berth_unload_age_weight"] = trial.suggest_float(
            "berth_unload_age_weight", 0.05, 0.40, step=0.01
        )
        params["berth_wait_weight"] = trial.suggest_float(
            "berth_wait_weight", 0.02, 0.25, step=0.01
        )
        params["berth_carried_teu_weight"] = trial.suggest_float(
            "berth_carried_teu_weight", 0.01, 0.15, step=0.01
        )
        params["berth_handling_penalty_weight"] = trial.suggest_float(
            "berth_handling_penalty_weight", 0.02, 0.25, step=0.01
        )

    return params


def main() -> int:
    args = parse_args()
    TUNING_DIR.mkdir(parents=True, exist_ok=True)

    storage_url = f"sqlite:///{SQLITE_DB.resolve()}"
    sampler = TPESampler(seed=args.seed, multivariate=True)

    study = optuna.create_study(
        study_name=args.study_name,
        storage=storage_url,
        direction="minimize",
        sampler=sampler,
        load_if_exists=True,
    )

    print("=" * 70)
    print("🚀 WSC 2026 Response Strategy - Bayesian Optimizer (Optuna)")
    print(f"Directory:    {TUNING_DIR}")
    print(f"Study:        {args.study_name}")
    print(f"Scope:        {args.scope}")
    print(f"Storage:      {storage_url}")
    print("=" * 70)

    # Warm-start from legacy binary search if study has fewer trials
    if args.warm_start and len(study.trials) == 0:
        injected = warm_start_study(study, LEGACY_TRIALS_CSV)
        print(f"✅ Warm-started study with {injected} historical trials from {LEGACY_TRIALS_CSV.name}")

    # Enqueue baseline starting candidates
    enqueue_baselines(study, args.scope)

    if study.best_trial is not None:
        print(f"🏆 Current Best ATT: {study.best_value:.2f} (Trial #{study.best_trial.number})")

    print("\nPress Ctrl+C at any time to pause. Progress is saved in SQLite and trials.csv.\n")

    def objective(trial: optuna.Trial) -> float:
        candidate_params = define_search_space(trial, args.scope)
        key = compute_param_key(candidate_params)
        trial_number = trial.number + 1

        result = execute_trial(trial_number, key, candidate_params)
        append_trial_csv(TRIALS_CSV, result)
        update_best_json(result)
        print_trial_result(result, study.best_value if len(study.trials) > 0 else None)

        if result["status"] == "success" and result["overall_mean"] is not None:
            return float(result["overall_mean"])
        return float("inf")

    try:
        study.optimize(
            objective,
            n_trials=args.n_trials,
            timeout=args.timeout,
            catch=(Exception,),
        )
        print("\n🎉 Optimization complete!")
        if study.best_trial is not None:
            print(f"🏆 Best ATT: {study.best_value:.2f} in Trial #{study.best_trial.number}")
            print(f"Parameters: {json.dumps(study.best_params, indent=2)}")
        return 0
    except KeyboardInterrupt:
        print("\n\n⏹️ Optimizer stopped gracefully by Ctrl+C.")
        if study.best_trial is not None:
            print(f"🏆 Current Best ATT: {study.best_value:.2f} in Trial #{study.best_trial.number}")
        return 130


def execute_trial(trial_number: int, key: str, params: dict[str, float]) -> dict[str, Any]:
    """Run one simulation trial in an isolated subprocess."""
    label = f"trial_{trial_number:05d}_{key[:10]}"
    trial_dir = TUNING_DIR / label
    trial_dir.mkdir(parents=True, exist_ok=True)

    output_copy_dir = trial_dir / "Output"
    stdout_path = trial_dir / "stdout.log"
    stderr_path = trial_dir / "stderr.log"
    params_path = trial_dir / "params.json"
    result_path = trial_dir / "result.json"

    env = os.environ.copy()
    env["PYTHONPATH"] = build_pythonpath(env.get("PYTHONPATH", ""))
    for name, value in params.items():
        env[f"WSC_STRATEGY_{name.upper()}"] = str(value)

    payload = {
        "trial_number": trial_number,
        "candidate_key": key,
        "params": params,
        "command": [sys.executable, "-c", "import main; main.run_simulation()"],
        "project_root": str(PROJECT_ROOT),
        "started_at": now_iso(),
    }
    write_json(params_path, payload)

    started = time.perf_counter()
    status = "failed"
    return_code = None
    overall_mean = None
    period_stddev = None
    peak_period_att = None
    high_period_count = None
    error = ""

    print(f"[{label}] starting simulation with {len(params)} parameters...")
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
            copy_output_dir(output_copy_dir)
            elapsed = time.perf_counter() - started
            result = build_result_dict(
                trial_number,
                key,
                trial_dir,
                params,
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
            append_trial_csv(TRIALS_CSV, result)
            raise

    copy_output_dir(output_copy_dir)
    elapsed = time.perf_counter() - started

    if return_code == 0:
        try:
            metrics = parse_att_metrics(output_copy_dir / "ATT_By_Statistics_Interval.csv")
            overall_mean = metrics["overall_mean"]
            period_stddev = metrics["period_stddev"]
            peak_period_att = metrics["peak_period_att"]
            high_period_count = metrics["high_period_count"]
            status = "success"
        except (OSError, ValueError) as exc:
            error = f"Could not read ATT metrics: {exc}"
    else:
        error = f"Simulation exited with code {return_code}."

    result = build_result_dict(
        trial_number,
        key,
        trial_dir,
        params,
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


def warm_start_study(study: optuna.Study, legacy_csv: Path) -> int:
    """Inject past successful trials into the Optuna study to warm-start TPE."""
    if not legacy_csv.exists():
        return 0

    count = 0
    with legacy_csv.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            if row.get("status") != "success" or not row.get("overall_mean"):
                continue
            try:
                mean_val = float(row["overall_mean"])
            except ValueError:
                continue

            params = dict(DEFAULT_PARAMS)
            # Override binary flags from CSV
            for feature in StrategyParameters.__dataclass_fields__.keys():
                if feature in row and row[feature] != "":
                    try:
                        params[feature] = float(row[feature])
                    except ValueError:
                        pass

            distributions = {}
            for name, val in params.items():
                if name.startswith("enable_") or name.startswith("suppress_"):
                    distributions[name] = optuna.distributions.CategoricalDistribution([0.0, 1.0])
                else:
                    distributions[name] = optuna.distributions.FloatDistribution(
                        min(val * 0.5, val), max(val * 2.0, val + 1.0)
                    )

            try:
                optuna_trial = optuna.trial.create_trial(
                    params=params,
                    distributions=distributions,
                    value=mean_val,
                )
                study.add_trial(optuna_trial)
                count += 1
            except Exception:
                continue

    return count


def enqueue_baselines(study: optuna.Study, scope: str) -> None:
    """Enqueue known strong candidate parameters into Optuna's queue."""
    if scope in {"continuous", "routing-focus"}:
        # Trial 11 baseline with default continuous parameters
        base11 = dict(DEFAULT_PARAMS)
        base11.update(BASELINE_TRIAL_11)
        study.enqueue_trial(base11)
    elif scope == "berth-focus":
        # Trial 8 baseline with custom berth priority
        base8 = dict(DEFAULT_PARAMS)
        base8.update(BASELINE_TRIAL_8)
        study.enqueue_trial(base8)
    elif scope == "all":
        base11 = dict(DEFAULT_PARAMS)
        base11.update(BASELINE_TRIAL_11)
        study.enqueue_trial(base11)
        base8 = dict(DEFAULT_PARAMS)
        base8.update(BASELINE_TRIAL_8)
        study.enqueue_trial(base8)


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


def copy_output_dir(destination: Path) -> None:
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


def parse_att_metrics(path: Path) -> dict[str, float | int | None]:
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


def build_result_dict(
    trial_number: int,
    key: str,
    trial_dir: Path,
    params: dict[str, float],
    status: str,
    return_code: int | None,
    elapsed_seconds: float,
    overall_mean: float | None,
    period_stddev: float | None,
    peak_period_att: float | None,
    high_period_count: int | None,
    error: str,
) -> dict[str, Any]:
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
        "params": params,
        "error": error,
        "finished_at": now_iso(),
    }


def append_trial_csv(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    param_keys = sorted(result["params"].keys())
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
        *param_keys,
    ]
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        row = {name: result.get(name) for name in fieldnames}
        for k, v in result["params"].items():
            row[k] = v
        writer.writerow(row)


def update_best_json(result: dict[str, Any]) -> None:
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


def print_trial_result(result: dict[str, Any], previous_best: float | None) -> None:
    trial_str = f"[trial_{result['trial']:05d}]"
    if result["status"] == "success":
        mean = result["overall_mean"]
        improvement = ""
        if previous_best is not None and mean < previous_best:
            improvement = f" 🔥 NEW BEST! (-{previous_best - mean:.2f})"
        print(
            f"{trial_str} mean={mean:.2f} std={result['period_stddev']} "
            f"peak={result['peak_period_att']} high_periods={result['high_period_count']} "
            f"elapsed={format_elapsed(result['elapsed_seconds'])}{improvement}"
        )
    else:
        print(
            f"{trial_str} {result['status']} "
            f"elapsed={format_elapsed(result['elapsed_seconds'])} "
            f"{result['error']}"
        )


def compute_param_key(params: dict[str, float]) -> str:
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def format_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bayesian parameter optimizer for WSC 2026 Response Strategies using Optuna."
    )
    parser.add_argument(
        "--scope",
        choices=["continuous", "berth-focus", "routing-focus", "all"],
        default="continuous",
        help=(
            "Search scope: 'continuous' (fine-tune weights based on Trial 11 foundation), "
            "'berth-focus' (tune custom berth weights + booking), 'routing-focus' (tune penalties + booking), "
            "or 'all' (tune binary and continuous jointly). Default: continuous."
        ),
    )
    parser.add_argument(
        "--n-trials",
        type=int,
        default=None,
        help="Number of trials to execute (default: run until stopped with Ctrl+C).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Timeout in seconds for the entire optimization run (default: None).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260821,
        help="Random seed for Optuna's TPE sampler.",
    )
    parser.add_argument(
        "--study-name",
        type=str,
        default="wsc_bayesian_strategy_tuning",
        help="Optuna study name in SQLite DB.",
    )
    parser.add_argument(
        "--warm-start",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Warm-start study with historical trials from trials.csv. Default: True.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
