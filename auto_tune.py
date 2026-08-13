"""Auto-Tuner for WSC 2026 Maritime Simulation Challenge.

Runs bounded, reproducible E10 comparison campaigns and preserves every trial.
"""

import datetime as dt
import itertools
import json
import math
import os
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


def _configuration_key(config: dict) -> str:
    return json.dumps(config, sort_keys=True, separators=(",", ":"))


def _load_completed_configurations() -> set[str]:
    history = TUNING_DIR / "optimization_history.jsonl"
    if not history.exists():
        return set()
    completed = set()
    for line in history.read_text(encoding="utf-8").splitlines():
        try:
            completed.add(_configuration_key(json.loads(line)["configuration"]))
        except (KeyError, json.JSONDecodeError):
            continue
    return completed


def _run_campaign(candidates: list[tuple[str, dict]], run_index: int, completed: set[str]):
    results = []
    for name, config in candidates:
        key = _configuration_key(config)
        if key in completed:
            print(f"[Omitido] {name}: configuración ya evaluada.")
            continue
        run_index += 1
        print(f"\n[Run #{run_index}] {name}")
        att, teus, duration = run_simulation(config, run_index, name)
        record_run(config, att, teus, duration, run_index, name)
        print(f"  ATT: {att:.4f} días | TEUs: {teus:,.0f} | Duración: {duration / 60:.2f} min")
        if math.isfinite(att):
            results.append((att, teus, config, name, run_index))
    return results, run_index


def main():
    """Run a bounded, reproducible campaign instead of an endless coordinate search."""
    print("WSC 2026: campaña de evaluación E10")
    print("Cada corrida usa Output/Tuning/<run>/ y no inicia el dashboard.")
    completed = _load_completed_configurations()
    run_index = 0

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
    first_pass = [
        ("C0_default", default_control),
        ("C1_e10_normal", e10_normal),
        ("C2_e10_parametros_ajustados", e10_adjusted),
    ]
    results, run_index = _run_campaign(first_pass, run_index, completed)
    e10_results = [result for result in results if result[2].get("ENABLE_STRATEGY")]
    finalists = sorted(e10_results, key=lambda result: result[0])[:1]

    refinement_candidates = []
    for _, _, base, name, _ in finalists:
        for saving, qcr_increase in itertools.product(
            (0.00, 0.02, 0.05), (0.10, 0.25, 0.50)
        ):
            candidate = {
                **base,
                "E10_MIN_EFFECTIVE_SAVING_RATIO": saving,
                "E10_QCR_ALLOWED_INCREASE": qcr_increase,
            }
            refinement_candidates.append(
                (f"R_{name}_{saving:.2f}_{qcr_increase:.2f}", candidate)
            )

    refined, run_index = _run_campaign(refinement_candidates, run_index, completed)
    all_results = results + refined
    if not all_results:
        print("No hubo corridas nuevas; revise Output/Tuning/optimization_history.jsonl.")
        return

    best_att, best_teus, best_config, best_name, best_run = min(all_results, key=lambda result: result[0])
    archive_improvement(
        best_config,
        best_att,
        best_teus,
        best_run,
        TUNING_DIR / f"{best_run:03d}_{best_name}" / "Output",
    )
    print(f"\nMejor resultado de esta campaña: {best_name} = {best_att:.4f} días")


if __name__ == "__main__":
    main()
