"""Continuous, resumable optimizer for the WSC 2026 E10 strategy."""

import datetime as dt
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Project Root
PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "Output"
LOGS_DIR = PROJECT_ROOT / "Logs"
MEJORAS_OUTPUT_DIR = OUTPUT_DIR / "Mejoras"
MEJORAS_LOGS_DIR = LOGS_DIR / "Mejoras"
TUNING_DIR = OUTPUT_DIR / "Tuning"


def get_python_executable() -> str:
    """Find virtual environment python executable or fall back to sys.executable."""
    venv_py = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    win_venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if win_venv_py.exists():
        return str(win_venv_py)
    return sys.executable


def find_att_csv(output_dir: Path = OUTPUT_DIR) -> Path | None:
    """Find ATT_By_Statistics_Interval.csv in Output root or subdirectories."""
    direct = output_dir / "ATT_By_Statistics_Interval.csv"
    if direct.exists():
        return direct
    matches = list(output_dir.glob("**/ATT_By_Statistics_Interval.csv"))
    if matches:
        return sorted(matches, key=os.path.getmtime, reverse=True)[0]
    return None


def parse_att_csv(csv_path: Path | None = None) -> tuple[float, float]:
    """Parse ATT_By_Statistics_Interval.csv and return (mean_att_days, total_completed_teus)."""
    if csv_path is None or not csv_path.exists():
        csv_path = find_att_csv()

    if csv_path is None or not csv_path.exists():
        return float("inf"), 0.0

    att_values = []
    overall_mean_found = None
    total_teus = 0.0

    try:
        with csv_path.open("r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        if len(lines) <= 1:
            return float("inf"), 0.0

        for line in lines[1:]:
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 4:
                # Check for OverallMean summary row
                if parts[2] == "OverallMean":
                    try:
                        overall_mean_found = float(parts[3])
                    except ValueError:
                        pass
                    continue
                if parts[2] == "PeriodStdDev":
                    continue

                # Normal period row: PeriodIndex, StartDay, EndDay, AverageTransportTime
                try:
                    p_idx = int(parts[0])
                    att_val = float(parts[3])
                    if att_val > 0:
                        att_values.append((p_idx, att_val))
                except ValueError:
                    pass

        # Exclude last interval if it's interval 72 or partial
        if att_values:
            if len(att_values) > 1 and att_values[-1][0] == 72:
                valid_atts = [val for p_idx, val in att_values[:-1]]
            else:
                valid_atts = [val for p_idx, val in att_values]

            if valid_atts:
                calculated_mean = sum(valid_atts) / len(valid_atts)
                return calculated_mean, total_teus

        if overall_mean_found is not None:
            return overall_mean_found, total_teus

        return float("inf"), 0.0

    except Exception as err:
        print(f"[AutoTune Error] Failed reading CSV {csv_path}: {err}")
        return float("inf"), 0.0


def run_simulation(config: dict, run_index: int, phase: str) -> tuple[float, float, float]:
    """Run main.py in a subprocess with the given configuration environment variables."""
    env = os.environ.copy()
    env["SIM_EXPERIMENT"] = str(config.get("EXPERIMENT", "CUSTOM"))
    env["SIM_MIN_REROUTE_SAVING_HOURS"] = str(config.get("MIN_REROUTE_SAVING_HOURS", 24.0))
    env["SIM_ENABLE_INITIAL_WAIT"] = str(config.get("ENABLE_INITIAL_WAIT", False))
    env["SIM_ENABLE_TRANSFER_COST"] = str(config.get("ENABLE_TRANSFER_COST", False))
    env["SIM_ENABLE_DYNAMIC_REROUTING"] = str(config.get("ENABLE_DYNAMIC_REROUTING", False))
    env["SIM_INITIAL_WAIT_WEIGHT"] = str(config.get("INITIAL_WAIT_WEIGHT", 1.0))
    env["SIM_TRANSFER_WAIT_WEIGHT"] = str(config.get("TRANSFER_WAIT_WEIGHT", 1.0))
    env["SIM_ENABLE_ALTERNATIVE_ROUTES"] = str(config.get("ENABLE_ALTERNATIVE_ROUTES", False))
    env["SIM_ENABLE_STRATEGY"] = str(config.get("ENABLE_STRATEGY", True))
    env["SIM_E10_MIN_EFFECTIVE_SAVING_RATIO"] = str(config.get("E10_MIN_EFFECTIVE_SAVING_RATIO", 0.02))
    env["SIM_E10_MAX_EXTRA_TRANSSHIPMENTS"] = str(config.get("E10_MAX_EXTRA_TRANSSHIPMENTS", 1))
    env["SIM_E10_QCR_ALLOWED_INCREASE"] = str(config.get("E10_QCR_ALLOWED_INCREASE", 0.25))
    env["SIM_E10_QCR_HIGH_PRESSURE"] = str(config.get("E10_QCR_HIGH_PRESSURE", 1.0))
    env["SIM_DISABLE_DASHBOARD"] = "True"

    run_directory = TUNING_DIR / f"{run_index:03d}_{phase}"
    output_directory = run_directory / "Output"
    logs_directory = run_directory / "Logs"
    output_directory.mkdir(parents=True, exist_ok=True)
    logs_directory.mkdir(parents=True, exist_ok=True)
    env["SIM_OUTPUT_DIR"] = str(output_directory)
    env["SIM_LOGS_DIR"] = str(logs_directory)

    python_exe = get_python_executable()
    cmd = [python_exe, str(PROJECT_ROOT / "main.py")]

    start_time = time.time()
    proc = subprocess.run(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    elapsed_sec = time.time() - start_time

    if proc.returncode != 0:
        print(f"\n[ERROR] main.py falló con código de salida {proc.returncode}:")
        print(proc.stdout[:1500] if proc.stdout else "No output.")
        return float("inf"), 0.0, elapsed_sec

    csv_file = find_att_csv(output_directory)
    mean_att, completed_teus = parse_att_csv(csv_file)

    return mean_att, completed_teus, elapsed_sec


def record_run(config: dict, mean_att: float, teus: float, duration: float, run_index: int, phase: str) -> None:
    """Persist every trial so interrupted campaigns never repeat work blindly."""
    TUNING_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": f"{dt.datetime.now():%Y-%m-%dT%H:%M:%S}",
        "run_index": run_index,
        "phase": phase,
        "mean_att_days": mean_att,
        "completed_teus": teus,
        "duration_seconds": duration,
        "configuration": config,
    }
    with (TUNING_DIR / "optimization_history.jsonl").open("a", encoding="utf-8") as history:
        history.write(json.dumps(record, sort_keys=True) + "\n")


def archive_improvement(
    config: dict,
    mean_att: float,
    teus: float,
    run_index: int,
    source_output_dir: Path | None = None,
):
    """Archive output CSVs and log file ONLY when a new record is achieved."""
    MEJORAS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MEJORAS_LOGS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = f"{dt.datetime.now():%Y%m%d_%H%M%S}"
    run_folder_name = f"Mejora_Run{run_index:03d}_ATT_{mean_att:.2f}dias_{timestamp}"
    dest_output = MEJORAS_OUTPUT_DIR / run_folder_name
    dest_output.mkdir(parents=True, exist_ok=True)

    # Copy CSV outputs
    source_output_dir = source_output_dir or OUTPUT_DIR
    if source_output_dir.exists():
        for csv_path in source_output_dir.glob("*.csv"):
            shutil.copy2(csv_path, dest_output / csv_path.name)

    # Copy latest log file
    source_logs_dir = source_output_dir.parent / "Logs" if source_output_dir != OUTPUT_DIR else LOGS_DIR
    log_files = sorted(source_logs_dir.glob("*.log"), key=os.path.getmtime, reverse=True)
    if log_files:
        latest_log = log_files[0]
        shutil.copy2(latest_log, MEJORAS_LOGS_DIR / f"{run_folder_name}.log")

    # Save configuration JSON
    record_info = {
        "timestamp": timestamp,
        "run_index": run_index,
        "mean_att_days": mean_att,
        "completed_teus": teus,
        "configuration": config,
    }

    with (dest_output / "config_record.json").open("w", encoding="utf-8") as f:
        json.dump(record_info, f, indent=2)

    with (MEJORAS_OUTPUT_DIR / "best_configuration.json").open("w", encoding="utf-8") as f:
        json.dump(record_info, f, indent=2)

    # Append to history log
    history_file = MEJORAS_OUTPUT_DIR / "optimization_history.jsonl"
    with history_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record_info) + "\n")

    print(f"\n[RÉCORD GUARDADO] Nueva mejora archivada en: {dest_output}")


_E10_DEFAULTS = {
    "EXPERIMENT": "E10_CHALLENGER",
    "ENABLE_STRATEGY": True,
    "E10_MIN_EFFECTIVE_SAVING_RATIO": 0.02,
    "E10_MAX_EXTRA_TRANSSHIPMENTS": 1,
    "E10_QCR_ALLOWED_INCREASE": 0.25,
    "E10_QCR_HIGH_PRESSURE": 1.00,
}


def _canonicalize_config(config: dict) -> dict:
    """Make omitted E10 defaults explicit for stable history deduplication."""
    if (
        config.get("EXPERIMENT") == "E10_CHALLENGER"
        and config.get("ENABLE_STRATEGY", True)
    ):
        return {**_E10_DEFAULTS, **config}
    return dict(config)


def _configuration_key(config: dict) -> str:
    return json.dumps(_canonicalize_config(config), sort_keys=True, separators=(",", ":"))


def _load_history() -> list[dict]:
    history = TUNING_DIR / "optimization_history.jsonl"
    if not history.exists():
        return []
    records = []
    for line in history.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            record["configuration"] = _canonicalize_config(record["configuration"])
            records.append(record)
        except (KeyError, json.JSONDecodeError):
            continue
    return records


def _load_historical_best() -> tuple[float, str]:
    """Return the best ATT available in prior Output experiments.

    Older experiment folders do not always preserve a complete parameter set,
    so they are a global benchmark rather than mutation parents for E10.
    """
    best_att = math.inf
    best_label = "sin resultados"
    for csv_path in OUTPUT_DIR.glob("*/ATT_By_Statistics_Interval.csv"):
        if csv_path.parent.name in {"Tuning", "Mejoras"}:
            continue
        att, _ = parse_att_csv(csv_path)
        if att < best_att:
            best_att = att
            best_label = csv_path.parent.name
    return best_att, best_label


def _is_finite_result(record: dict) -> bool:
    return math.isfinite(float(record.get("mean_att_days", math.inf)))


def _is_e10_result(record: dict) -> bool:
    config = record["configuration"]
    return config.get("EXPERIMENT") == "E10_CHALLENGER" and config.get(
        "ENABLE_STRATEGY", True
    )


def _adaptive_candidate(records: list[dict], seen: set[str]) -> tuple[str, dict] | None:
    """Propose an untested local or distant E10 mutation from saved results."""
    results = [record for record in records if _is_e10_result(record) and _is_finite_result(record)]
    if not results:
        return None

    ranked = sorted(results, key=lambda record: record["mean_att_days"])
    best = ranked[0]
    recent = results[-4:]
    earlier = results[:-4]
    earlier_best = min(
        (record["mean_att_days"] for record in earlier), default=math.inf
    )
    recent_improvement = min(
        (record["mean_att_days"] for record in recent), default=math.inf
    ) < earlier_best - 1e-9
    # Stagnation expands the local search and raises the chance of a distant jump.
    scale = 0.6 if recent_improvement else 1.8
    rng = random.Random(20260813 + len(records))
    stagnant = not recent_improvement and len(recent) == 4
    distant_search = rng.random() < (0.35 if stagnant else 0.20)

    parameter_names = {
        "saving": "E10_MIN_EFFECTIVE_SAVING_RATIO",
        "transfers": "E10_MAX_EXTRA_TRANSSHIPMENTS",
    }
    mutable_dimensions = []
    for dimension, parameter_name in parameter_names.items():
        observed_values = {
            record["configuration"][parameter_name] for record in results
        }
        observed_scores = {
            round(record["mean_att_days"], 9)
            for record in results
            if parameter_name in record["configuration"]
        }
        # Do not keep spending runs on a parameter that has already varied but
        # produced exactly the same ATT in every observed E10 result.
        if len(observed_values) > 1 and len(observed_scores) == 1:
            continue
        mutable_dimensions.append(dimension)
    if not mutable_dimensions:
        mutable_dimensions = list(parameter_names)

    for _ in range(120):
        if distant_search:
            candidate = _canonicalize_config(best["configuration"])
            candidate["E10_MIN_EFFECTIVE_SAVING_RATIO"] = round(
                rng.uniform(0.0, 0.30), 3
            )
            candidate["E10_MAX_EXTRA_TRANSSHIPMENTS"] = rng.randint(0, 3)
            dimensions = ()
        else:
            parent_pool = ranked[: min(5, len(ranked))]
            parent = rng.choice(parent_pool)
            candidate = _canonicalize_config(parent["configuration"])
            dimensions = rng.sample(
                mutable_dimensions,
                k=min(
                    len(mutable_dimensions),
                    1 if len(results) < 6 else rng.choice((1, 2)),
                ),
            )
        for dimension in dimensions:
            if dimension == "saving":
                delta = rng.uniform(-0.05, 0.05) * scale
                candidate["E10_MIN_EFFECTIVE_SAVING_RATIO"] = round(
                    min(0.30, max(0.0, candidate["E10_MIN_EFFECTIVE_SAVING_RATIO"] + delta)),
                    3,
                )
            elif dimension == "transfers":
                delta = rng.choice((-1, 1))
                candidate["E10_MAX_EXTRA_TRANSSHIPMENTS"] = min(
                    3,
                    max(0, candidate["E10_MAX_EXTRA_TRANSSHIPMENTS"] + delta),
                )
        if _configuration_key(candidate) not in seen:
            mode = "distante" if distant_search else "local"
            return mode, candidate
    return None


def main():
    """Run until Ctrl+C, resuming from the persisted optimization history."""
    print("WSC 2026: optimizador adaptativo continuo E10")
    print("Cada corrida usa Output/Tuning/<run>/ y no inicia el dashboard.")
    print("Detenga la búsqueda de forma segura con Ctrl+C.")
    records = _load_history()
    seen = {_configuration_key(record["configuration"]) for record in records}
    run_index = max((int(record.get("run_index", 0)) for record in records), default=0)
    historical_best_att, historical_best_label = _load_historical_best()
    recorded_best = min(
        (record["mean_att_days"] for record in records if _is_finite_result(record)),
        default=math.inf,
    )
    global_best_att = min(historical_best_att, recorded_best)
    print(
        f"Mejor referencia acumulada: {global_best_att:.4f} días "
        f"({historical_best_label})."
    )

    default_control = {"EXPERIMENT": "E10_CHALLENGER", "ENABLE_STRATEGY": False}
    e10_normal = {"EXPERIMENT": "E10_CHALLENGER", "ENABLE_STRATEGY": True}
    # Segunda prueba: E10 con los parámetros que explora la automatización.
    # No corresponde a una persona ni altera los experimentos E1--E7.
    e10_adjusted = {
        **e10_normal,
        "E10_MIN_EFFECTIVE_SAVING_RATIO": 0.00,
        "E10_QCR_ALLOWED_INCREASE": 0.50,
        "E10_QCR_HIGH_PRESSURE": 1.25,
    }
    seed_candidates = [
        ("C0_default", default_control),
        ("C1_e10_normal", e10_normal),
        ("C2_e10_parametros_ajustados", e10_adjusted),
    ]
    try:
        while True:
            next_item = next(
                (
                    (name, _canonicalize_config(config))
                    for name, config in seed_candidates
                    if _configuration_key(config) not in seen
                ),
                None,
            )
            if next_item is None:
                proposed = _adaptive_candidate(records, seen)
                if proposed is None:
                    print("No se pudo proponer una combinación E10 no evaluada.")
                    break
                mode, candidate = proposed
                next_item = (f"A{run_index + 1:06d}_{mode}", candidate)

            name, config = next_item
            run_index += 1
            print(f"\n[Run #{run_index}] {name}: {config}")
            att, teus, duration = run_simulation(config, run_index, name)
            record_run(config, att, teus, duration, run_index, name)
            record = {
                "run_index": run_index,
                "phase": name,
                "configuration": _canonicalize_config(config),
                "mean_att_days": att,
                "completed_teus": teus,
                "duration_seconds": duration,
            }
            records.append(record)
            seen.add(_configuration_key(config))
            print(
                f"  ATT: {att:.4f} días | TEUs: {teus:,.0f} | "
                f"Duración: {duration / 60:.2f} min"
            )
            if math.isfinite(att) and att < global_best_att:
                global_best_att = att
                archive_improvement(
                    config,
                    att,
                    teus,
                    run_index,
                    TUNING_DIR / f"{run_index:03d}_{name}" / "Output",
                )
                print(f"  Nuevo mejor ATT global: {global_best_att:.4f} días")
    except KeyboardInterrupt:
        best_e10 = min(
            (record for record in records if _is_e10_result(record) and _is_finite_result(record)),
            key=lambda record: record["mean_att_days"],
            default=None,
        )
        print("\nBúsqueda detenida. El historial quedó guardado.")
        if best_e10 is not None:
            print(
                f"Mejor E10 reanudable: {best_e10['mean_att_days']:.4f} días "
                f"({best_e10['phase']})."
            )


if __name__ == "__main__":
    main()
