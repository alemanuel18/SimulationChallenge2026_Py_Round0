"""Auto-Tuner for WSC 2026 Maritime Simulation Challenge.

Automates hyperparameter search over simulation response strategies.
Runs in a continuous loop, testing parameter combinations and saving
outputs/logs ONLY when a new record (lower ATT) is achieved.
"""

import datetime as dt
import json
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


def find_att_csv() -> Path | None:
    """Find ATT_By_Statistics_Interval.csv in Output or its subdirectories."""
    direct = OUTPUT_DIR / "ATT_By_Statistics_Interval.csv"
    if direct.exists():
        return direct
    matches = list(OUTPUT_DIR.glob("**/ATT_By_Statistics_Interval.csv"))
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


def run_simulation(config: dict) -> tuple[float, float, float]:
    """Run main.py in a subprocess with the given configuration environment variables."""
    env = os.environ.copy()
    env["SIM_EXPERIMENT"] = str(config.get("EXPERIMENT", "CUSTOM"))
    env["SIM_MIN_REROUTE_SAVING_HOURS"] = str(config.get("MIN_REROUTE_SAVING_HOURS", 24.0))
    env["SIM_ENABLE_INITIAL_WAIT"] = str(config.get("ENABLE_INITIAL_WAIT", False))
    env["SIM_ENABLE_TRANSFER_COST"] = str(config.get("ENABLE_TRANSFER_COST", False))
    env["SIM_ENABLE_DYNAMIC_REROUTING"] = str(config.get("SIM_ENABLE_DYNAMIC_REROUTING", False))
    env["SIM_INITIAL_WAIT_WEIGHT"] = str(config.get("INITIAL_WAIT_WEIGHT", 1.0))
    env["SIM_TRANSFER_WAIT_WEIGHT"] = str(config.get("TRANSFER_WAIT_WEIGHT", 1.0))
    env["SIM_ENABLE_ALTERNATIVE_ROUTES"] = str(config.get("ENABLE_ALTERNATIVE_ROUTES", False))

    start_time = time.time()
    cmd = [sys.executable, str(PROJECT_ROOT / "main.py")]

    proc = subprocess.run(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    elapsed_sec = time.time() - start_time
    csv_file = find_att_csv()
    mean_att, completed_teus = parse_att_csv(csv_file)

    return mean_att, completed_teus, elapsed_sec


def archive_improvement(config: dict, mean_att: float, teus: float, run_index: int):
    """Archive output CSVs and log file ONLY when a new record is achieved."""
    MEJORAS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MEJORAS_LOGS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = f"{dt.datetime.now():%Y%m%d_%H%M%S}"
    run_folder_name = f"Mejora_Run{run_index:03d}_ATT_{mean_att:.2f}dias_{timestamp}"
    dest_output = MEJORAS_OUTPUT_DIR / run_folder_name
    dest_output.mkdir(parents=True, exist_ok=True)

    # Copy CSV outputs
    if OUTPUT_DIR.exists():
        for csv_path in OUTPUT_DIR.glob("*.csv"):
            shutil.copy2(csv_path, dest_output / csv_path.name)

    # Copy latest log file
    log_files = sorted(LOGS_DIR.glob("*.log"), key=os.path.getmtime, reverse=True)
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


def main():
    print("=" * 70)
    print("      AUTOMATED HYPERPARAMETER TUNER FOR WSC 2026 SIMULATION")
    print("=" * 70)
    print("Modo de ejecución: BUCLE CONTINUO (se detiene con Ctrl+C)")
    print("Filtro de guardado: SOLO SE ARCHIVAN MEJORAS DE TIEMPO (ATT)")
    print("=" * 70)

    # Define initial baseline configuration
    best_config = {
        "EXPERIMENT": "E7",
        "MIN_REROUTE_SAVING_HOURS": 24.0,
        "ENABLE_INITIAL_WAIT": False,
        "ENABLE_TRANSFER_COST": False,
        "ENABLE_DYNAMIC_REROUTING": False,
        "INITIAL_WAIT_WEIGHT": 1.0,
        "TRANSFER_WAIT_WEIGHT": 1.0,
        "ENABLE_ALTERNATIVE_ROUTES": False,
    }

    print("\n[Pass 0] Evaluando configuración Baseline...")
    best_att, best_teus, elapsed = run_simulation(best_config)
    print(f"--> Baseline ATT: {best_att:.4f} días | Completed TEUs: {best_teus:,.0f} | Tiempo: {elapsed/60:.2f} min")

    if best_att != float("inf"):
        archive_improvement(best_config, best_att, best_teus, 0)

    run_count = 0
    cycle_count = 1

    # Candidate spaces for variables
    param_spaces = {
        "MIN_REROUTE_SAVING_HOURS": [0.0, 6.0, 12.0, 24.0, 48.0, 72.0],
        "INITIAL_WAIT_WEIGHT": [0.0, 0.25, 0.5, 0.75, 1.0, 1.5],
        "TRANSFER_WAIT_WEIGHT": [0.0, 0.25, 0.5, 0.75, 1.0, 1.5],
        "ENABLE_DYNAMIC_REROUTING": [False, True],
        "ENABLE_INITIAL_WAIT": [False, True],
        "ENABLE_TRANSFER_COST": [False, True],
        "ENABLE_ALTERNATIVE_ROUTES": [False, True],
    }

    try:
        while True:
            print(f"\n============================================================")
            print(f" INICIANDO CICLO DE BÚSQUEDA #{cycle_count} | RÉCORD ACTUAL ATT: {best_att:.4f} días")
            print(f"============================================================")
            cycle_improved = False

            # Test each variable independently
            top_candidates_per_var = {}

            for var_name, values in param_spaces.items():
                print(f"\n--- Explorando Variable: {var_name} ---")
                var_results = []

                for val in values:
                    run_count += 1
                    test_config = dict(best_config)
                    test_config["EXPERIMENT"] = "CUSTOM"
                    test_config[var_name] = val

                    # Validation check: ENABLE_TRANSFER_COST and ENABLE_DYNAMIC_REROUTING dependencies
                    if test_config["ENABLE_TRANSFER_COST"] and not test_config["ENABLE_INITIAL_WAIT"]:
                        test_config["ENABLE_INITIAL_WAIT"] = True
                    if test_config["ENABLE_DYNAMIC_REROUTING"]:
                        test_config["ENABLE_INITIAL_WAIT"] = True
                        test_config["ENABLE_TRANSFER_COST"] = True

                    print(f"\n[Run #{run_count}] Probando {var_name} = {val} ...")
                    att, teus, duration = run_simulation(test_config)
                    print(f"   Resultado ATT: {att:.4f} días | TEUs: {teus:,.0f} | Duración: {duration/60:.2f} min")

                    var_results.append((att, val, test_config))

                    if att < best_att:
                        print(f"   ¡NUEVO RÉCORD DETECTADO! ATT mejoró de {best_att:.4f} a {att:.4f} días.")
                        best_att = att
                        best_teus = teus
                        best_config = test_config
                        cycle_improved = True
                        archive_improvement(best_config, best_att, best_teus, run_count)

                # Keep top 2 values for this variable
                var_results.sort(key=lambda x: x[0])
                top_candidates_per_var[var_name] = [item[1] for item in var_results[:2]]

            # Combinatorial pass of top candidates
            print(f"\n--- Evaluando Combinaciones de Top Candidatos ---")
            top_min_reroute = top_candidates_per_var.get("MIN_REROUTE_SAVING_HOURS", [24.0])[0]
            top_init_weight = top_candidates_per_var.get("INITIAL_WAIT_WEIGHT", [1.0])[0]
            top_trans_weight = top_candidates_per_var.get("TRANSFER_WAIT_WEIGHT", [1.0])[0]

            comb_config = dict(best_config)
            comb_config["EXPERIMENT"] = "CUSTOM"
            comb_config["MIN_REROUTE_SAVING_HOURS"] = top_min_reroute
            comb_config["INITIAL_WAIT_WEIGHT"] = top_init_weight
            comb_config["TRANSFER_WAIT_WEIGHT"] = top_trans_weight

            run_count += 1
            print(f"\n[Run #{run_count}] Probando Combinación Top: {comb_config} ...")
            att, teus, duration = run_simulation(comb_config)
            print(f"   Resultado ATT: {att:.4f} días | TEUs: {teus:,.0f} | Duración: {duration/60:.2f} min")

            if att < best_att:
                print(f"   ¡NUEVO RÉCORD COMBINATORIO! ATT mejoró a {att:.4f} días.")
                best_att = att
                best_teus = teus
                best_config = comb_config
                cycle_improved = True
                archive_improvement(best_config, best_att, best_teus, run_count)

            if not cycle_improved:
                print("\n[Aviso] Este ciclo no encontró un récord directo.")
                print("Refinando rangos y aplicando pequeñas perturbaciones para el siguiente ciclo...")
                # Refine continuous spaces around best current values
                cur_reroute = float(best_config.get("MIN_REROUTE_SAVING_HOURS", 24.0))
                param_spaces["MIN_REROUTE_SAVING_HOURS"] = sorted(list({
                    max(0.0, cur_reroute - 12.0),
                    max(0.0, cur_reroute - 6.0),
                    cur_reroute,
                    cur_reroute + 6.0,
                    cur_reroute + 12.0,
                }))

                cur_init_w = float(best_config.get("INITIAL_WAIT_WEIGHT", 1.0))
                param_spaces["INITIAL_WAIT_WEIGHT"] = sorted(list({
                    max(0.0, cur_init_w - 0.25),
                    max(0.0, cur_init_w - 0.1),
                    cur_init_w,
                    cur_init_w + 0.1,
                    cur_init_w + 0.25,
                }))

            cycle_count += 1

    except KeyboardInterrupt:
        print("\n\n============================================================")
        print(" Optimización detenida manualmente por el usuario.")
        print(f" Mejor ATT alcanzado: {best_att:.4f} días")
        print(f" Configuración ganadora guardada en: Output/Mejoras/best_configuration.json")
        print("============================================================")


if __name__ == "__main__":
    main()
